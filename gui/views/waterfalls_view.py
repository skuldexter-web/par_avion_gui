"""
gui/views/waterfalls_view.py — Waterfalls mode (RTL-SDR spectrum analyzer).
"""

from __future__ import annotations

from gui.views.launch_view import LaunchModeView


class WaterfallsView(LaunchModeView):
    mode_key = "waterfalls"
    mode_label = "Waterfalls"
    description = ("Live RTL-SDR spectrum analyzer with a scrolling ANSI "
                    "waterfall display. Falls back to a simulated spectrum "
                    "if no SDR hardware is detected, so the display is "
                    "still explorable without a dongle attached.")
    keybindings = [
        ("2", "Enter Waterfalls mode from the main menu"),
        ("Left/Right", "Tune +/-100 kHz"),
        ("Shift+Left/Right", "Fine-tune +/-10 kHz"),
        ("Up/Down", "Cycle band presets"),
        ("Q / Esc", "Return to menu"),
    ]
