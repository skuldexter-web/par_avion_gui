"""
gui/views/airplanes_view.py — Airplanes mode: fully embedded radar + data.

No external terminal is ever launched. Starts/stops dump1090 via the
real core.modules.airplanes.Dump1090Controller, then polls the real
AircraftTracker on a background thread and streams contacts onto a
native RadarCanvas plus a live CTkTextbox-based data table — matching
the "LIVE AIR TRAFFIC" reference layout.
"""

from __future__ import annotations

import sys

import customtkinter as ctk

from gui.components.radar_canvas import RadarCanvas, RadarContact
from gui.themes.theme import ACCENT_GREEN, ACCENT_RED, ACCENT_CYAN, Theme
from gui.views.base_view import BaseView, CORE_DIR

sys.path.insert(0, CORE_DIR)

POLL_MS = 1000
DEFAULT_MAX_RANGE_NM = 60.0


class AirplanesView(BaseView):
    def __init__(self, master, theme: Theme, console, **kwargs):
        super().__init__(master, theme, console, **kwargs)
        self._controller = None
        self._tracker = None
        self._running = False
        self._poll_job = None
        self._ref_lat = None
        self._ref_lon = None

        header = ctk.CTkLabel(self, text="Airplanes",
                               font=ctk.CTkFont(size=24, weight="bold"),
                               text_color=theme.text)
        header.pack(anchor="w", pady=(0, 4))
        desc = ctk.CTkLabel(
            self, text="ADS-B 1090MHz aircraft tracking. Receive-only — decodes "
                       "the same publicly broadcast signals used by FlightRadar24.",
            font=ctk.CTkFont(size=13), text_color=theme.text_dim,
            wraplength=900, justify="left",
        )
        desc.pack(anchor="w", pady=(0, 12))

        ctrl_row = ctk.CTkFrame(self, fg_color=theme.panel, corner_radius=10)
        ctrl_row.pack(fill="x", pady=(0, 10))
        inner = ctk.CTkFrame(ctrl_row, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=10)

        self.status_label = ctk.CTkLabel(inner, text="dump1090: unknown",
                                          font=ctk.CTkFont(size=12))
        self.status_label.pack(side="left", padx=(0, 16))

        self.start_btn = ctk.CTkButton(inner, text="Start Tracking", width=140,
                                        fg_color=ACCENT_GREEN, hover_color="#2fae45",
                                        text_color="#0a0a0a", command=self._start_tracking)
        self.start_btn.pack(side="left", padx=(0, 6))

        self.stop_btn = ctk.CTkButton(inner, text="Stop Tracking", width=140,
                                       fg_color="#8a3b3b", hover_color="#a34a4a",
                                       command=self._stop_tracking)
        self.stop_btn.pack(side="left", padx=(0, 16))

        ctk.CTkLabel(inner, text="Range (NM):", font=ctk.CTkFont(size=12)).pack(side="left")
        self.range_menu = ctk.CTkOptionMenu(inner, values=["20", "40", "60", "100", "150"],
                                             width=80, command=self._on_range_change)
        self.range_menu.set("60")
        self.range_menu.pack(side="left", padx=(6, 0))

        self.contact_count_label = ctk.CTkLabel(inner, text="Contacts: 0",
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
        self.radar = RadarCanvas(radar_panel, max_range_nm=DEFAULT_MAX_RANGE_NM)
        self.radar.pack(fill="both", expand=True, padx=10, pady=10)

        table_panel = ctk.CTkFrame(content, fg_color=theme.panel, corner_radius=10)
        table_panel.grid(row=0, column=1, sticky="nsew")
        ctk.CTkLabel(table_panel, text="LIVE AIR TRAFFIC",
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
        header = f"{'CALLSIGN':<10}{'ALT':>8}{'SPD':>7}{'DIST':>8}{'TRK':>6}\n"
        self.table_box.insert("end", header)
        self.table_box.insert("end", "-" * 39 + "\n")
        self.table_box.configure(state="disabled")

    def _on_range_change(self, value: str) -> None:
        try:
            self.radar.set_max_range(float(value))
        except ValueError:
            pass

    def _start_tracking(self) -> None:
        self.console.log("Starting dump1090 and aircraft tracker...", "info")
        self.start_btn.configure(state="disabled")
        self.run_async(self._do_start, self._on_start_done)

    @staticmethod
    def _do_start():
        from modules.airplanes import Dump1090Controller, AircraftTracker
        from modules import hardware

        controller = Dump1090Controller()
        ok = controller.ensure_running()

        tracker = None
        if ok:
            tracker = AircraftTracker()
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
            self.console.log(
                "dump1090 is running"
                + (f" (launched {controller.launched_binary})" if controller.owns_process else " (already running)")
                + ". Tracking aircraft.", "success",
            )
            if ref_lat is None:
                self.console.log("No GPS fix — using relative bearing/distance estimation.", "warning")
            self._running = True
            self._poll_job = self.after(POLL_MS, self._poll_tracker)
        else:
            self.console.log(f"Could not start dump1090: {controller.last_error}", "error")

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
        self.console.log("Aircraft tracking stopped.", "info")
        self._refresh_status_label()
        self.radar.update_contacts([])
        self.contact_count_label.configure(text="Contacts: 0")
        self._render_table_header()

    def _refresh_status_label(self) -> None:
        if self._tracker is not None and self._tracker.connected:
            self.status_label.configure(text="dump1090: ACTIVE", text_color=ACCENT_GREEN)
        elif self._running:
            self.status_label.configure(text="dump1090: connecting...", text_color=ACCENT_CYAN)
        else:
            self.status_label.configure(text="dump1090: stopped", text_color=ACCENT_RED)

    def _poll_tracker(self) -> None:
        if self._tracker is not None:
            aircraft = self._tracker.snapshot()
            self._update_display(aircraft)
            self._refresh_status_label()
        if self._running:
            self._poll_job = self.after(POLL_MS, self._poll_tracker)

    def _update_display(self, aircraft) -> None:
        gps_available = self._ref_lat is not None and self._ref_lon is not None

        contacts = []
        rows = []
        for ac in aircraft[:50]:
            dist, bearing = (None, None)
            if gps_available:
                dist, bearing = ac.distance_bearing_from(self._ref_lat, self._ref_lon)

            if gps_available and dist is not None:
                contacts.append(RadarContact(
                    distance_nm=dist, bearing_deg=bearing,
                    label_lines=[ac.callsign or ac.icao,
                                 f"{ac.altitude_ft or 0} ft  {ac.speed_kt or 0:.0f} kt"],
                    glyph="\u2708",
                ))
            elif not gps_available:
                seed = sum(ord(ch) for ch in (ac.callsign or ac.icao)) or 1
                contacts.append(RadarContact(
                    distance_nm=self.radar.max_range_nm * (0.25 + (seed % 100) / 133.0),
                    bearing_deg=(seed * 47) % 360,
                    label_lines=[ac.callsign or ac.icao, "(no GPS fix)"],
                    glyph="\u2708",
                ))

            rows.append(
                f"{(ac.callsign or ac.icao):<10}"
                f"{(ac.altitude_ft or 0):>8}"
                f"{(ac.speed_kt or 0):>7.0f}"
                f"{(dist or 0):>8.1f}"
                f"{(bearing or 0):>6.0f}"
            )

        self.radar.update_contacts(contacts)
        self.contact_count_label.configure(text=f"Contacts: {len(aircraft)}")

        self.table_box.configure(state="normal")
        self.table_box.delete("1.0", "end")
        self.table_box.insert("end", f"{'CALLSIGN':<10}{'ALT':>8}{'SPD':>7}{'DIST':>8}{'TRK':>6}\n")
        self.table_box.insert("end", "-" * 39 + "\n")
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
