"""
gui/components/waterfall_canvas.py — Native spectrum + scrolling waterfall.

Two widgets, both fed by `core.modules.waterfall.SpectrumFrame`:

  SpectrumCanvas   live power-vs-frequency plot with a dB grid, labelled
                   frequency axis, peak-hold trace and a hover readout.
  WaterfallCanvas  scrolling spectrogram.

The waterfall renders through a Pillow image at one pixel per FFT bin
and is blitted to the canvas in a single operation. The previous version
drew a grid of Tk rectangles, grouping several bins into each one: that
is what made it look blocky, and it also got slower the wider the window
got. An image blit is both sharper and cheaper — it is a handful of
numpy operations and one PhotoImage update per frame, which matters on a
Raspberry Pi.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

import customtkinter as ctk

try:
    import numpy as np
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False

try:
    from PIL import Image, ImageTk
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False

from gui.themes.theme import ACCENT_CYAN, ACCENT_GREEN, DARK_TEXT_DIM

# Classic SDR waterfall ramp: black -> deep blue -> cyan -> green ->
# yellow -> red. Stops are (position 0..1, (r, g, b)).
_GRADIENT_STOPS = [
    (0.00, (4, 6, 18)),
    (0.18, (16, 36, 110)),
    (0.38, (0, 140, 190)),
    (0.56, (40, 190, 120)),
    (0.74, (220, 210, 60)),
    (0.88, (235, 120, 30)),
    (1.00, (240, 40, 40)),
]

_GRID_COLOR = "#16202a"
_AXIS_COLOR = "#44525e"


def _build_lut():
    """256-entry RGB lookup table for the gradient."""
    if not HAVE_NUMPY:
        return None
    lut = np.zeros((256, 3), dtype=np.uint8)
    positions = [s[0] for s in _GRADIENT_STOPS]
    colors = [s[1] for s in _GRADIENT_STOPS]
    xs = np.linspace(0.0, 1.0, 256)
    for channel in range(3):
        lut[:, channel] = np.interp(
            xs, positions, [c[channel] for c in colors]
        ).astype(np.uint8)
    return lut


_LUT = _build_lut()


def _gradient_color(value: float) -> str:
    """Single colour as a hex string (used by the no-Pillow fallback)."""
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


def _format_hz(hz: float) -> str:
    if hz >= 1e9:
        return f"{hz / 1e9:.4f}G"
    if hz >= 1e6:
        return f"{hz / 1e6:.3f}M"
    if hz >= 1e3:
        return f"{hz / 1e3:.1f}k"
    return f"{hz:.0f}"


class SpectrumCanvas(ctk.CTkCanvas):
    """Live power-vs-frequency plot."""

    def __init__(self, master, color: str = ACCENT_GREEN, **kwargs):
        kwargs.setdefault("bg", "#050709")
        kwargs.setdefault("highlightthickness", 0)
        super().__init__(master, **kwargs)
        self.color = color
        self._frame = None
        self._peak_hold = None
        self.peak_hold_enabled = True
        self.peak_decay_db = 0.35
        self._cursor_x: Optional[int] = None
        self._marker_hz: Optional[float] = None

        self.bind("<Configure>", lambda e: self._redraw())
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self._on_leave)

    # ------------------------------------------------------------------
    def push_frame(self, frame) -> None:
        self._frame = frame
        if HAVE_NUMPY and self.peak_hold_enabled:
            db = frame.power_db
            if self._peak_hold is None or len(self._peak_hold) != len(db):
                self._peak_hold = db.copy()
            else:
                # Peaks decay slowly so an intermittent burst stays
                # visible long enough to read, then fades.
                self._peak_hold = np.maximum(self._peak_hold - self.peak_decay_db, db)
        self._redraw()

    def reset_peak_hold(self) -> None:
        self._peak_hold = None
        self._redraw()

    def set_marker(self, hz: Optional[float]) -> None:
        """Highlight a frequency (the VFO) on the plot."""
        self._marker_hz = hz
        self._redraw()

    # ------------------------------------------------------------------
    def _on_motion(self, event) -> None:
        self._cursor_x = event.x
        self._redraw()

    def _on_leave(self, _event) -> None:
        self._cursor_x = None
        self._redraw()

    def _x_to_hz(self, x: float, w: int) -> Optional[float]:
        if self._frame is None or w <= 1:
            return None
        f = self._frame
        return f.center_hz - f.span_hz / 2.0 + f.span_hz * (x / w)

    def _hz_to_x(self, hz: float, w: int) -> float:
        f = self._frame
        low = f.center_hz - f.span_hz / 2.0
        return (hz - low) / max(1.0, f.span_hz) * w

    # ------------------------------------------------------------------
    def _redraw(self) -> None:
        self.delete("all")
        w = max(1, self.winfo_width())
        h = max(1, self.winfo_height())

        frame = self._frame
        if frame is None or not HAVE_NUMPY:
            self.create_text(w / 2, h / 2, text="No signal data",
                             fill="#555555", font=("Consolas", 11))
            return

        pad_bottom = 14
        plot_h = max(10, h - pad_bottom)
        floor, ceiling = frame.floor_db, frame.ceiling_db
        span_db = max(1e-6, ceiling - floor)

        def db_to_y(db: float) -> float:
            return plot_h - (db - floor) / span_db * (plot_h - 4) - 2

        # dB grid
        step = 10 if span_db <= 80 else 20
        level = float(int(ceiling // step) * step)
        while level > floor:
            y = db_to_y(level)
            self.create_line(0, y, w, y, fill=_GRID_COLOR)
            self.create_text(3, y - 6, text=f"{level:.0f}", anchor="w",
                             fill=_GRID_COLOR, font=("Consolas", 8))
            level -= step

        # Frequency axis
        for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
            x = frac * w
            hz = frame.center_hz + (frac - 0.5) * frame.span_hz
            self.create_line(x, 0, x, plot_h, fill=_GRID_COLOR)
            anchor = "w" if frac == 0.0 else "e" if frac == 1.0 else "center"
            self.create_text(min(w - 2, max(2, x)), h - 6, text=_format_hz(hz),
                             anchor=anchor, fill=_AXIS_COLOR, font=("Consolas", 8))

        n = len(frame.norm)
        step_x = w / max(1, n - 1)

        # Peak-hold trace behind the live trace.
        if self._peak_hold is not None and len(self._peak_hold) == n:
            pts: List[float] = []
            for i, db in enumerate(self._peak_hold):
                pts.extend((i * step_x, db_to_y(float(db))))
            if len(pts) >= 4:
                self.create_line(*pts, fill="#2d6b46", width=1)

        # Filled live trace.
        pts = [0.0, plot_h]
        for i, db in enumerate(frame.power_db):
            pts.extend((i * step_x, db_to_y(float(db))))
        pts.extend((w, plot_h))
        if len(pts) >= 6:
            self.create_polygon(*pts, fill="#0d2a1a", outline="")
        line_pts: List[float] = []
        for i, db in enumerate(frame.power_db):
            line_pts.extend((i * step_x, db_to_y(float(db))))
        if len(line_pts) >= 4:
            self.create_line(*line_pts, fill=self.color, width=1)

        # Centre line and VFO marker.
        self.create_line(w / 2, 0, w / 2, plot_h, fill="#3a4b57", dash=(3, 3))
        if self._marker_hz is not None:
            mx = self._hz_to_x(self._marker_hz, w)
            if 0 <= mx <= w:
                self.create_line(mx, 0, mx, plot_h, fill=ACCENT_CYAN)

        # Hover readout.
        if self._cursor_x is not None and 0 <= self._cursor_x <= w:
            hz = self._x_to_hz(self._cursor_x, w)
            idx = min(n - 1, max(0, int(self._cursor_x / max(1e-6, step_x))))
            db = float(frame.power_db[idx])
            self.create_line(self._cursor_x, 0, self._cursor_x, plot_h,
                             fill="#5d6f7d", dash=(2, 2))
            text = f"{_format_hz(hz)}Hz  {db:.1f} dB"
            tx = self._cursor_x + 6 if self._cursor_x < w * 0.7 else self._cursor_x - 6
            self.create_text(tx, 10, text=text, anchor="w" if tx > self._cursor_x else "e",
                             fill=ACCENT_CYAN, font=("Consolas", 9))

        peak_hz, peak_db = frame.peak()
        # Top-right, not bottom-right: the bottom edge belongs to the
        # frequency axis and the two labels would collide there.
        self.create_text(w - 4, 10, text=f"peak {_format_hz(peak_hz)}Hz {peak_db:.0f} dB",
                         anchor="e", fill=DARK_TEXT_DIM, font=("Consolas", 8))


class WaterfallCanvas(ctk.CTkCanvas):
    """Scrolling spectrogram rendered through a Pillow image."""

    def __init__(self, master, history_rows: int = 240, **kwargs):
        kwargs.setdefault("bg", "#04060a")
        kwargs.setdefault("highlightthickness", 0)
        super().__init__(master, **kwargs)
        self.history_rows = history_rows
        self._hist = None          # (rows, bins) float32, row 0 = newest
        self._bins = 0
        self._photo = None
        self._image_id = None
        self._frame = None
        self.bind("<Configure>", lambda e: self._redraw())

    # ------------------------------------------------------------------
    def push_frame(self, frame) -> None:
        self._frame = frame
        self.push_spectrum(frame.norm)

    def _resize_history(self, rows: int) -> None:
        """Keep one history row per screen pixel: fewer rows and the
        scroll is visibly chunky, more and the extra rows are thrown away
        by the downscale. Existing history is preserved across a resize."""
        rows = max(40, min(1200, int(rows)))
        if self._hist is not None and self._hist.shape[0] == rows:
            return
        new = np.zeros((rows, max(1, self._bins)), dtype=np.float32)
        if self._hist is not None and self._bins:
            keep = min(rows, self._hist.shape[0])
            new[:keep] = self._hist[:keep]
        self._hist = new
        self.history_rows = rows

    def push_spectrum(self, spectrum: Sequence[float]) -> None:
        if not HAVE_NUMPY:
            return
        row = np.asarray(spectrum, dtype=np.float32)
        if self._hist is None or row.size != self._bins:
            self._bins = row.size
            self._hist = np.zeros((self.history_rows, self._bins), dtype=np.float32)
        wanted_rows = max(40, self.winfo_height() or self.history_rows)
        if wanted_rows != self._hist.shape[0]:
            self._resize_history(wanted_rows)
        # Scroll down by one and write the newest row at the top.
        self._hist[1:] = self._hist[:-1]
        self._hist[0] = np.clip(row, 0.0, 1.0)
        self._redraw()

    def clear(self) -> None:
        if self._hist is not None:
            self._hist[:] = 0.0
        self._redraw()

    # ------------------------------------------------------------------
    def _redraw(self) -> None:
        w = max(1, self.winfo_width())
        h = max(1, self.winfo_height())
        if self._hist is None or not HAVE_NUMPY or w < 10 or h < 10:
            return

        if not HAVE_PIL or _LUT is None:
            self._redraw_fallback(w, h)
            return

        idx = (self._hist * 255.0).astype(np.uint8)
        rgb = _LUT[idx]                      # (rows, bins, 3)
        image = Image.fromarray(rgb, "RGB")
        # One source pixel per FFT bin, scaled to the widget. Bilinear
        # keeps the vertical scroll smooth instead of banded; the
        # horizontal axis is usually close to 1:1 because the view asks
        # for as many bins as it has pixels.
        image = image.resize((w, h), Image.BILINEAR)
        self._photo = ImageTk.PhotoImage(image)
        if self._image_id is None:
            self.delete("all")
            self._image_id = self.create_image(0, 0, anchor="nw", image=self._photo)
        else:
            self.itemconfigure(self._image_id, image=self._photo)
            self.coords(self._image_id, 0, 0)
        self._draw_overlay(w, h)

    def _draw_overlay(self, w: int, h: int) -> None:
        self.delete("overlay")
        self.create_line(w / 2, 0, w / 2, h, fill="#ffffff", stipple="gray25",
                         tags="overlay")
        if self._frame is not None:
            self.create_text(w - 4, h - 8,
                             text=f"span {_format_hz(self._frame.span_hz)}Hz",
                             anchor="e", fill="#8fa0ad", font=("Consolas", 8),
                             tags="overlay")

    def _redraw_fallback(self, w: int, h: int) -> None:
        """Pillow-less path: coarse rectangles, so the widget still shows
        something useful rather than nothing at all."""
        self.delete("all")
        rows = min(self.history_rows, 60)
        row_h = max(1.0, h / rows)
        cols = min(self._bins, 120)
        col_w = max(1.0, w / cols)
        for r in range(rows):
            spectrum = self._hist[r]
            for c in range(cols):
                lo = int(c * self._bins / cols)
                hi = max(lo + 1, int((c + 1) * self._bins / cols))
                value = float(spectrum[lo:hi].max())
                self.create_rectangle(c * col_w, r * row_h,
                                      (c + 1) * col_w, (r + 1) * row_h,
                                      fill=_gradient_color(value), outline="")
        self.create_text(w / 2, 10, text="install Pillow for a sharp waterfall",
                         fill=DARK_TEXT_DIM, font=("Consolas", 8))
