"""
gui/views/iss_view.py — ISS tracker: live position, ground track and passes.

Propagation runs locally from a two-line element set (see
core/modules/iss.py), so this works with no network connection and no
API key. The panel is explicit about which elements it is using and how
old they are, because that — not the arithmetic — is what limits pass
accuracy.

Three ways to get elements, in order of convenience:
  * Refresh elements — one click, needs the network, no account or key.
  * The bundled/cached set — used automatically, always available.
  * Manual TLE — paste two lines from any source, for a machine that
    never touches a network.
"""

from __future__ import annotations

import sys

import customtkinter as ctk

from gui.components.map_canvas import MapCanvas, MapContact
from gui.themes.theme import ACCENT_AMBER, ACCENT_CYAN, ACCENT_RED, Theme
from gui.views.base_view import BaseView, CORE_DIR

sys.path.insert(0, CORE_DIR)

TICK_MS = 1000
PASS_REFRESH_S = 300
ISS_COLOR = "#ffcc33"


class IssView(BaseView):
    def __init__(self, master, theme: Theme, console, **kwargs):
        super().__init__(master, theme, console, **kwargs)
        self._tracker = None
        self._tick_job = None
        self._last_pass_calc = 0.0
        self._passes = []
        self._ref_lat = None
        self._ref_lon = None
        self._ref_source = "none"
        self._build_ui()
        self.after(200, self._bootstrap)

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        theme = self.theme

        ctk.CTkLabel(self, text="ISS Tracker",
                     font=ctk.CTkFont(size=24, weight="bold"),
                     text_color=theme.text).pack(anchor="w", pady=(0, 2))
        ctk.CTkLabel(
            self,
            text="Live orbit propagation from published two-line elements. "
                 "Runs entirely offline — no API key, no account, no ephemeris "
                 "download.",
            font=ctk.CTkFont(size=12), text_color=theme.text_dim,
            wraplength=1000, justify="left").pack(anchor="w", pady=(0, 10))

        ctrl = ctk.CTkFrame(self, fg_color=theme.panel, corner_radius=10)
        ctrl.pack(fill="x", pady=(0, 8))
        inner = ctk.CTkFrame(ctrl, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=10)

        self.refresh_btn = ctk.CTkButton(inner, text="Refresh elements", width=150,
                                         command=self._refresh_elements)
        self.refresh_btn.pack(side="left", padx=(0, 8))
        self.manual_btn = ctk.CTkButton(inner, text="Manual TLE", width=110,
                                        fg_color="#2b3a46", hover_color="#364857",
                                        command=self._toggle_manual)
        self.manual_btn.pack(side="left", padx=(0, 16))

        self.elements_label = ctk.CTkLabel(inner, text="Loading elements...",
                                           font=ctk.CTkFont(size=12),
                                           text_color=theme.text_dim)
        self.elements_label.pack(side="left")

        self.position_label = ctk.CTkLabel(inner, text="", font=ctk.CTkFont(size=11),
                                           text_color=ACCENT_AMBER)
        self.position_label.pack(side="right")

        # Collapsible manual-TLE entry.
        self.manual_frame = ctk.CTkFrame(self, fg_color=theme.panel, corner_radius=10)
        ctk.CTkLabel(self.manual_frame,
                     text="Paste a two-line element set (the name line is optional):",
                     font=ctk.CTkFont(size=12), text_color=theme.text
                     ).pack(anchor="w", padx=14, pady=(10, 4))
        self.manual_box = ctk.CTkTextbox(self.manual_frame, height=74,
                                         font=ctk.CTkFont(family="Consolas", size=11))
        self.manual_box.pack(fill="x", padx=14)
        row = ctk.CTkFrame(self.manual_frame, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=8)
        ctk.CTkButton(row, text="Use these elements", width=160,
                      command=self._apply_manual).pack(side="left")
        ctk.CTkLabel(row,
                     text="Sources needing no account: celestrak.org, "
                          "live.ariss.org/iss.txt, amsat.org/tle/current/nasabare.txt",
                     font=ctk.CTkFont(size=10), text_color=theme.text_dim
                     ).pack(side="left", padx=(12, 0))
        self._manual_open = False

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)

        map_panel = ctk.CTkFrame(body, fg_color=theme.panel, corner_radius=10)
        map_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        header = ctk.CTkFrame(map_panel, fg_color="transparent")
        header.pack(fill="x", padx=14, pady=(10, 4))
        ctk.CTkLabel(header, text="GROUND TRACK",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=theme.text).pack(side="left")
        ctk.CTkLabel(header, text="offline vector map — no tiles, no API key",
                     font=ctk.CTkFont(size=11),
                     text_color=theme.text_dim).pack(side="left", padx=(10, 0))
        self.map = MapCanvas(map_panel, world=True, show_rings=False)
        self.map.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        side = ctk.CTkFrame(body, fg_color="transparent")
        side.grid(row=0, column=1, sticky="nsew")

        tel = ctk.CTkFrame(side, fg_color=theme.panel, corner_radius=10)
        tel.pack(fill="x")
        ctk.CTkLabel(tel, text="ORBITAL TELEMETRY",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=theme.text).pack(anchor="w", padx=14, pady=(12, 6))
        self.telemetry_label = ctk.CTkLabel(
            tel, text="Waiting for elements...", justify="left", anchor="w",
            font=ctk.CTkFont(family="Consolas", size=12), text_color=ACCENT_CYAN)
        self.telemetry_label.pack(fill="x", padx=14, pady=(0, 12))

        passes = ctk.CTkFrame(side, fg_color=theme.panel, corner_radius=10)
        passes.pack(fill="both", expand=True, pady=(10, 0))
        ctk.CTkLabel(passes, text="NEXT PASSES",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=theme.text).pack(anchor="w", padx=14, pady=(12, 6))
        self.passes_box = ctk.CTkTextbox(passes, fg_color=theme.console_bg,
                                         font=ctk.CTkFont(family="Consolas", size=11))
        self.passes_box.pack(fill="both", expand=True, padx=14, pady=(0, 6))
        self.passes_box.configure(state="disabled")
        self.quality_label = ctk.CTkLabel(passes, text="", justify="left", anchor="w",
                                          font=ctk.CTkFont(size=11),
                                          text_color=ACCENT_AMBER, wraplength=360)
        self.quality_label.pack(fill="x", padx=14, pady=(0, 12))

    # ------------------------------------------------------------------
    def _toggle_manual(self) -> None:
        if self._manual_open:
            self.manual_frame.pack_forget()
        else:
            self.manual_frame.pack(fill="x", pady=(0, 8), after=self.winfo_children()[2])
        self._manual_open = not self._manual_open

    def _bootstrap(self) -> None:
        self.run_async(self._load_tracker, self._on_tracker_ready)

    @staticmethod
    def _load_tracker():
        from modules import appconfig
        from modules.iss import ISSTracker
        lat, lon, source = appconfig.reference_location()
        tracker = ISSTracker(observer_lat=lat, observer_lon=lon)
        return tracker, lat, lon, source

    def _on_tracker_ready(self, result, error) -> None:
        if error is not None:
            self.console.log(f"ISS tracker failed to start: {error}", "error")
            self.elements_label.configure(text=str(error), text_color=ACCENT_RED)
            return
        self._tracker, self._ref_lat, self._ref_lon, self._ref_source = result

        from modules import appconfig
        self.position_label.configure(
            text="observer: " + appconfig.describe_location(
                self._ref_lat, self._ref_lon, self._ref_source))
        self.map.set_home(self._ref_lat, self._ref_lon, self._ref_source)

        if not self._tracker.ready:
            msg = self._tracker.last_error or "no orbital elements available"
            self.elements_label.configure(text=msg, text_color=ACCENT_RED)
            self.console.log(f"ISS: {msg}", "error")
            return

        self._update_elements_label()
        self.console.log(f"ISS tracker ready — using {self._tracker.element_source_text()}.",
                         "success")
        self._tick()

    def _update_elements_label(self) -> None:
        if self._tracker is None or self._tracker.tle is None:
            return
        self.elements_label.configure(
            text=f"Elements: {self._tracker.element_source_text()}",
            text_color=self.theme.text_dim)
        self.quality_label.configure(text=self._tracker.tle.quality_note())

    # ------------------------------------------------------------------
    def _refresh_elements(self) -> None:
        if self._tracker is None:
            return
        self.refresh_btn.configure(state="disabled", text="Refreshing...")
        self.console.log("Fetching fresh ISS elements (no API key needed)...", "info")
        self.run_async(lambda: self._tracker.refresh_tle(force=True),
                       self._on_refreshed)

    def _on_refreshed(self, result, error) -> None:
        self.refresh_btn.configure(state="normal", text="Refresh elements")
        if error is not None:
            self.console.log(f"Element refresh failed: {error}", "error")
            return
        changed, message = result
        self.console.log(message, "success" if changed else "warning")
        self._update_elements_label()
        self._last_pass_calc = 0.0

    def _apply_manual(self) -> None:
        if self._tracker is None:
            return
        text = self.manual_box.get("1.0", "end")
        ok, message = self._tracker.set_manual_tle(text)
        self.console.log(message, "success" if ok else "error")
        if ok:
            self._update_elements_label()
            self._last_pass_calc = 0.0

    # ------------------------------------------------------------------
    def _tick(self) -> None:
        if self._tracker is not None and self._tracker.ready:
            try:
                self._update_live()
            except Exception as e:
                self.console.log(f"ISS update error: {e}", "warning")
        self._tick_job = self.after(TICK_MS, self._tick)

    def _update_live(self) -> None:
        from modules import mapdata
        import time as _time

        sp = self._tracker.subpoint()
        if sp is None:
            return

        lines = [
            f"Latitude   {sp.lat:+8.3f}",
            f"Longitude  {sp.lon:+8.3f}",
            f"Altitude   {sp.alt_km:8.1f} km",
            f"Speed      {sp.velocity_kms:8.3f} km/s",
            f"Sunlight   {'sunlit' if sp.sunlit else 'in eclipse':>8}",
        ]
        look = self._tracker.look_angles()
        if look is not None:
            az, el, rng = look
            lines.append(f"Azimuth    {az:8.1f}°")
            lines.append(f"Elevation  {el:8.1f}°"
                         + ("   OVERHEAD NOW" if el > 0 else ""))
            lines.append(f"Range      {rng:8.0f} km")
        self.telemetry_label.configure(text="\n".join(lines))

        track = self._tracker.ground_track(45, 95, 60)
        self.map.set_tracks([(track, ACCENT_CYAN)])
        self.map.set_markers([MapContact(
            lat=sp.lat, lon=sp.lon, label="ISS", glyph="+", color=ISS_COLOR,
            sublabel=f"{sp.alt_km:.0f} km")])
        self.map.set_circles([(sp.lat, sp.lon,
                               mapdata.horizon_radius_nm(sp.alt_km), "#6a5a2a")])

        now = _time.time()
        if self._ref_lat is not None and now - self._last_pass_calc > PASS_REFRESH_S:
            self._last_pass_calc = now
            self.run_async(lambda: self._tracker.next_passes(count=5),
                           self._on_passes)
        elif self._ref_lat is None:
            self._render_passes(None)

    def _on_passes(self, result, error) -> None:
        if error is not None:
            self.console.log(f"Pass prediction failed: {error}", "warning")
            return
        self._passes = result or []
        self._render_passes(self._passes)

    def _render_passes(self, passes) -> None:
        self.passes_box.configure(state="normal")
        self.passes_box.delete("1.0", "end")
        if passes is None:
            self.passes_box.insert(
                "end",
                "No observer position.\n\nPass times depend entirely on where you\n"
                "are, so none are shown rather than times for\nsomewhere you aren't.\n\n"
                "Set a home location in Settings, or connect\na GPS receiver.\n")
        elif not passes:
            self.passes_box.insert("end", "\n  No passes above 10° in the next 48 hours.\n")
        else:
            self.passes_box.insert("end", f"{'WHEN (UTC)':<18}{'MAX EL':>7}{'DUR':>8}  VIS\n")
            self.passes_box.insert("end", "-" * 42 + "\n")
            for p in passes:
                when = p.rise_time.strftime("%d %b %H:%M:%S") if p.rise_time else "--"
                self.passes_box.insert(
                    "end",
                    f"{when:<18}{p.max_elevation_deg:>6.0f}°{p.duration_text():>8}"
                    f"  {'yes' if p.visible else 'no'}\n")
                if p.rise_azimuth_deg is not None and p.set_azimuth_deg is not None:
                    self.passes_box.insert(
                        "end",
                        f"   rises {self._compass(p.rise_azimuth_deg)} "
                        f"→ sets {self._compass(p.set_azimuth_deg)}\n")
            self.passes_box.insert(
                "end", "\n'VIS' = sunlit station, dark sky: visible to the eye.\n")
        self.passes_box.configure(state="disabled")

    @staticmethod
    def _compass(deg: float) -> str:
        points = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                  "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
        return points[int((deg % 360) / 22.5 + 0.5) % 16]

    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        super().shutdown()
        if self._tick_job is not None:
            try:
                self.after_cancel(self._tick_job)
            except Exception:
                pass
            self._tick_job = None
