"""
gui/components/waterfall_canvas.py — Native spectrum + scrolling waterfall.

Renders a live FFT power-spectrum line plot (top) and a scrolling
color-gradient waterfall/spectrogram (bottom) directly on Tkinter
Canvases, driven by periodic `push_spectrum()` calls with a numpy
power array (0..1 normalized) — the same shape SDRReader.read_power_spectrum()
already returns. No external tool or terminal is involved.
"""

from __future__ import annotations

from collections import deque
from typing import Deque, Optional, Sequence

import customtkinter as ctk

_GRADIENT_STOPS = [
    (0.0, (10, 10, 40)),
    (0.25, (20, 60, 180)),
    (0.5, (0, 180, 200)),
    (0.7, (60, 200, 80)),
    (0.85, (230, 210, 40)),
    (1.0, (230, 40, 40)),
]


def _gradient_color(value: float) -> str:
    value = max(0.0, min(1.0, value))
    for i in range(len(_GRADIENT_STOPS) - 1):
        f0, c0 = _GRADIENT_STOPS[i]
        f1, c1 = _GRADIENT_STOPS[i + 1]
        if f0 <= value <= f1:
            t = (value - f0) / (f1 - f0) if f1 > f0 else 0.0
            r = int(c0[0] + (c1[0] - c0[0]) * t)
            g = int(c0[1] + (c1[1] - c0[1]) * t)
            b = int(c0[2] + (c1[2] - c0[2]) * t)
            return f"#{r:02x}{g:02x}{b:02x}"
    return "#000000"


class SpectrumCanvas(ctk.CTkCanvas):
    """Live FFT power-spectrum line plot."""

    def __init__(self, master, color: str = "#39d353", **kwargs):
        kwargs.setdefault("bg", "#050505")
        kwargs.setdefault("highlightthickness", 0)
        super().__init__(master, **kwargs)
        self.color = color
        self._spectrum: Optional[Sequence[float]] = None
        self.bind("<Configure>", lambda e: self._redraw())

    def push_spectrum(self, spectrum: Sequence[float]) -> None:
        self._spectrum = spectrum
        self._redraw()

    def _redraw(self) -> None:
        self.delete("all")
        w = self.winfo_width() or 400
        h = self.winfo_height() or 150
        for frac in (0.25, 0.5, 0.75):
            y = h * frac
            self.create_line(0, y, w, y, fill="#1a2a1a", width=1)

        if not self._spectrum:
            self.create_text(w / 2, h / 2, text="No signal data",
                              fill="#555555", font=("Consolas", 11))
            return

        n = len(self._spectrum)
        if n < 2:
            return
        step = w / (n - 1)
        points = []
        for i, val in enumerate(self._spectrum):
            x = i * step
            y = h - (max(0.0, min(1.0, val)) * (h - 6)) - 3
            points.extend([x, y])
        self.create_line(*points, fill=self.color, width=1, smooth=False)


class WaterfallCanvas(ctk.CTkCanvas):
    """
    Scrolling spectrogram: each push_spectrum() call inserts one new row
    at the top and scrolls everything else down, drawn as a grid of
    small colored rectangles (a full redraw each push — acceptable at
    the update rates this widget is used at).
    """

    def __init__(self, master, history_rows: int = 80, **kwargs):
        kwargs.setdefault("bg", "#000000")
        kwargs.setdefault("highlightthickness", 0)
        super().__init__(master, **kwargs)
        self.history_rows = history_rows
        self._history: Deque[Sequence[float]] = deque(maxlen=history_rows)
        self.bind("<Configure>", lambda e: self._redraw())

    def push_spectrum(self, spectrum: Sequence[float]) -> None:
        self._history.appendleft(spectrum)
        self._redraw()

    def _redraw(self) -> None:
        self.delete("all")
        w = self.winfo_width() or 400
        h = self.winfo_height() or 300
        if not self._history:
            return

        row_h = max(1.0, h / self.history_rows)
        n_bins = len(self._history[0]) if self._history[0] else 1
        col_w = max(1.0, w / n_bins)

        for row_idx, spectrum in enumerate(self._history):
            y0 = row_idx * row_h
            y1 = y0 + row_h
            group = max(1, int(n_bins / max(1, w / 2)))
            i = 0
            while i < len(spectrum):
                chunk = spectrum[i:i + group]
                if not chunk:
                    break
                avg = sum(chunk) / len(chunk)
                x0 = i * col_w
                x1 = min(w, (i + group) * col_w)
                self.create_rectangle(x0, y0, x1, y1, fill=_gradient_color(avg),
                                       outline="")
                i += group
