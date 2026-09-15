"""
gui/themes/theme.py — Fixed dark tactical color palette for PAR AVION GUI.

The application is strictly dark-mode only (no light mode, no runtime
toggle) — a black/dark-slate background with neon green/cyan/blue
accents, matching an aviation/SDR tactical console aesthetic. This
module exposes a single frozen Theme instance; nothing in the GUI
switches appearance mode at runtime.
"""

from __future__ import annotations

import customtkinter as ctk

# Accent palette — echoes classic radar-scope phosphor green plus a
# cyan/blue secondary accent for headers and live-data highlights.
ACCENT_GREEN = "#39d353"
ACCENT_GREEN_DIM = "#1f7a30"
ACCENT_CYAN = "#3ad6d6"
ACCENT_AMBER = "#e0a940"
ACCENT_RED = "#e05c5c"
ACCENT_BLUE = "#4a9eff"
ACCENT_PURPLE = "#b57bee"

# Tactical dark palette — near-black backgrounds, slightly-raised panels,
# a near-black console pane so scope-style green/cyan text pops off it.
DARK_BG = "#0a0a0a"
DARK_SIDEBAR = "#111214"
DARK_PANEL = "#16181b"
DARK_CONSOLE_BG = "#050805"
DARK_TEXT = "#e6e6e6"
DARK_TEXT_DIM = "#8a9199"


class Theme:
    """Fixed dark-mode color set. No toggle, no light variant — the
    application is strictly dark mode per product requirements. Views
    still read colors from this object (rather than hardcoding strings)
    so the palette stays centralized and easy to retune in one place."""

    def __init__(self):
        self.mode = "dark"
        self.bg = DARK_BG
        self.sidebar = DARK_SIDEBAR
        self.panel = DARK_PANEL
        self.console_bg = DARK_CONSOLE_BG
        self.text = DARK_TEXT
        self.text_dim = DARK_TEXT_DIM


def apply_dark_mode() -> None:
    """Call once at startup. CustomTkinter still has an appearance-mode
    concept internally (used to resolve a handful of built-in widget
    defaults); this pins it to dark and it is never changed again."""
    ctk.set_appearance_mode("dark")


# Log level -> accent color, used by the console/log widget for colored
# success/error/warning output as required.
LOG_COLORS = {
    "info": ACCENT_CYAN,
    "success": ACCENT_GREEN,
    "warning": ACCENT_AMBER,
    "error": ACCENT_RED,
    "debug": DARK_TEXT_DIM,
}

