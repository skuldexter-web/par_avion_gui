"""
gui/views/waterfalls_view.py — Waterfalls: embedded spectrum analyser,
scrolling waterfall and VFO.

Everything is in the window: no terminal, no external viewer. Samples
are read from `core.modules.waterfall.SDRReader` on a dedicated
acquisition thread and handed to the UI through a queue, so a slow USB
read can never stall the interface. If no RTL-SDR is attached the
back end produces a simulated spectrum and says so, which keeps the
whole view testable without hardware.
"""

from __future__ import annotations

import queue
import sys
import threading
import time

import customtkinter as ctk

from gui.components.vfo_knob import VfoKnob
from gui.components.waterfall_canvas import SpectrumCanvas, WaterfallCanvas
from gui.themes.theme import (ACCENT_AMBER, ACCENT_CYAN, ACCENT_GREEN,
                              ACCENT_RED, Theme)
from gui.views.base_view import BaseView, CORE_DIR

sys.path.insert(0, CORE_DIR)

UI_POLL_MS = 60
ACQUIRE_INTERVAL_S = 0.05
MIN_BINS = 160
MAX_BINS = 2048


class WaterfallsView(BaseView):
    def __init__(self, master, theme: Theme, console, **kwargs):
        super().__init__(master, theme, console, **kwargs)
        self._tuner = None
        self._reader = None
        self._running = False
        self._poll_job = None
        self._thread = None
        self._frames: "queue.Queue" = queue.Queue(maxsize=2)
        self._commands: "queue.Queue" = queue.Queue()
        self._stop_flag = threading.Event()
        self._bins = 600
        self._frame_count = 0
        self._last_rate_check = time.time()

        header = ctk.CTkLabel(self, text="Waterfalls",
                              font=ctk.CTkFont(size=24, weight="bold"),
                              text_color=theme.text)
        header.pack(anchor="w", pady=(0, 2))
        ctk.CTkLabel(
            self, text="Live RTL-SDR spectrum analyser, scrolling waterfall and "
                       "VFO. Falls back to a simulated spectrum when no SDR is "
                       "detected, so the display can be checked without hardware.",
            font=ctk.CTkFont(size=12), text_color=theme.text_dim,
            wraplength=900, justify="left",
        ).pack(anchor="w", pady=(0, 10))

        # --- control strip ------------------------------------------------
        ctrl = ctk.CTkFrame(self, fg_color=theme.panel, corner_radius=10)
        ctrl.pack(fill="x", pady=(0, 8))
        inner = ctk.CTkFrame(ctrl, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=10)

        self.start_btn = ctk.CTkButton(inner, text="Start", width=100,
                                       fg_color=ACCENT_GREEN, hover_color="#2fae45",
                                       text_color="#0a0a0a", command=self._toggle_running)
        self.start_btn.pack(side="left", padx=(0, 14))

        ctk.CTkLabel(inner, text="Band:", font=ctk.CTkFont(size=12),
                     text_color=theme.text_dim).pack(side="left", padx=(0, 6))
        self.band_menu = ctk.CTkOptionMenu(inner, values=self._band_names(),
                                           width=170, command=self._on_band_change)
        self.band_menu.pack(side="left", padx=(0, 14))

        ctk.CTkLabel(inner, text="Averaging:", font=ctk.CTkFont(size=12),
                     text_color=theme.text_dim).pack(side="left", padx=(0, 6))
        self.avg_menu = ctk.CTkOptionMenu(inner, values=["1", "2", "4", "8", "16"],
                                          width=70, command=self._on_avg_change)
        self.avg_menu.set("4")
        self.avg_menu.pack(side="left", padx=(0, 14))

        self.peak_switch = ctk.CTkSwitch(inner, text="Peak hold",
                                         command=self._on_peak_toggle)
        self.peak_switch.select()
        self.peak_switch.pack(side="left", padx=(0, 14))

        self.status_label = ctk.CTkLabel(inner, text="Idle",
                                         font=ctk.CTkFont(size=12),
                                         text_color=theme.text_dim)
        self.status_label.pack(side="left")

        self.rate_label = ctk.CTkLabel(inner, text="", font=ctk.CTkFont(size=11),
                                       text_color=ACCENT_CYAN)
        self.rate_label.pack(side="right")

        # --- main area: displays on the left, VFO on the right ------------
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=0)
        body.grid_rowconfigure(0, weight=1)

        displays = ctk.CTkFrame(body, fg_color=theme.panel, corner_radius=10)
        displays.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        displays.grid_columnconfigure(0, weight=1)
        displays.grid_rowconfigure(1, weight=0)
        displays.grid_rowconfigure(3, weight=1)

        ctk.CTkLabel(displays, text="SPECTRUM",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=theme.text).grid(row=0, column=0, sticky="w",
                                                 padx=14, pady=(12, 4))
        self.spectrum_canvas = SpectrumCanvas(displays, height=160)
        self.spectrum_canvas.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 10))

        ctk.CTkLabel(displays, text="WATERFALL",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=theme.text).grid(row=2, column=0, sticky="w",
                                                 padx=14, pady=(0, 4))
        self.waterfall_canvas = WaterfallCanvas(displays)
        self.waterfall_canvas.grid(row=3, column=0, sticky="nsew", padx=14, pady=(0, 14))
        self.waterfall_canvas.bind("<Configure>", self._on_display_resize)

        side = ctk.CTkFrame(body, fg_color="transparent", width=330)
        side.grid(row=0, column=1, sticky="ns")
        side.grid_propagate(False)

        self.vfo = VfoKnob(side, theme, on_change=self._on_vfo_change,
                           frequency_hz=self._preset(0)[1], step_hz=25_000)
        self.vfo.pack(fill="x")

        display_panel = ctk.CTkFrame(side, fg_color=theme.panel, corner_radius=10)
        display_panel.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(display_panel, text="DISPLAY",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=theme.text).pack(anchor="w", padx=12, pady=(10, 6))

        ctk.CTkLabel(display_panel, text="Contrast (dB range)",
                     font=ctk.CTkFont(size=11),
                     text_color=theme.text_dim).pack(anchor="w", padx=12)
        self.range_slider = ctk.CTkSlider(display_panel, from_=20, to=100,
                                          number_of_steps=16,
                                          command=self._on_range_change)
        self.range_slider.set(60)
        self.range_slider.pack(fill="x", padx=12, pady=(2, 8))

        self.auto_switch = ctk.CTkSwitch(display_panel, text="Auto noise floor",
                                         command=self._on_auto_toggle)
        self.auto_switch.select()
        self.auto_switch.pack(anchor="w", padx=12, pady=(0, 10))

        self.info_label = ctk.CTkLabel(display_panel, text="", justify="left",
                                       anchor="w", font=ctk.CTkFont(size=11),
                                       text_color=theme.text_dim, wraplength=290)
        self.info_label.pack(fill="x", padx=12, pady=(0, 12))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _presets():
        from modules.waterfall import BAND_PRESETS
        return BAND_PRESETS

    def _preset(self, index: int):
        return self._presets()[index]

    def _band_names(self):
        return [p[0] for p in self._presets()]

    def _on_display_resize(self, event) -> None:
        self._bins = max(MIN_BINS, min(MAX_BINS, int(event.width)))

    # ------------------------------------------------------------------
    # Controls
    # ------------------------------------------------------------------
    def _on_band_change(self, value: str) -> None:
        names = self._band_names()
        index = names.index(value) if value in names else 0
        preset = self._preset(index)
        self.vfo.set_frequency(preset[1])
        self._commands.put(("preset", index))
        if self._tuner is not None:
            self._update_info()

    def _on_vfo_change(self, hz: int) -> None:
        self._commands.put(("freq", hz))
        self.spectrum_canvas.set_marker(hz)
        self._update_info()

    def _on_avg_change(self, value: str) -> None:
        self._commands.put(("averages", int(value)))

    def _on_peak_toggle(self) -> None:
        self.spectrum_canvas.peak_hold_enabled = bool(self.peak_switch.get())
        if not self.peak_switch.get():
            self.spectrum_canvas.reset_peak_hold()

    def _on_range_change(self, value) -> None:
        self._commands.put(("range", float(value)))

    def _on_auto_toggle(self) -> None:
        self._commands.put(("auto_floor", bool(self.auto_switch.get())))

    def _toggle_running(self) -> None:
        if self._running:
            self._stop()
        else:
            self._start()

    # ------------------------------------------------------------------
    # Acquisition
    # ------------------------------------------------------------------
    def _start(self) -> None:
        self.console.log("Starting spectrum acquisition...", "info")
        self.start_btn.configure(state="disabled")
        self.run_async(self._do_start, self._on_start_done)

    def _do_start(self):
        from modules.waterfall import SDRReader, TunerState
        tuner = TunerState()
        tuner.apply_preset(self._band_names().index(self.band_menu.get()))
        tuner.set_freq(self.vfo.frequency_hz)
        tuner.step_hz = self.vfo.step_hz
        reader = SDRReader(tuner, averages=int(self.avg_menu.get()))
        reader.ceiling_db = reader.floor_db + float(self.range_slider.get())
        return tuner, reader

    def _on_start_done(self, result, error) -> None:
        self.start_btn.configure(state="normal")
        if error is not None:
            self.console.log(f"Could not start spectrum acquisition: {error}", "error")
            self.status_label.configure(text="Error", text_color=ACCENT_RED)
            return
        self._tuner, self._reader = result
        self._running = True
        self._stop_flag.clear()
        self.start_btn.configure(text="Stop", fg_color=ACCENT_RED, hover_color="#c94a4a")

        if self._reader.simulated:
            self.status_label.configure(text="SIMULATED (no SDR detected)",
                                        text_color=ACCENT_AMBER)
            self.console.log("No RTL-SDR detected — showing a simulated spectrum. "
                             "Plug in a dongle and press Start again for live data.",
                             "warning")
            if self._reader.last_error:
                self.console.log(f"SDR open failed: {self._reader.last_error}", "warning")
        else:
            self.status_label.configure(text="LIVE", text_color=ACCENT_GREEN)
            self.console.log("Spectrum acquisition started (live SDR).", "success")

        self.spectrum_canvas.set_marker(self._tuner.center_freq_hz)
        self._thread = threading.Thread(target=self._acquire_loop, daemon=True)
        self._thread.start()
        self._poll_job = self.after(UI_POLL_MS, self._poll_frames)
        self._update_info()

    def _acquire_loop(self) -> None:
        """Runs off the UI thread: applies pending tuning commands, reads
        one frame, hands it over, repeats."""
        while not self._stop_flag.is_set():
            try:
                while True:
                    kind, value = self._commands.get_nowait()
                    self._apply_command(kind, value)
            except queue.Empty:
                pass

            try:
                frame = self._reader.read_frame(self._bins)
            except Exception:
                time.sleep(0.3)
                continue

            try:
                self._frames.put_nowait(frame)
            except queue.Full:
                # The UI is behind; drop the stale frame and keep the
                # newest rather than building a backlog.
                try:
                    self._frames.get_nowait()
                    self._frames.put_nowait(frame)
                except (queue.Empty, queue.Full):
                    pass
            time.sleep(ACQUIRE_INTERVAL_S)

    def _apply_command(self, kind: str, value) -> None:
        if self._reader is None or self._tuner is None:
            return
        if kind == "freq":
            self._tuner.set_freq(value)
            self._reader.retune()
        elif kind == "preset":
            self._tuner.apply_preset(value)
            self._reader.retune()
        elif kind == "averages":
            self._reader.averages = max(1, int(value))
        elif kind == "range":
            self._reader.ceiling_db = self._reader.floor_db + float(value)
        elif kind == "auto_floor":
            self._reader.auto_floor = bool(value)

    def _poll_frames(self) -> None:
        frame = None
        try:
            while True:
                frame = self._frames.get_nowait()
        except queue.Empty:
            pass

        if frame is not None:
            self.spectrum_canvas.push_frame(frame)
            self.waterfall_canvas.push_frame(frame)
            self._frame_count += 1
            now = time.time()
            if now - self._last_rate_check >= 1.0:
                fps = self._frame_count / (now - self._last_rate_check)
                self.rate_label.configure(text=f"{fps:.0f} fps   {len(frame.norm)} bins")
                self._frame_count = 0
                self._last_rate_check = now

        if self._running:
            self._poll_job = self.after(UI_POLL_MS, self._poll_frames)

    def _stop(self) -> None:
        self._running = False
        self._stop_flag.set()
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except Exception:
                pass
            self._poll_job = None
        if self._thread is not None:
            self._thread.join(timeout=1.5)
            self._thread = None
        if self._reader is not None:
            try:
                self._reader.close()
            except Exception:
                pass
        self.start_btn.configure(text="Start", fg_color=ACCENT_GREEN,
                                 hover_color="#2fae45")
        self.status_label.configure(text="Stopped", text_color=self.theme.text_dim)
        self.rate_label.configure(text="")
        self.console.log("Spectrum acquisition stopped.", "info")

    def _update_info(self) -> None:
        from modules.waterfall import format_hz, format_step
        freq = self.vfo.frequency_hz
        lines = [f"Tuned: {format_hz(freq)}", f"Step: {format_step(self.vfo.step_hz)}"]
        if self._tuner is not None:
            lines.append(f"Span: {format_hz(self._tuner.span_hz)}")
            lines.append(f"Band: {self._tuner.band_name()}")
        self.info_label.configure(text="\n".join(lines))

    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        super().shutdown()
        self._running = False
        self._stop_flag.set()
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self._reader is not None:
            try:
                self._reader.close()
            except Exception:
                pass
