"""
gui/views/dashboard.py — Landing page: hardware status + quick launch.

Calls the real core.modules.hardware.full_report() on a background
thread (it shells out to lsusb/gpsd/etc., which can take a moment) and
displays SDR/GPS/dump1090 status as GUI cards, plus a quick-launch grid
for every mode.
"""

from __future__ import annotations

import sys

import customtkinter as ctk

from gui.themes.theme import ACCENT_GREEN, ACCENT_RED, ACCENT_AMBER, Theme
from gui.views.base_view import BaseView, CORE_DIR

sys.path.insert(0, CORE_DIR)

MODE_CARDS = [
    ("airplanes", "Airplanes", "ADS-B 1090MHz + Tactical Radar"),
    ("waterfalls", "Waterfalls", "Spectrum Analyzer & Waterfall"),
    ("radio", "Radio", "Broadcast AM/FM Tuner"),
    ("maritime", "Maritime", "AIS Vessel Tracking"),
    ("iss", "ISS Tracker", "Orbit & Pass Predictor"),
    ("sstv", "SSTV", "Slow Scan TV Decoder"),
    ("morse", "Morse / CW", "Audio Code Decoder"),
]


class DashboardView(BaseView):
    def __init__(self, master, theme: Theme, console, **kwargs):
        super().__init__(master, theme, console, **kwargs)
        self._build_ui()

    def _build_ui(self) -> None:
        theme = self.theme
        # Everything lives inside a scrollable frame so the dashboard
        # (status cards + 7 launch cards) stays fully reachable even on
        # shorter windows / smaller displays, instead of the bottom rows
        # being silently cut off with no way to reach them.
        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        # Header/sub-header live inside a plain CTkFrame nested in the
        # scrollable frame, not as direct children of it — labels placed
        # directly on a CTkScrollableFrame's own (canvas-backed) surface
        # were found not to repaint correctly after a theme switch even
        # though their text_color property was verifiably correct
        # underneath; nesting a normal frame sidesteps that redraw quirk,
        # matching how the HARDWARE STATUS panel (already frame-nested)
        # behaved correctly in the same rebuild.
        header_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        header_frame.pack(fill="x", anchor="w")
        header = ctk.CTkLabel(
            header_frame, text="Dashboard",
            font=ctk.CTkFont(size=24, weight="bold"), text_color=theme.text,
        )
        header.pack(anchor="w", pady=(0, 4))
        sub = ctk.CTkLabel(
            header_frame, text="Hardware status and quick launch",
            font=ctk.CTkFont(size=13), text_color=theme.text_dim,
        )
        sub.pack(anchor="w", pady=(0, 16))

        # --- Hardware status cards ---
        status_frame = ctk.CTkFrame(scroll, fg_color=theme.panel, corner_radius=10)
        status_frame.pack(fill="x", pady=(0, 16))

        status_header = ctk.CTkFrame(status_frame, fg_color="transparent")
        status_header.pack(fill="x", padx=16, pady=(12, 4))
        ctk.CTkLabel(status_header, text="HARDWARE STATUS", text_color=theme.text,
                     font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")
        self.refresh_btn = ctk.CTkButton(
            status_header, text="Refresh", width=90, height=26,
            command=self._refresh_hardware,
        )
        self.refresh_btn.pack(side="right")

        cards_row = ctk.CTkFrame(status_frame, fg_color="transparent")
        cards_row.pack(fill="x", padx=16, pady=(4, 16))
        cards_row.grid_columnconfigure((0, 1, 2), weight=1, uniform="cards")

        self.sdr_card = self._make_status_card(cards_row, "SDR Device", "Checking...")
        self.sdr_card["frame"].grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self.gps_card = self._make_status_card(cards_row, "GPS Fix", "Checking...")
        self.gps_card["frame"].grid(row=0, column=1, sticky="ew", padx=6)

        self.dump1090_card = self._make_status_card(cards_row, "dump1090", "Checking...")
        self.dump1090_card["frame"].grid(row=0, column=2, sticky="ew", padx=(6, 0))

        # --- Quick launch grid ---
        launch_label_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        launch_label_frame.pack(fill="x", anchor="w")
        launch_label = ctk.CTkLabel(
            launch_label_frame, text="QUICK LAUNCH",
            font=ctk.CTkFont(size=13, weight="bold"), text_color=theme.text,
        )
        launch_label.pack(anchor="w", pady=(4, 8))

        grid = ctk.CTkFrame(scroll, fg_color="transparent")
        grid.pack(fill="both", expand=True)
        for col in range(3):
            grid.grid_columnconfigure(col, weight=1, uniform="launch")

        for i, (key, label, desc) in enumerate(MODE_CARDS):
            row, col = divmod(i, 3)
            card = self._make_launch_card(grid, key, label, desc)
            card.grid(row=row, column=col, sticky="nsew", padx=6, pady=6)

        self._refresh_hardware()

    # ------------------------------------------------------------------
    def _make_status_card(self, parent, title: str, initial_value: str) -> dict:
        frame = ctk.CTkFrame(parent, fg_color=self.theme.bg, corner_radius=8)
        ctk.CTkLabel(frame, text=title, font=ctk.CTkFont(size=11),
                     text_color=self.theme.text_dim).pack(anchor="w", padx=12, pady=(8, 0))
        value_label = ctk.CTkLabel(frame, text=initial_value, text_color=self.theme.text,
                                    font=ctk.CTkFont(size=14, weight="bold"))
        value_label.pack(anchor="w", padx=12, pady=(0, 10))
        return {"frame": frame, "value_label": value_label}

    def _make_launch_card(self, parent, key: str, label: str, desc: str) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(parent, fg_color=self.theme.panel, corner_radius=10)
        ctk.CTkLabel(frame, text=label, font=ctk.CTkFont(size=15, weight="bold"),
                     text_color=self.theme.text
                     ).pack(anchor="w", padx=14, pady=(12, 2))
        ctk.CTkLabel(frame, text=desc, font=ctk.CTkFont(size=11),
                     text_color=self.theme.text_dim, wraplength=200, justify="left"
                     ).pack(anchor="w", padx=14, pady=(0, 10))
        btn = ctk.CTkButton(frame, text="Open", height=30,
                             command=lambda: self._navigate_to(key))
        btn.pack(fill="x", padx=14, pady=(0, 12))
        return frame

    def _navigate_to(self, key: str) -> None:
        """Switch to the given mode's tab in-app — every mode is now a
        fully embedded view, so 'launching' a mode from the dashboard
        just means showing its tab, never spawning an external terminal."""
        app = self.winfo_toplevel()
        show_fn = getattr(app, "_show_view", None)
        sidebar = getattr(app, "sidebar", None)
        if callable(show_fn):
            show_fn(key)
        if sidebar is not None:
            sidebar.set_active(key)

    # ------------------------------------------------------------------
    def _refresh_hardware(self) -> None:
        self.refresh_btn.configure(state="disabled", text="Checking...")
        self.console.log("Scanning hardware (SDR/GPS/dump1090)...", "info")
        self.run_async(self._fetch_hardware_report, self._on_hardware_report)

    @staticmethod
    def _fetch_hardware_report():
        from modules import hardware
        return hardware.full_report()

    def _on_hardware_report(self, report, error) -> None:
        self.refresh_btn.configure(state="normal", text="Refresh")
        if error is not None:
            self.console.log(f"Hardware scan failed: {error}", "error")
            self._set_card(self.sdr_card, "Error", ACCENT_RED)
            self._set_card(self.gps_card, "Error", ACCENT_RED)
            self._set_card(self.dump1090_card, "Error", ACCENT_RED)
            return

        if report.sdrs:
            self._set_card(self.sdr_card, f"{len(report.sdrs)} detected", ACCENT_GREEN)
            self.console.log(f"Found {len(report.sdrs)} SDR device(s).", "success")
        else:
            self._set_card(self.sdr_card, "None detected", ACCENT_RED)
            self.console.log("No SDR devices detected.", "warning")

        if report.gps and report.gps.fix:
            self._set_card(self.gps_card, f"{report.gps.lat:.3f}, {report.gps.lon:.3f}", ACCENT_GREEN)
            self.console.log("GPS fix acquired.", "success")
        else:
            self._set_card(self.gps_card, "No fix", ACCENT_AMBER)
            self.console.log("No GPS fix — radar modes will use relative estimation.", "warning")

        if report.dump1090_running:
            self._set_card(self.dump1090_card, "Running", ACCENT_GREEN)
        else:
            self._set_card(self.dump1090_card, "Not running", ACCENT_AMBER)

        for err in report.errors:
            self.console.log(err, "warning")

    def _set_card(self, card: dict, text: str, color: str) -> None:
        card["value_label"].configure(text=text, text_color=color)

    # ------------------------------------------------------------------
    def apply_theme(self, theme: Theme) -> None:
        self.theme = theme
        # Cards were built with the old theme's colors baked in; a full
        # rebuild is simplest and cheap enough for a dashboard-sized
        # widget count. Rebuilds by clearing children and re-running the
        # widget-construction method only — NEVER by calling
        # self.__init__() again: re-running CTkFrame.__init__ on an
        # already-placed widget silently clears its own grid placement
        # in its parent (grid_info() comes back empty afterward), which
        # left the whole view unmapped/invisible after a theme switch.
        for child in self.winfo_children():
            child.destroy()
        self._build_ui()
