"""
gui/views/maritime_view.py — Maritime mode: launch + rtl_ais control.
"""

from __future__ import annotations

import sys

import customtkinter as ctk

from gui.themes.theme import ACCENT_GREEN, ACCENT_RED, Theme
from gui.views.base_view import CORE_DIR
from gui.views.launch_view import LaunchModeView

sys.path.insert(0, CORE_DIR)


class MaritimeView(LaunchModeView):
    mode_key = "maritime"
    mode_label = "Maritime"
    description = ("AIS vessel tracking with a tactical marine radar "
                    "display. Receive-only — decodes the same publicly "
                    "broadcast AIS signals used by MarineTraffic and "
                    "similar services.")
    keybindings = [
        ("4", "Enter Maritime mode from the main menu"),
        ("S", "Restart rtl_ais"),
        ("Q / Esc", "Return to menu"),
    ]

    def __init__(self, master, theme: Theme, console, **kwargs):
        self._controller = None
        super().__init__(master, theme, console, **kwargs)

    def _build_extra_controls(self, panel: ctk.CTkFrame) -> None:
        row = ctk.CTkFrame(panel, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(0, 16))

        self.status_label = ctk.CTkLabel(row, text="rtl_ais: unknown",
                                          font=ctk.CTkFont(size=12))
        self.status_label.pack(side="left", padx=(0, 12))

        self.start_btn = ctk.CTkButton(row, text="Start rtl_ais", width=140,
                                        command=self._start_rtl_ais)
        self.start_btn.pack(side="left", padx=(0, 6))

        self.stop_btn = ctk.CTkButton(row, text="Stop rtl_ais", width=140,
                                       fg_color="#8a3b3b", hover_color="#a34a4a",
                                       command=self._stop_rtl_ais)
        self.stop_btn.pack(side="left")

    def _start_rtl_ais(self) -> None:
        self.console.log("Starting rtl_ais...", "info")
        self.start_btn.configure(state="disabled")
        self.run_async(self._do_start, self._on_start_done)

    @staticmethod
    def _do_start():
        from modules.maritime import RtlAisController
        controller = RtlAisController()
        ok = controller.ensure_running()
        return controller, ok

    def _on_start_done(self, result, error) -> None:
        self.start_btn.configure(state="normal")
        if error is not None:
            self.console.log(f"Failed to start rtl_ais: {error}", "error")
            self.status_label.configure(text="rtl_ais: error", text_color=ACCENT_RED)
            return
        controller, ok = result
        self._controller = controller
        if ok:
            self.console.log("rtl_ais is running.", "success")
            self.status_label.configure(text="rtl_ais: ACTIVE", text_color=ACCENT_GREEN)
        else:
            self.console.log(f"Could not start rtl_ais: {controller.last_error}", "error")
            self.status_label.configure(text="rtl_ais: not running", text_color=ACCENT_RED)

    def _stop_rtl_ais(self) -> None:
        if self._controller is None:
            self.console.log("rtl_ais was not started from this panel — nothing to stop here.", "warning")
            return
        self._controller.shutdown()
        self.console.log("rtl_ais stopped.", "info")
        self.status_label.configure(text="rtl_ais: stopped", text_color=ACCENT_RED)

    def shutdown(self) -> None:
        super().shutdown()
        if self._controller is not None:
            self._controller.shutdown()
