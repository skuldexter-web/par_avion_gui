"""
gui/components/vfo_knob.py — Variable-frequency oscillator control.

A tuning panel with the three things a radio operator actually reaches
for:

  * a knob you drag (or scroll over) to tune in whole steps, with
    detents and a pointer, like the VFO on a real rig;
  * a digit readout where clicking the top or bottom half of a digit
    increments or decrements that decade — the fastest way to jump to a
    known frequency, and standard in SDR software;
  * a step-size selector, from 100 Hz up to 1 MHz.

The widget owns no radio: it reports the frequency it has been tuned to
through `on_change`, and the view decides what to do about it.
"""

from __future__ import annotations

import math
from typing import Callable, List, Optional

import customtkinter as ctk

from gui.themes.theme import ACCENT_AMBER, ACCENT_GREEN, DARK_TEXT_DIM, Theme

KNOB_BG = "#0c1014"
KNOB_FACE = "#161c22"
KNOB_EDGE = "#2b3a46"

# Degrees of knob rotation per tuning step. One full turn is ~24 steps,
# which feels close to a real rig's detented VFO.
DEGREES_PER_STEP = 15.0


class VfoKnob(ctk.CTkFrame):
    def __init__(self, master, theme: Theme,
                 on_change: Optional[Callable[[int], None]] = None,
                 frequency_hz: int = 100_000_000,
                 step_hz: int = 25_000,
                 steps: Optional[List[int]] = None,
                 min_hz: int = 100_000, max_hz: int = 1_766_000_000,
                 **kwargs):
        kwargs.setdefault("fg_color", theme.panel)
        kwargs.setdefault("corner_radius", 10)
        super().__init__(master, **kwargs)
        self.theme = theme
        self.on_change = on_change
        self.min_hz = min_hz
        self.max_hz = max_hz
        self._freq = int(frequency_hz)
        self._step = int(step_hz)
        self._steps = steps or [100, 500, 1_000, 2_500, 5_000, 10_000,
                                12_500, 25_000, 50_000, 100_000, 500_000, 1_000_000]
        self._angle = 0.0
        self._drag_angle: Optional[float] = None
        self._drag_accum = 0.0
        self._digit_hits: List[tuple] = []   # (x0, x1, decade_value)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(10, 0))
        ctk.CTkLabel(header, text="VFO", font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=theme.text).pack(side="left")
        self.mode_label = ctk.CTkLabel(header, text="", font=ctk.CTkFont(size=11),
                                       text_color=DARK_TEXT_DIM)
        self.mode_label.pack(side="right")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="x", padx=12, pady=(6, 10))

        self.knob = ctk.CTkCanvas(body, width=108, height=108, bg=KNOB_BG,
                                  highlightthickness=0)
        self.knob.pack(side="left", padx=(0, 12))
        self.knob.bind("<ButtonPress-1>", self._on_knob_press)
        self.knob.bind("<B1-Motion>", self._on_knob_drag)
        self.knob.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag_angle", None))
        self.knob.bind("<Button-4>", lambda e: self.nudge(1))
        self.knob.bind("<Button-5>", lambda e: self.nudge(-1))
        self.knob.bind("<MouseWheel>", lambda e: self.nudge(1 if e.delta > 0 else -1))

        right = ctk.CTkFrame(body, fg_color="transparent")
        right.pack(side="left", fill="both", expand=True)

        self.readout = ctk.CTkCanvas(right, height=46, bg=KNOB_BG,
                                     highlightthickness=0)
        self.readout.pack(fill="x")
        self.readout.bind("<Button-1>", self._on_digit_click)
        self.readout.bind("<Configure>", lambda e: self._draw_readout())

        hint = ctk.CTkLabel(
            right,
            text="drag the knob or scroll to tune · click a digit's top/bottom half",
            font=ctk.CTkFont(size=10), text_color=DARK_TEXT_DIM,
            anchor="w", justify="left",
        )
        hint.pack(fill="x", pady=(2, 6))

        step_row = ctk.CTkFrame(right, fg_color="transparent")
        step_row.pack(fill="x")
        ctk.CTkLabel(step_row, text="Step:", font=ctk.CTkFont(size=11),
                     text_color=theme.text_dim).pack(side="left", padx=(0, 6))
        self.step_menu = ctk.CTkOptionMenu(
            step_row, values=[self._format_step(s) for s in self._steps],
            width=110, height=26, command=self._on_step_change,
        )
        self.step_menu.set(self._format_step(self._step))
        self.step_menu.pack(side="left")

        ctk.CTkButton(step_row, text="◀", width=34, height=26,
                      command=lambda: self.nudge(-1)).pack(side="left", padx=(10, 2))
        ctk.CTkButton(step_row, text="▶", width=34, height=26,
                      command=lambda: self.nudge(1)).pack(side="left", padx=2)

        self._draw_knob()
        self._draw_readout()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @property
    def frequency_hz(self) -> int:
        return self._freq

    @property
    def step_hz(self) -> int:
        return self._step

    def set_frequency(self, hz: float, notify: bool = False) -> int:
        self._freq = int(max(self.min_hz, min(self.max_hz, hz)))
        self._draw_readout()
        if notify and self.on_change is not None:
            self.on_change(self._freq)
        return self._freq

    def set_step(self, hz: int) -> None:
        self._step = int(hz)
        self.step_menu.set(self._format_step(self._step))
        self._draw_readout()

    def set_mode_text(self, text: str) -> None:
        self.mode_label.configure(text=text)

    def nudge(self, steps: int) -> None:
        target = self._freq + steps * self._step
        if self._step > 1:
            target = round(target / self._step) * self._step
        self.set_frequency(target, notify=True)
        self._angle = (self._angle + steps * DEGREES_PER_STEP) % 360.0
        self._draw_knob()

    # ------------------------------------------------------------------
    # Knob
    # ------------------------------------------------------------------
    def _on_knob_press(self, event) -> None:
        self._drag_angle = self._angle_at(event.x, event.y)
        self._drag_accum = 0.0

    def _on_knob_drag(self, event) -> None:
        if self._drag_angle is None:
            return
        now = self._angle_at(event.x, event.y)
        delta = (now - self._drag_angle + 540.0) % 360.0 - 180.0
        self._drag_angle = now
        self._drag_accum += delta
        # Only emit whole steps, so dragging feels detented and never
        # lands the radio on an off-grid frequency.
        while abs(self._drag_accum) >= DEGREES_PER_STEP:
            direction = 1 if self._drag_accum > 0 else -1
            self._drag_accum -= direction * DEGREES_PER_STEP
            self.nudge(direction)

    def _angle_at(self, x: float, y: float) -> float:
        w = self.knob.winfo_width() or 108
        h = self.knob.winfo_height() or 108
        return math.degrees(math.atan2(y - h / 2.0, x - w / 2.0))

    def _draw_knob(self) -> None:
        c = self.knob
        c.delete("all")
        w = c.winfo_reqwidth()
        h = c.winfo_reqheight()
        cx, cy = w / 2.0, h / 2.0
        r = min(w, h) / 2.0 - 6

        c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=KNOB_FACE,
                      outline=KNOB_EDGE, width=2)
        # Detent ticks around the rim.
        for i in range(24):
            ang = math.radians(i * 15.0)
            r1, r2 = r - 3, r - (8 if i % 6 == 0 else 5)
            c.create_line(cx + r1 * math.sin(ang), cy - r1 * math.cos(ang),
                          cx + r2 * math.sin(ang), cy - r2 * math.cos(ang),
                          fill=KNOB_EDGE)
        # Pointer.
        ang = math.radians(self._angle)
        c.create_line(cx, cy, cx + (r - 12) * math.sin(ang),
                      cy - (r - 12) * math.cos(ang),
                      fill=ACCENT_GREEN, width=3)
        c.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, fill=ACCENT_GREEN,
                      outline="")
        c.create_text(cx, cy + r - 2, text=self._format_step(self._step),
                      fill=DARK_TEXT_DIM, font=("Consolas", 8))

    # ------------------------------------------------------------------
    # Digit readout
    # ------------------------------------------------------------------
    def _on_digit_click(self, event) -> None:
        h = self.readout.winfo_height() or 46
        for x0, x1, decade in self._digit_hits:
            if x0 <= event.x <= x1:
                direction = 1 if event.y < h / 2 else -1
                self.set_frequency(self._freq + direction * decade, notify=True)
                return

    def _draw_readout(self) -> None:
        c = self.readout
        c.delete("all")
        self._digit_hits = []
        w = max(1, c.winfo_width())
        h = max(1, c.winfo_height())

        # Fixed 10-digit field (up to 1,766 MHz), grouped as
        # GHz,MMM.kkk,hhh for readability.
        digits = f"{self._freq:010d}"
        groups = [(0, 1), (1, 4), (4, 7), (7, 10)]
        font = ("Consolas", 20, "bold")
        char_w = 15
        sep_w = 7
        total = len(digits) * char_w + (len(groups) - 1) * sep_w
        x = max(6, (w - total) / 2.0)
        y = h / 2.0

        leading = True
        for gi, (start, end) in enumerate(groups):
            for i in range(start, end):
                decade = 10 ** (len(digits) - 1 - i)
                if digits[i] != "0":
                    leading = False
                active = not leading or decade <= self._step
                color = ACCENT_AMBER if decade == self._step else (
                    self.theme.text if active else "#39434c")
                c.create_text(x + char_w / 2, y, text=digits[i], fill=color, font=font)
                self._digit_hits.append((x, x + char_w, decade))
                x += char_w
            if gi < len(groups) - 1:
                c.create_text(x + sep_w / 2, y + 4,
                              text="." if gi == 1 else ",",
                              fill=DARK_TEXT_DIM, font=font)
                x += sep_w

        c.create_text(w - 6, y, text="Hz", anchor="e", fill=DARK_TEXT_DIM,
                      font=("Consolas", 10))
        # Underline the digit the step size currently moves.
        for x0, x1, decade in self._digit_hits:
            if decade == self._step:
                c.create_line(x0 + 2, y + 14, x1 - 2, y + 14,
                              fill=ACCENT_AMBER, width=2)

    # ------------------------------------------------------------------
    def _on_step_change(self, label: str) -> None:
        for s in self._steps:
            if self._format_step(s) == label:
                self._step = s
                break
        self._draw_knob()
        self._draw_readout()

    @staticmethod
    def _format_step(hz: int) -> str:
        if hz >= 1_000_000:
            return f"{hz / 1e6:g} MHz"
        if hz >= 1_000:
            return f"{hz / 1e3:g} kHz"
        return f"{hz} Hz"
