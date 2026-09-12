"""
gui/themes/theme.py — Color palette and theme switching for PAR AVION GUI.

Defines a small palette on top of CustomTkinter's own light/dark
appearance modes, so the app's accent colors (the green "scope" look
carried over from the CLI) stay consistent across both modes rather
than relying only on CustomTkinter's defaults.
"""

from __future__ import annotations

import customtkinter as ctk

# Accent palette — deliberately echoes the CLI's ANSI green/cyan/amber/red
# scope aesthetic, adapted for a GUI (flat colors instead of terminal
# color pairs).
ACCENT_GREEN = "#39d353"
ACCENT_GREEN_DIM = "#1f7a30"
ACCENT_CYAN = "#3ad6d6"
ACCENT_AMBER = "#e0a940"
ACCENT_RED = "#e05c5c"
ACCENT_BLUE = "#4a9eff"
ACCENT_PURPLE = "#b57bee"

DARK_BG = "#121212"
DARK_SIDEBAR = "#181818"
DARK_PANEL = "#1e1e1e"
DARK_CONSOLE_BG = "#0a0f0a"
DARK_TEXT = "#e6e6e6"
DARK_TEXT_DIM = "#9a9a9a"

LIGHT_BG = "#f4f4f4"
LIGHT_SIDEBAR = "#e8e8e8"
LIGHT_PANEL = "#ffffff"
LIGHT_CONSOLE_BG = "#eef4ee"
LIGHT_TEXT = "#1a1a1a"
LIGHT_TEXT_DIM = "#5a5a5a"


class Theme:
    """Holds the current mode's resolved colors; call toggle()/set_mode()
    to switch. Views read colors from this object rather than hardcoding
    them, so a theme switch can restyle already-built widgets."""

    def __init__(self, mode: str = "dark"):
        self.mode = mode
        self._refresh()

    def _refresh(self) -> None:
        if self.mode == "light":
            self.bg = LIGHT_BG
            self.sidebar = LIGHT_SIDEBAR
            self.panel = LIGHT_PANEL
            self.console_bg = LIGHT_CONSOLE_BG
            self.text = LIGHT_TEXT
            self.text_dim = LIGHT_TEXT_DIM
        else:
            self.bg = DARK_BG
            self.sidebar = DARK_SIDEBAR
            self.panel = DARK_PANEL
            self.console_bg = DARK_CONSOLE_BG
            self.text = DARK_TEXT
            self.text_dim = DARK_TEXT_DIM

    def toggle(self) -> str:
        self.mode = "light" if self.mode == "dark" else "dark"
        self._refresh()
        ctk.set_appearance_mode(self.mode)
        return self.mode

    def set_mode(self, mode: str) -> None:
        if mode not in ("light", "dark"):
            return
        self.mode = mode
        self._refresh()
        ctk.set_appearance_mode(self.mode)


# Log level -> accent color, used by the console/log widget for colored
# success/error/warning output as required.
LOG_COLORS = {
    "info": ACCENT_CYAN,
    "success": ACCENT_GREEN,
    "warning": ACCENT_AMBER,
    "error": ACCENT_RED,
    "debug": DARK_TEXT_DIM,
}
