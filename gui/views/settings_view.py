"""
gui/views/settings_view.py — Settings: theme, paths, about.
"""

from __future__ import annotations

import platform

import customtkinter as ctk

from gui.themes.theme import Theme
from gui.views.base_view import BaseView, CORE_DIR, REPO_ROOT

VERSION = "2.0.0-gui"


class SettingsView(BaseView):
    def __init__(self, master, theme: Theme, console, **kwargs):
        super().__init__(master, theme, console, **kwargs)
        self._build_ui()

    def _build_ui(self) -> None:
        theme = self.theme
        header = ctk.CTkLabel(self, text="Settings", font=ctk.CTkFont(size=24, weight="bold"), text_color=theme.text)
        header.pack(anchor="w", pady=(0, 4))
        sub = ctk.CTkLabel(self, text="Appearance, paths, and application info",
                            font=ctk.CTkFont(size=13), text_color=theme.text_dim)
        sub.pack(anchor="w", pady=(0, 16))

        appearance_panel = ctk.CTkFrame(self, fg_color=theme.panel, corner_radius=10)
        appearance_panel.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(appearance_panel, text="APPEARANCE",
                     font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=16, pady=(12, 8))
        theme_row = ctk.CTkFrame(appearance_panel, fg_color="transparent")
        theme_row.pack(fill="x", padx=16, pady=(0, 14))
        ctk.CTkLabel(theme_row, text="Theme mode:").pack(side="left", padx=(0, 10))
        self.theme_menu = ctk.CTkOptionMenu(theme_row, values=["Dark", "Light"],
                                             command=self._on_theme_selected, width=120)
        self.theme_menu.set("Dark" if theme.mode == "dark" else "Light")
        self.theme_menu.pack(side="left")

        paths_panel = ctk.CTkFrame(self, fg_color=theme.panel, corner_radius=10)
        paths_panel.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(paths_panel, text="PATHS", font=ctk.CTkFont(size=12, weight="bold")
                     ).pack(anchor="w", padx=16, pady=(12, 8))
        self._path_row(paths_panel, "Repository root:", REPO_ROOT)
        self._path_row(paths_panel, "Core backend:", CORE_DIR)
        self._path_row(paths_panel, "SSTV captures:", f"{CORE_DIR}/captures/sstv")
        ctk.CTkFrame(paths_panel, fg_color="transparent", height=6).pack()

        about_panel = ctk.CTkFrame(self, fg_color=theme.panel, corner_radius=10)
        about_panel.pack(fill="x")
        ctk.CTkLabel(about_panel, text="ABOUT", font=ctk.CTkFont(size=12, weight="bold")
                     ).pack(anchor="w", padx=16, pady=(12, 8))
        about_text = (
            f"PAR AVION GUI  v{VERSION}\n"
            f"Python {platform.python_version()}  |  {platform.system()} {platform.machine()}\n"
            "Tactical RF, ADS-B, Maritime AIS, Satellite & Signal Decoding Suite.\n"
            "All modes are receive-only — no mode in this application transmits "
            "on any RF interface."
        )
        ctk.CTkLabel(about_panel, text=about_text, font=ctk.CTkFont(size=12),
                     text_color=theme.text_dim, justify="left").pack(anchor="w", padx=16, pady=(0, 16))

    def _path_row(self, parent, label: str, value: str) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=2)
        ctk.CTkLabel(row, text=label, width=140, anchor="w",
                     font=ctk.CTkFont(size=12)).pack(side="left")
        ctk.CTkLabel(row, text=value, anchor="w", font=ctk.CTkFont(size=12),
                     text_color=self.theme.text_dim).pack(side="left")

    def _on_theme_selected(self, value: str) -> None:
        target_mode = "dark" if value == "Dark" else "light"
        if target_mode != self.theme.mode:
            app = self.winfo_toplevel()
            toggle_fn = getattr(app, "_toggle_theme", None)
            if callable(toggle_fn):
                toggle_fn()

    def apply_theme(self, theme: Theme) -> None:
        self.theme = theme
        # NEVER call self.__init__() again — see dashboard.py/iss_view.py
        # for why (it silently clears the widget's own grid placement in
        # its parent). Also note theme_menu.set() is intentionally NOT
        # called here before the rebuild: theme_menu is itself one of the
        # children about to be destroyed below, and _build_ui() sets the
        # freshly-created one to the correct value anyway.
        for child in self.winfo_children():
            child.destroy()
        self._build_ui()
