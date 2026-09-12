"""
gui/views/airplanes_view.py — Airplanes mode: launch + dump1090 control.

Adds a Start/Stop dump1090 control on top of the generic launch panel,
calling the real core.modules.airplanes.Dump1090Controller directly so
the GUI can manage the feed even before the user opens the curses radar
view (and the curses view will simply see it already running).
"""

from __future__ import annotations

import sys

import customtkinter as ctk

from gui.themes.theme import ACCENT_GREEN, ACCENT_RED, Theme
from gui.views.base_view import CORE_DIR
from gui.views.launch_view import LaunchModeView

sys.path.insert(0, CORE_DIR)


class AirplanesView(LaunchModeView):
    mode_key = "airplanes"
    mode_label = "Airplanes"
    description = ("ADS-B 1090MHz aircraft tracking with a tactical radar "
                    "display. Receive-only — decodes the same publicly "
                    "broadcast signals used by FlightRadar24 and similar.")
    keybindings = [
        ("1", "Enter Airplanes mode from the main menu"),
        ("S", "Start / restart dump1090"),
        ("Q / Esc", "Return to menu"),
    ]

    def __init__(self, master, theme: Theme, console, **kwargs):
        self._controller = None
        super().__init__(master, theme, console, **kwargs)

    def _build_extra_controls(self, panel: ctk.CTkFrame) -> None:
        row = ctk.CTkFrame(panel, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(0, 16))

        self.status_label = ctk.CTkLabel(row, text="dump1090: unknown",
                                          font=ctk.CTkFont(size=12))
        self.status_label.pack(side="left", padx=(0, 12))

        self.start_btn = ctk.CTkButton(row, text="Start dump1090", width=140,
                                        command=self._start_dump1090)
        self.start_btn.pack(side="left", padx=(0, 6))

        self.stop_btn = ctk.CTkButton(row, text="Stop dump1090", width=140,
                                       fg_color="#8a3b3b", hover_color="#a34a4a",
                                       command=self._stop_dump1090)
        self.stop_btn.pack(side="left")

        self._refresh_status()

    def _refresh_status(self) -> None:
        self.run_async(self._check_running, self._on_status_checked)

    @staticmethod
    def _check_running():
        from modules import hardware
        return hardware.is_dump1090_running()

    def _on_status_checked(self, running, error) -> None:
        if error is not None:
            self.status_label.configure(text=f"dump1090: error ({error})", text_color=ACCENT_RED)
            return
        if running:
            self.status_label.configure(text="dump1090: ACTIVE", text_color=ACCENT_GREEN)
        else:
            self.status_label.configure(text="dump1090: not running", text_color=ACCENT_RED)

    def _start_dump1090(self) -> None:
        self.console.log("Starting dump1090...", "info")
        self.start_btn.configure(state="disabled")
        self.run_async(self._do_start, self._on_start_done)

    @staticmethod
    def _do_start():
        from modules.airplanes import Dump1090Controller
        controller = Dump1090Controller()
        ok = controller.ensure_running()
        return controller, ok

    def _on_start_done(self, result, error) -> None:
        self.start_btn.configure(state="normal")
        if error is not None:
            self.console.log(f"Failed to start dump1090: {error}", "error")
            return
        controller, ok = result
        self._controller = controller
        if ok:
            self.console.log(
                f"dump1090 is running"
                + (f" (launched {controller.launched_binary})" if controller.owns_process else " (already running)")
                + ".", "success",
            )
        else:
            self.console.log(f"Could not start dump1090: {controller.last_error}", "error")
        self._refresh_status()

    def _stop_dump1090(self) -> None:
        if self._controller is None:
            self.console.log("dump1090 was not started from this panel — nothing to stop here.", "warning")
            return
        self._controller.shutdown()
        self.console.log("dump1090 stopped.", "info")
        self._refresh_status()

    def shutdown(self) -> None:
        super().shutdown()
        if self._controller is not None:
            self._controller.shutdown()
