"""
gui/components/sidebar.py — Left sidebar navigation rail.

Holds the mode-switch buttons (one per CLI mode), the telemetry widget,
and the light/dark theme toggle, per the "sidebar navigation rail"
layout requirement. Clicking a nav button calls back into the main App
to swap the visible view; the sidebar itself holds no view logic.
"""

from __future__ import annotations

from typing import Callable, Optional

import customtkinter as ctk

from gui.components.telemetry import TelemetryWidget
from gui.themes.theme import ACCENT_GREEN, Theme

NAV_ITEMS = [
    ("dashboard", "Dashboard"),
    ("airplanes", "Airplanes"),
    ("waterfalls", "Waterfalls"),
    ("radio", "Radio"),
    ("maritime", "Maritime"),
    ("iss", "ISS Tracker"),
    ("sstv", "SSTV"),
    ("morse", "Morse / CW"),
    ("settings", "Settings"),
]


class Sidebar(ctk.CTkFrame):
    def __init__(self, master, theme: Theme,
                 on_nav: Callable[[str], None],
                 on_theme_toggle: Callable[[], None],
                 **kwargs):
        super().__init__(master, fg_color=theme.sidebar, corner_radius=0,
                          width=210, **kwargs)
        self.theme = theme
        self.on_nav = on_nav
        self.on_theme_toggle = on_theme_toggle
        self.grid_propagate(False)

        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        self._active_key: Optional[str] = None

        # --- Header / logo ---
        header = ctk.CTkLabel(
            self, text="PAR AVION",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=ACCENT_GREEN,
        )
        header.pack(pady=(18, 0))
        subheader = ctk.CTkLabel(
            self, text="Tactical RF Suite v2.0",
            font=ctk.CTkFont(size=11),
            text_color=theme.text_dim,
        )
        subheader.pack(pady=(0, 14))

        # --- Nav buttons ---
        nav_frame = ctk.CTkScrollableFrame(self, fg_color="transparent",
                                            width=190)
        nav_frame.pack(fill="both", expand=True, padx=8)

        for key, label in NAV_ITEMS:
            btn = ctk.CTkButton(
                nav_frame, text=label, anchor="w",
                fg_color="transparent", hover_color=theme.panel,
                text_color=theme.text, corner_radius=6, height=36,
                command=lambda k=key: self._handle_nav_click(k),
            )
            btn.pack(fill="x", pady=2)
            self._nav_buttons[key] = btn

        # --- Telemetry ---
        self.telemetry = TelemetryWidget(self, theme)
        self.telemetry.pack(fill="x", padx=10, pady=(10, 6), side="bottom")

        # --- Theme toggle ---
        toggle_frame = ctk.CTkFrame(self, fg_color="transparent")
        toggle_frame.pack(fill="x", padx=10, pady=(0, 12), side="bottom")
        ctk.CTkLabel(toggle_frame, text="Theme", font=ctk.CTkFont(size=11),
                     text_color=theme.text_dim).pack(side="left")
        self.theme_switch = ctk.CTkSwitch(
            toggle_frame, text="Dark" if theme.mode == "dark" else "Light",
            command=self._handle_theme_toggle, onvalue="dark", offvalue="light",
        )
        self.theme_switch.pack(side="right")
        if theme.mode == "dark":
            self.theme_switch.select()
        else:
            self.theme_switch.deselect()

        self.set_active("dashboard")

    def _handle_nav_click(self, key: str) -> None:
        self.set_active(key)
        self.on_nav(key)

    def _handle_theme_toggle(self) -> None:
        self.on_theme_toggle()
        mode = self.theme.mode
        self.theme_switch.configure(text="Dark" if mode == "dark" else "Light")

    def set_active(self, key: str) -> None:
        """Visually highlight the active nav item."""
        for k, btn in self._nav_buttons.items():
            if k == key:
                btn.configure(fg_color=self.theme.panel, text_color=ACCENT_GREEN,
                              font=ctk.CTkFont(size=13, weight="bold"))
            else:
                btn.configure(fg_color="transparent", text_color=self.theme.text,
                              font=ctk.CTkFont(size=13, weight="normal"))
        self._active_key = key

    def apply_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.configure(fg_color=theme.sidebar)
        self.telemetry.apply_theme(theme)
        if self._active_key:
            self.set_active(self._active_key)
