"""
gui/views/waterfalls_view.py — Waterfalls mode: fully embedded spectrum
analyzer + scrolling waterfall.

No external terminal is ever launched. Reads from the real
core.modules.waterfall.SDRReader (which itself falls back to a
simulated spectrum when no RTL-SDR is attached) on a periodic Tk
`after()` poll, pushing each new power array onto native Canvas-based
SpectrumCanvas / WaterfallCanvas widgets.
"""

from __future__ import annotations

import sys

import customtkinter as ctk

from gui.components.waterfall_canvas import SpectrumCanvas, WaterfallCanvas
from gui.themes.theme import ACCENT_GREEN, ACCENT_CYAN, ACCENT_RED, Theme
from gui.views.base_view import BaseView, CORE_DIR

sys.path.insert(0, CORE_DIR)

POLL_MS = 150
N_BINS = 300


class WaterfallsView(BaseView):
    def __init__(self, master, theme: Theme, console, **kwargs):
        super().__init__(master, theme, console, **kwargs)
        self._tuner = None
        self._reader = None
        self._running = False
        self._poll_job = None

        header = ctk.CTkLabel(self, text="Waterfalls",
                               font=ctk.CTkFont(size=24, weight="bold"),
                               text_color=theme.text)
        header.pack(anchor="w", pady=(0, 4))
        desc = ctk.CTkLabel(
            self, text="Live RTL-SDR spectrum analyzer and scrolling waterfall. "
                       "Falls back to a simulated spectrum if no SDR is detected.",
            font=ctk.CTkFont(size=13), text_color=theme.text_dim,
            wraplength=900, justify="left",
        )
        desc.pack(anchor="w", pady=(0, 12))

        ctrl_row = ctk.CTkFrame(self, fg_color=theme.panel, corner_radius=10)
        ctrl_row.pack(fill="x", pady=(0, 10))
        inner = ctk.CTkFrame(ctrl_row, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=10)

        ctk.CTkLabel(inner, text="Band:").pack(side="left", padx=(0, 6))
        self.band_menu = ctk.CTkOptionMenu(
            inner,
            values=["433 MHz ISM", "FM Broadcast", "Airband (AM)", "2m HAM", "70cm HAM", "315 MHz ISM"],
            width=150, command=self._on_band_change,
        )
        self.band_menu.pack(side="left", padx=(0, 16))

        self.start_btn = ctk.CTkButton(inner, text="Start", width=110,
                                        fg_color=ACCENT_GREEN, hover_color="#2fae45",
                                        text_color="#0a0a0a", command=self._toggle_running)
        self.start_btn.pack(side="left", padx=(0, 16))

        self.status_label = ctk.CTkLabel(inner, text="Idle", font=ctk.CTkFont(size=12),
                                          text_color=theme.text_dim)
        self.status_label.pack(side="left")

        self.freq_label = ctk.CTkLabel(inner, text="", font=ctk.CTkFont(size=12),
                                        text_color=ACCENT_CYAN)
        self.freq_label.pack(side="right")

        content = ctk.CTkFrame(self, fg_color=theme.panel, corner_radius=10)
        content.pack(fill="both", expand=True)

        ctk.CTkLabel(content, text="SPECTRUM", font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=theme.text).pack(anchor="w", padx=14, pady=(12, 4))
        self.spectrum_canvas = SpectrumCanvas(content, height=140)
        self.spectrum_canvas.pack(fill="x", padx=14, pady=(0, 10))

        ctk.CTkLabel(content, text="WATERFALL", font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=theme.text).pack(anchor="w", padx=14, pady=(0, 4))
        self.waterfall_canvas = WaterfallCanvas(content)
        self.waterfall_canvas.pack(fill="both", expand=True, padx=14, pady=(0, 14))

    def _on_band_change(self, value: str) -> None:
        if self._tuner is None:
            return
        from modules.waterfall import BAND_PRESETS
        index = next((i for i, p in enumerate(BAND_PRESETS) if p[0] == value), 0)
        self._tuner.apply_preset(index)
        if self._reader is not None:
            self._reader.retune()
        self._update_freq_label()

    def _update_freq_label(self) -> None:
        if self._tuner is not None:
            self.freq_label.configure(
                text=f"{self._tuner.center_freq_hz/1e6:.4f} MHz  ({self._tuner.sample_rate_hz/1e6:.3f} MSPS)"
            )

    def _toggle_running(self) -> None:
        if self._running:
            self._stop()
        else:
            self._start()

    def _start(self) -> None:
        self.console.log("Starting spectrum acquisition...", "info")
        self.start_btn.configure(state="disabled")
        self.run_async(self._do_start, self._on_start_done)

    @staticmethod
    def _do_start():
        from modules.waterfall import TunerState, SDRReader
        tuner = TunerState()
        tuner.apply_preset(0)
        reader = SDRReader(tuner)
        return tuner, reader

    def _on_start_done(self, result, error) -> None:
        self.start_btn.configure(state="normal")
        if error is not None:
            self.console.log(f"Failed to start spectrum acquisition: {error}", "error")
            self.status_label.configure(text="Error", text_color=ACCENT_RED)
            return
        self._tuner, self._reader = result
        self._running = True
        self.start_btn.configure(text="Stop", fg_color=ACCENT_RED, hover_color="#c94a4a")
        mode_tag = "SIMULATED (no SDR detected)" if self._reader.simulated else "LIVE"
        self.status_label.configure(text=mode_tag, text_color=ACCENT_GREEN)
        self._update_freq_label()
        self.console.log(f"Spectrum acquisition started ({mode_tag}).", "success")
        self._poll_job = self.after(POLL_MS, self._poll_spectrum)

    def _stop(self) -> None:
        self._running = False
        if self._poll_job is not None:
            self.after_cancel(self._poll_job)
            self._poll_job = None
        if self._reader is not None:
            try:
                self._reader.close()
            except Exception:
                pass
        self.start_btn.configure(text="Start", fg_color=ACCENT_GREEN, hover_color="#2fae45")
        self.status_label.configure(text="Stopped", text_color=self.theme.text_dim)
        self.console.log("Spectrum acquisition stopped.", "info")

    def _poll_spectrum(self) -> None:
        if self._reader is not None:
            try:
                spectrum = self._reader.read_power_spectrum(N_BINS)
                spectrum_list = spectrum.tolist() if hasattr(spectrum, "tolist") else list(spectrum)
                self.spectrum_canvas.push_spectrum(spectrum_list)
                self.waterfall_canvas.push_spectrum(spectrum_list)
            except Exception as e:
                self.console.log(f"Spectrum read error: {e}", "warning")
        if self._running:
            self._poll_job = self.after(POLL_MS, self._poll_spectrum)

    def shutdown(self) -> None:
        super().shutdown()
        self._running = False
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except Exception:
                pass
        if self._reader is not None:
            try:
                self._reader.close()
            except Exception:
                pass
