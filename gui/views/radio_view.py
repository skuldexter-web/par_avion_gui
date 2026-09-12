"""
gui/views/radio_view.py — Radio mode: native broadcast AM/FM tuner.

Fully native controls (frequency entry, preset dropdown, play/stop,
volume slider, band toggle) driving core.modules.radio.AudioTunerController
directly — this mode's controls map naturally to GUI widgets, unlike the
radar/scope modes, so no terminal launch is needed here.
"""

from __future__ import annotations

import sys

import customtkinter as ctk

from gui.themes.theme import ACCENT_GREEN, ACCENT_RED, Theme
from gui.views.base_view import BaseView, CORE_DIR

sys.path.insert(0, CORE_DIR)


class RadioView(BaseView):
    def __init__(self, master, theme: Theme, console, **kwargs):
        super().__init__(master, theme, console, **kwargs)
        self._tuner = None
        self._controller = None
        self._playing = False

        header = ctk.CTkLabel(self, text="Radio", font=ctk.CTkFont(size=24, weight="bold"), text_color=theme.text)
        header.pack(anchor="w", pady=(0, 4))
        desc = ctk.CTkLabel(
            self, text="Broadcast AM/FM audio demodulator and tuner (rtl_fm -> sox play). "
                       "Receive-only; requires speakers/headphones and an RTL-SDR dongle.",
            font=ctk.CTkFont(size=13), text_color=theme.text_dim,
            wraplength=700, justify="left",
        )
        desc.pack(anchor="w", pady=(0, 16))

        panel = ctk.CTkFrame(self, fg_color=theme.panel, corner_radius=10)
        panel.pack(fill="x", pady=(0, 16))

        freq_row = ctk.CTkFrame(panel, fg_color="transparent")
        freq_row.pack(fill="x", padx=16, pady=(16, 8))

        ctk.CTkLabel(freq_row, text="Band:").pack(side="left", padx=(0, 6))
        self.band_var = ctk.StringVar(value="FM")
        self.band_menu = ctk.CTkOptionMenu(freq_row, values=["FM", "AM"],
                                            variable=self.band_var, width=80,
                                            command=self._on_band_change)
        self.band_menu.pack(side="left", padx=(0, 20))

        ctk.CTkLabel(freq_row, text="Frequency:").pack(side="left", padx=(0, 6))
        self.freq_entry = ctk.CTkEntry(freq_row, width=110, placeholder_text="101.1")
        self.freq_entry.insert(0, "101.1")
        self.freq_entry.pack(side="left", padx=(0, 6))
        self.freq_unit_label = ctk.CTkLabel(freq_row, text="MHz")
        self.freq_unit_label.pack(side="left", padx=(0, 20))

        self.tune_btn = ctk.CTkButton(freq_row, text="Tune", width=80,
                                       command=self._apply_frequency)
        self.tune_btn.pack(side="left")

        preset_row = ctk.CTkFrame(panel, fg_color="transparent")
        preset_row.pack(fill="x", padx=16, pady=(0, 8))
        ctk.CTkLabel(preset_row, text="Presets:").pack(side="left", padx=(0, 6))
        self.preset_menu = ctk.CTkOptionMenu(
            preset_row,
            values=["FM 88.5", "FM 94.9", "FM 101.1", "FM 107.9", "AM 1010", "AM 1350"],
            command=self._on_preset_selected, width=140,
        )
        self.preset_menu.pack(side="left")

        vol_row = ctk.CTkFrame(panel, fg_color="transparent")
        vol_row.pack(fill="x", padx=16, pady=(0, 8))
        ctk.CTkLabel(vol_row, text="Volume:").pack(side="left", padx=(0, 6))
        self.volume_slider = ctk.CTkSlider(vol_row, from_=0, to=150, number_of_steps=30,
                                            command=self._on_volume_change, width=200)
        self.volume_slider.set(70)
        self.volume_slider.pack(side="left", padx=(0, 10))
        self.volume_label = ctk.CTkLabel(vol_row, text="70%", width=40)
        self.volume_label.pack(side="left")

        ctrl_row = ctk.CTkFrame(panel, fg_color="transparent")
        ctrl_row.pack(fill="x", padx=16, pady=(8, 16))
        self.play_btn = ctk.CTkButton(ctrl_row, text="Play", width=110, height=36,
                                       fg_color=ACCENT_GREEN, hover_color="#2fae45",
                                       text_color="#0a0a0a",
                                       font=ctk.CTkFont(size=14, weight="bold"),
                                       command=self._toggle_play)
        self.play_btn.pack(side="left", padx=(0, 12))
        self.status_label = ctk.CTkLabel(ctrl_row, text="Stopped",
                                          font=ctk.CTkFont(size=13))
        self.status_label.pack(side="left")

    def _get_tuner_and_controller(self):
        from modules.radio import TunerState, AudioTunerController
        if self._tuner is None:
            self._tuner = TunerState()
        if self._controller is None:
            self._controller = AudioTunerController()
        return self._tuner, self._controller

    def _on_band_change(self, value: str) -> None:
        tuner, _ = self._get_tuner_and_controller()
        tuner.mode = value.lower()
        self.freq_unit_label.configure(text="MHz" if value == "FM" else "kHz")
        if value == "FM":
            self.freq_entry.delete(0, "end")
            self.freq_entry.insert(0, "101.1")
        else:
            self.freq_entry.delete(0, "end")
            self.freq_entry.insert(0, "1010")
        self._restart_if_playing()

    def _apply_frequency(self) -> None:
        tuner, _ = self._get_tuner_and_controller()
        text = self.freq_entry.get().strip()
        try:
            value = float(text)
        except ValueError:
            self.console.log(f"Invalid frequency: {text!r}", "error")
            return
        tuner.freq_hz = int(value * 1e6) if tuner.mode == "fm" else int(value * 1e3)
        self.console.log(f"Tuned to {text} {'MHz' if tuner.mode == 'fm' else 'kHz'}.", "info")
        self._restart_if_playing()

    def _on_preset_selected(self, label: str) -> None:
        from modules.radio import PRESETS
        tuner, _ = self._get_tuner_and_controller()
        match = next((p for p in PRESETS if p[0] == label), None)
        if match is None:
            return
        _, freq_hz, mode = match
        tuner.mode = mode
        tuner.freq_hz = freq_hz
        self.band_var.set(mode.upper())
        self.freq_unit_label.configure(text="MHz" if mode == "fm" else "kHz")
        self.freq_entry.delete(0, "end")
        self.freq_entry.insert(0, f"{freq_hz/1e6:.3f}" if mode == "fm" else f"{freq_hz/1e3:.0f}")
        self.console.log(f"Preset selected: {label}", "info")
        self._restart_if_playing()

    def _on_volume_change(self, value: float) -> None:
        tuner, _ = self._get_tuner_and_controller()
        tuner.volume_pct = int(value)
        self.volume_label.configure(text=f"{int(value)}%")
        self._restart_if_playing()

    def _restart_if_playing(self) -> None:
        if self._playing:
            self._start_playback()

    def _toggle_play(self) -> None:
        if self._playing:
            self._stop_playback()
        else:
            self._start_playback()

    def _start_playback(self) -> None:
        tuner, controller = self._get_tuner_and_controller()
        self.play_btn.configure(state="disabled")
        self.console.log("Starting audio pipeline (rtl_fm -> sox play)...", "info")
        self.run_async(self._do_start, self._on_start_done, tuner, controller)

    @staticmethod
    def _do_start(tuner, controller):
        ok = controller.start(tuner)
        return ok, controller.last_error

    def _on_start_done(self, result, error) -> None:
        self.play_btn.configure(state="normal")
        if error is not None:
            self.console.log(f"Playback failed: {error}", "error")
            self.status_label.configure(text="Error", text_color=ACCENT_RED)
            return
        ok, last_error = result
        if ok:
            self._playing = True
            self.play_btn.configure(text="Stop", fg_color=ACCENT_RED, hover_color="#c94a4a")
            self.status_label.configure(text="Playing", text_color=ACCENT_GREEN)
            self.console.log("Audio playback started.", "success")
        else:
            self._playing = False
            self.status_label.configure(text="Stopped", text_color=ACCENT_RED)
            self.console.log(f"Could not start playback: {last_error}", "error")

    def _stop_playback(self) -> None:
        if self._controller is not None:
            self._controller.stop()
        self._playing = False
        self.play_btn.configure(text="Play", fg_color=ACCENT_GREEN, hover_color="#2fae45")
        self.status_label.configure(text="Stopped")
        self.console.log("Audio playback stopped.", "info")

    def shutdown(self) -> None:
        super().shutdown()
        if self._controller is not None:
            self._controller.stop()

    def apply_theme(self, theme: Theme) -> None:
        self.theme = theme
