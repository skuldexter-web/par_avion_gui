"""
gui/views/maritime_view.py — Maritime mode: fully embedded radar + data.

No external terminal is ever launched. Starts/stops rtl_ais via the real
core.modules.maritime.RtlAisController, then polls the real AISTracker
on a background thread and streams vessels onto a native RadarCanvas
plus a live data table — matching the "LIVE MARITIME TRAFFIC" reference
layout.
"""

from __future__ import annotations

import sys

import customtkinter as ctk

from gui.components.radar_canvas import RadarCanvas, RadarContact
from gui.themes.theme import ACCENT_BLUE, ACCENT_RED, ACCENT_CYAN, Theme
from gui.views.base_view import BaseView, CORE_DIR

sys.path.insert(0, CORE_DIR)

POLL_MS = 1000
DEFAULT_MAX_RANGE_NM = 12.0


class MaritimeView(BaseView):
    def __init__(self, master, theme: Theme, console, **kwargs):
        super().__init__(master, theme, console, **kwargs)
        self._controller = None
        self._tracker = None
        self._running = False
        self._poll_job = None
        self._ref_lat = None
        self._ref_lon = None

        header = ctk.CTkLabel(self, text="Maritime",
                               font=ctk.CTkFont(size=24, weight="bold"),
                               text_color=theme.text)
        header.pack(anchor="w", pady=(0, 4))
        desc = ctk.CTkLabel(
            self, text="AIS vessel tracking. Receive-only — decodes the same "
                       "publicly broadcast AIS signals used by MarineTraffic.",
            font=ctk.CTkFont(size=13), text_color=theme.text_dim,
            wraplength=900, justify="left",
        )
        desc.pack(anchor="w", pady=(0, 12))

        ctrl_row = ctk.CTkFrame(self, fg_color=theme.panel, corner_radius=10)
        ctrl_row.pack(fill="x", pady=(0, 10))
        inner = ctk.CTkFrame(ctrl_row, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=10)

        self.status_label = ctk.CTkLabel(inner, text="rtl_ais: unknown",
                                          font=ctk.CTkFont(size=12))
        self.status_label.pack(side="left", padx=(0, 16))

        self.start_btn = ctk.CTkButton(inner, text="Start Tracking", width=140,
                                        fg_color=ACCENT_BLUE, hover_color="#3a7fd4",
                                        text_color="#0a0a0a", command=self._start_tracking)
        self.start_btn.pack(side="left", padx=(0, 6))

        self.stop_btn = ctk.CTkButton(inner, text="Stop Tracking", width=140,
                                       fg_color="#8a3b3b", hover_color="#a34a4a",
                                       command=self._stop_tracking)
        self.stop_btn.pack(side="left", padx=(0, 16))

        ctk.CTkLabel(inner, text="Range (NM):", font=ctk.CTkFont(size=12)).pack(side="left")
        self.range_menu = ctk.CTkOptionMenu(inner, values=["5", "10", "12", "20", "40"],
                                             width=80, command=self._on_range_change)
        self.range_menu.set("12")
        self.range_menu.pack(side="left", padx=(6, 0))

        self.contact_count_label = ctk.CTkLabel(inner, text="Vessels: 0",
                                                 font=ctk.CTkFont(size=12),
                                                 text_color=ACCENT_CYAN)
        self.contact_count_label.pack(side="right")

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True)
        content.grid_columnconfigure(0, weight=3)
        content.grid_columnconfigure(1, weight=2)
        content.grid_rowconfigure(0, weight=1)

        radar_panel = ctk.CTkFrame(content, fg_color=theme.panel, corner_radius=10)
        radar_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.radar = RadarCanvas(radar_panel, max_range_nm=DEFAULT_MAX_RANGE_NM,
                                  color=ACCENT_BLUE)
        self.radar.pack(fill="both", expand=True, padx=10, pady=10)

        table_panel = ctk.CTkFrame(content, fg_color=theme.panel, corner_radius=10)
        table_panel.grid(row=0, column=1, sticky="nsew")
        ctk.CTkLabel(table_panel, text="LIVE MARITIME TRAFFIC",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=theme.text).pack(anchor="w", padx=14, pady=(12, 6))
        self.table_box = ctk.CTkTextbox(table_panel, fg_color=theme.console_bg,
                                         font=ctk.CTkFont(family="Consolas", size=12))
        self.table_box.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.table_box.configure(state="disabled")
        self._render_table_header()

    def _render_table_header(self) -> None:
        self.table_box.configure(state="normal")
        self.table_box.delete("1.0", "end")
        header = f"{'NAME':<16}{'SOG':>7}{'COG':>6}{'DIST':>8}\n"
        self.table_box.insert("end", header)
        self.table_box.insert("end", "-" * 37 + "\n")
        self.table_box.configure(state="disabled")

    def _on_range_change(self, value: str) -> None:
        try:
            self.radar.set_max_range(float(value))
        except ValueError:
            pass

    def _start_tracking(self) -> None:
        self.console.log("Starting rtl_ais and vessel tracker...", "info")
        self.start_btn.configure(state="disabled")
        self.run_async(self._do_start, self._on_start_done)

    @staticmethod
    def _do_start():
        from modules.maritime import RtlAisController, AISTracker
        from modules import hardware

        controller = RtlAisController()
        ok = controller.ensure_running()

        tracker = AISTracker()
        tracker.start()

        ref_lat, ref_lon = None, None
        gps = hardware.detect_gps()
        if gps and gps.fix:
            ref_lat, ref_lon = gps.lat, gps.lon

        return controller, tracker, ok, ref_lat, ref_lon

    def _on_start_done(self, result, error) -> None:
        self.start_btn.configure(state="normal")
        if error is not None:
            self.console.log(f"Failed to start tracking: {error}", "error")
            return
        controller, tracker, ok, ref_lat, ref_lon = result
        self._controller = controller
        self._tracker = tracker
        self._ref_lat = ref_lat
        self._ref_lon = ref_lon

        if ok:
            self.console.log("rtl_ais is running. Tracking vessels.", "success")
        else:
            self.console.log(f"rtl_ais unavailable: {controller.last_error}. "
                              "Listening for an externally-fed AIS stream on UDP:10110 anyway.",
                              "warning")
        if ref_lat is None:
            self.console.log("No GPS fix — using relative bearing/distance estimation.", "warning")

        self._running = True
        self._poll_job = self.after(POLL_MS, self._poll_tracker)
        self._refresh_status_label()

    def _stop_tracking(self) -> None:
        self._running = False
        if self._poll_job is not None:
            self.after_cancel(self._poll_job)
            self._poll_job = None
        if self._tracker is not None:
            self._tracker.stop()
        if self._controller is not None:
            self._controller.shutdown()
        self.console.log("Vessel tracking stopped.", "info")
        self._refresh_status_label()
        self.radar.update_contacts([])
        self.contact_count_label.configure(text="Vessels: 0")
        self._render_table_header()

    def _refresh_status_label(self) -> None:
        if self._tracker is not None and self._tracker.connected:
            self.status_label.configure(text="rtl_ais: ACTIVE", text_color=ACCENT_BLUE)
        elif self._running:
            self.status_label.configure(text="rtl_ais: listening (no feed yet)", text_color=ACCENT_CYAN)
        else:
            self.status_label.configure(text="rtl_ais: stopped", text_color=ACCENT_RED)

    def _poll_tracker(self) -> None:
        if self._tracker is not None:
            vessels = self._tracker.snapshot()
            self._update_display(vessels)
            self._refresh_status_label()
        if self._running:
            self._poll_job = self.after(POLL_MS, self._poll_tracker)

    def _update_display(self, vessels) -> None:
        gps_available = self._ref_lat is not None and self._ref_lon is not None

        contacts = []
        rows = []
        for v in vessels[:50]:
            dist, bearing = (None, None)
            if gps_available:
                dist, bearing = v.distance_bearing_from(self._ref_lat, self._ref_lon)

            display_name = (v.name or v.mmsi)[:15]

            if gps_available and dist is not None:
                contacts.append(RadarContact(
                    distance_nm=dist, bearing_deg=bearing,
                    label_lines=[display_name,
                                 f"{v.sog_kt or 0:.1f} kn  {v.cog_deg or 0:.0f}"],
                    glyph="\u25b2",
                ))
            elif not gps_available:
                seed = sum(ord(ch) for ch in display_name) or 1
                contacts.append(RadarContact(
                    distance_nm=self.radar.max_range_nm * (0.25 + (seed % 100) / 133.0),
                    bearing_deg=(seed * 47) % 360,
                    label_lines=[display_name, "(no GPS fix)"],
                    glyph="\u25b2",
                ))

            rows.append(
                f"{display_name:<16}"
                f"{(v.sog_kt or 0):>7.1f}"
                f"{(v.cog_deg or 0):>6.0f}"
                f"{(dist or 0):>8.1f}"
            )

        self.radar.update_contacts(contacts)
        self.contact_count_label.configure(text=f"Vessels: {len(vessels)}")

        self.table_box.configure(state="normal")
        self.table_box.delete("1.0", "end")
        self.table_box.insert("end", f"{'NAME':<16}{'SOG':>7}{'COG':>6}{'DIST':>8}\n")
        self.table_box.insert("end", "-" * 37 + "\n")
        for row in rows:
            self.table_box.insert("end", row + "\n")
        self.table_box.configure(state="disabled")

    def shutdown(self) -> None:
        super().shutdown()
        self._running = False
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except Exception:
                pass
        if self._tracker is not None:
            self._tracker.stop()
        if self._controller is not None:
            self._controller.shutdown()
        self.radar.stop_animation()
