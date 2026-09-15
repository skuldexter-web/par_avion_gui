"""
gui/components/radar_canvas.py — Native circular radar display.

Renders a rotating-sweep circular radar (concentric range rings, compass
labels, range-ring distance labels, a rotating sweep arm with a fading
trail, and plotted contacts) directly on a Tkinter Canvas — replacing
the old "launch the curses view in an external terminal" approach.
Matches the look of the reference tactical radar images: black
background, neon green rings/sweep, contact blips with callsign+speed+
track labels.

This widget owns no data-acquisition logic — callers periodically push
a fresh contact list via `update_contacts()` and the widget redraws
itself and advances the sweep animation on its own `after()` timer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Sequence

import customtkinter as ctk

from gui.themes.theme import ACCENT_GREEN

SWEEP_STEP_DEG = 3.0
ANIMATE_MS = 40


@dataclass
class RadarContact:
    """One plotted contact. distance_nm/bearing_deg are relative to the
    radar's center; label_lines is drawn next to the blip, top to
    bottom (e.g. ["KLM2487", "37000 ft  452 kt"])."""
    distance_nm: float
    bearing_deg: float           # 0 = North, clockwise
    label_lines: Sequence[str]
    glyph: str = "\u25b2"          # triangle; callers can use a plane/ship glyph


class RadarCanvas(ctk.CTkCanvas):
    """
    A self-animating circular radar. `max_range_nm` sets what the
    outermost ring represents; contacts farther than that are clamped
    to the outer ring rather than dropped, so nothing "disappears".
    """

    def __init__(self, master, max_range_nm: float = 60.0,
                 ring_count: int = 4, color: str = ACCENT_GREEN,
                 unit_label: str = "NM", **kwargs):
        kwargs.setdefault("bg", "#000000")
        kwargs.setdefault("highlightthickness", 0)
        super().__init__(master, **kwargs)
        self.max_range_nm = max_range_nm
        self.ring_count = ring_count
        self.color = color
        self.unit_label = unit_label
        self._contacts: List[RadarContact] = []
        self._sweep_deg = 0.0
        self._animating = True
        self._after_id = None

        self.bind("<Configure>", lambda e: self._redraw())
        self._tick()

    # ------------------------------------------------------------------
    def update_contacts(self, contacts: Sequence[RadarContact]) -> None:
        self._contacts = list(contacts)
        self._redraw()

    def set_max_range(self, max_range_nm: float) -> None:
        self.max_range_nm = max(0.1, max_range_nm)
        self._redraw()

    def pause(self) -> None:
        self._animating = False

    def resume(self) -> None:
        self._animating = True

    def stop_animation(self) -> None:
        """Call before destroying this widget so its recurring after()
        timer doesn't keep firing against a dead widget."""
        self._animating = False
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    # ------------------------------------------------------------------
    def _tick(self) -> None:
        if self._animating:
            self._sweep_deg = (self._sweep_deg + SWEEP_STEP_DEG) % 360
            self._redraw()
        self._after_id = self.after(ANIMATE_MS, self._tick)

    def _geometry(self):
        w = self.winfo_width() or 400
        h = self.winfo_height() or 400
        cx, cy = w / 2, h / 2
        r_max = max(10.0, min(w, h) / 2 - 40)  # margin for compass labels
        return w, h, cx, cy, r_max

    def _redraw(self) -> None:
        self.delete("all")
        w, h, cx, cy, r_max = self._geometry()

        self._draw_rings(cx, cy, r_max)
        self._draw_crosshair(cx, cy, r_max)
        self._draw_compass(cx, cy, r_max)
        self._draw_sweep(cx, cy, r_max)
        self._draw_contacts(cx, cy, r_max)

    def _draw_rings(self, cx, cy, r_max) -> None:
        for i in range(1, self.ring_count + 1):
            r = r_max * i / self.ring_count
            self.create_oval(cx - r, cy - r, cx + r, cy + r,
                              outline=self.color, width=1)
            ring_range = self.max_range_nm * i / self.ring_count
            label = f"{ring_range:.0f} {self.unit_label}"
            # Label sits on the vertical (North) radius, just inside the
            # ring, matching the reference image's layout.
            self.create_text(cx + 4, cy - r + 12, text=label, fill=self.color,
                              font=("Consolas", 10), anchor="w")

    def _draw_crosshair(self, cx, cy, r_max) -> None:
        self.create_line(cx - r_max, cy, cx + r_max, cy, fill=self.color, width=1)
        self.create_line(cx, cy - r_max, cx, cy + r_max, fill=self.color, width=1)
        for deg in range(0, 360, 30):
            ang = math.radians(deg)
            x1 = cx + (r_max - 6) * math.sin(ang)
            y1 = cy - (r_max - 6) * math.cos(ang)
            x2 = cx + r_max * math.sin(ang)
            y2 = cy - r_max * math.cos(ang)
            self.create_line(x1, y1, x2, y2, fill=self.color, width=1)

    def _draw_compass(self, cx, cy, r_max) -> None:
        pad = 22
        self.create_text(cx, cy - r_max - pad, text="N", fill=self.color,
                          font=("Consolas", 16, "bold"))
        self.create_text(cx, cy + r_max + pad, text="S", fill=self.color,
                          font=("Consolas", 16, "bold"))
        self.create_text(cx + r_max + pad, cy, text="E", fill=self.color,
                          font=("Consolas", 16, "bold"))
        self.create_text(cx - r_max - pad, cy, text="W", fill=self.color,
                          font=("Consolas", 16, "bold"))
        self.create_text(cx, cy - r_max - pad + 16, text="000\u00b0", fill=self.color,
                          font=("Consolas", 9))
        self.create_text(cx, cy + r_max + pad - 16, text="180\u00b0", fill=self.color,
                          font=("Consolas", 9))
        self.create_text(cx + r_max + pad, cy + 16, text="090\u00b0", fill=self.color,
                          font=("Consolas", 9))
        self.create_text(cx - r_max - pad, cy + 16, text="270\u00b0", fill=self.color,
                          font=("Consolas", 9))

    def _draw_sweep(self, cx, cy, r_max) -> None:
        # Plain Tkinter canvas colors have no real alpha blending, so the
        # "fading trail" look from the reference image is approximated
        # with several thin radial lines behind the leading edge whose
        # color is progressively blended toward black.
        trail_steps = 18
        for i in range(trail_steps, 0, -1):
            ang = math.radians(self._sweep_deg - i * 1.5)
            x = cx + r_max * math.sin(ang)
            y = cy - r_max * math.cos(ang)
            fade = i / trail_steps
            fill = self._fade_color(fade)
            self.create_line(cx, cy, x, y, fill=fill, width=2)

        ang = math.radians(self._sweep_deg)
        x = cx + r_max * math.sin(ang)
        y = cy - r_max * math.cos(ang)
        self.create_line(cx, cy, x, y, fill=self.color, width=2)

    def _fade_color(self, fade: float) -> str:
        """Blend self.color toward black by (1-fade)."""
        try:
            r = int(self.color[1:3], 16)
            g = int(self.color[3:5], 16)
            b = int(self.color[5:7], 16)
        except (ValueError, IndexError):
            r, g, b = 0, 200, 80
        r = int(r * fade)
        g = int(g * fade)
        b = int(b * fade)
        return f"#{r:02x}{g:02x}{b:02x}"

    def _draw_contacts(self, cx, cy, r_max) -> None:
        for c in self._contacts:
            frac = min(1.0, max(0.0, c.distance_nm / self.max_range_nm)) if self.max_range_nm else 0.0
            r = frac * r_max
            ang = math.radians(c.bearing_deg)
            x = cx + r * math.sin(ang)
            y = cy - r * math.cos(ang)

            self.create_text(x, y, text=c.glyph, fill=self.color,
                              font=("Consolas", 14, "bold"))
            # Flip the label to the left of the blip when it's in the
            # right half of the canvas, and right-align it there —
            # otherwise labels on contacts near the East edge get
            # clipped off the visible canvas area (the offset would
            # push them further right, off-screen).
            on_right_half = x > cx
            label_x = x - 12 if on_right_half else x + 12
            anchor = "e" if on_right_half else "w"
            for i, line in enumerate(c.label_lines):
                self.create_text(label_x, y - 8 + i * 12, text=line, fill=self.color,
                                  font=("Consolas", 9), anchor=anchor)
