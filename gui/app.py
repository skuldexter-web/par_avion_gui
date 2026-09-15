"""
gui/app.py — Main application window for PAR AVION GUI.

Wires together the sidebar navigation rail, the dynamic main content
area (one view per mode, only the active one is shown), and the
embedded log console at the bottom — the three-region layout from the
spec (Left Sidebar / Main View Area / Bottom Panel).

View switching just raises/lowers pre-built frames (all views are
constructed once at startup, not rebuilt on every nav click) — cheap
enough on a Pi and avoids re-creating widgets (and re-binding worker
threads) every time the user switches tabs.
"""

from __future__ import annotations

import sys

import customtkinter as ctk

from gui.components.console import LogConsole
from gui.components.sidebar import Sidebar
from gui.themes.theme import Theme, apply_dark_mode

# Views are imported lazily inside _build_views() so a single broken
# view module doesn't prevent the whole app from importing/starting —
# each import is wrapped and any failure is logged to the console
# instead of crashing app startup.
VIEW_MODULES = {
    "dashboard": ("gui.views.dashboard", "DashboardView"),
    "airplanes": ("gui.views.airplanes_view", "AirplanesView"),
    "waterfalls": ("gui.views.waterfalls_view", "WaterfallsView"),
    "radio": ("gui.views.radio_view", "RadioView"),
    "maritime": ("gui.views.maritime_view", "MaritimeView"),
    "iss": ("gui.views.iss_view", "IssView"),
    "sstv": ("gui.views.sstv_view", "SstvView"),
    "morse": ("gui.views.morse_view", "MorseView"),
    "settings": ("gui.views.settings_view", "SettingsView"),
}


class ParAvionApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.theme = Theme()
        apply_dark_mode()
        ctk.set_default_color_theme("green")

        self.title("PAR AVION — Tactical RF & Telemetry Suite")
        self.geometry("1280x800")
        self.minsize(900, 600)
        self.configure(fg_color=self.theme.bg)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # --- Root layout: sidebar | (views + console) ---
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.sidebar = Sidebar(self, self.theme, self._show_view)
        self.sidebar.grid(row=0, column=0, sticky="ns")

        right = ctk.CTkFrame(self, fg_color=self.theme.bg, corner_radius=0)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_rowconfigure(0, weight=3)  # view area gets more space
        right.grid_rowconfigure(1, weight=1)  # console gets the rest
        right.grid_columnconfigure(0, weight=1)

        self.view_container = ctk.CTkFrame(right, fg_color=self.theme.bg,
                                            corner_radius=0)
        self.view_container.grid(row=0, column=0, sticky="nsew", padx=12, pady=(12, 6))

        self.console = LogConsole(right, self.theme)
        self.console.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))

        self._views: dict[str, ctk.CTkFrame] = {}
        self._active_view_key: str | None = None
        self._build_views()

        self.console.log("PAR AVION GUI started.", "success")
        self._show_view("dashboard")

    # ------------------------------------------------------------------
    def _build_views(self) -> None:
        for key, (module_path, class_name) in VIEW_MODULES.items():
            try:
                module = __import__(module_path, fromlist=[class_name])
                view_cls = getattr(module, class_name)
                view = view_cls(self.view_container, self.theme, self.console)
                view.grid(row=0, column=0, sticky="nsew")
                self._views[key] = view
            except Exception as e:
                # A broken view shouldn't take down the whole app —
                # log it and fall back to a placeholder so navigation
                # still works for every other tab.
                placeholder = ctk.CTkFrame(self.view_container, fg_color="transparent")
                ctk.CTkLabel(
                    placeholder,
                    text=f"'{key}' view failed to load:\n{e}",
                    text_color="#e05c5c", justify="left",
                ).pack(padx=20, pady=20, anchor="w")
                placeholder.grid(row=0, column=0, sticky="nsew")
                self._views[key] = placeholder
                self.console.log(f"View '{key}' failed to load: {e}", "error")

        self.view_container.grid_rowconfigure(0, weight=1)
        self.view_container.grid_columnconfigure(0, weight=1)

    def _show_view(self, key: str) -> None:
        if key == self._active_view_key:
            return
        view = self._views.get(key)
        if view is not None:
            view.tkraise()
            self._active_view_key = key
            self.sidebar.set_active(key)

    def _on_close(self) -> None:
        # Give every view a chance to stop background threads/subprocess
        # cleanly before the process exits, so nothing (dump1090,
        # rtl_ais, rtl_fm, sox play, capture threads) is left running.
        for view in self._views.values():
            shutdown_fn = getattr(view, "shutdown", None)
            if callable(shutdown_fn):
                try:
                    shutdown_fn()
                except Exception:
                    pass
        self.sidebar.telemetry.shutdown()
        self.destroy()


def main() -> None:
    app = ParAvionApp()
    app.mainloop()


if __name__ == "__main__":
    sys.exit(main())
