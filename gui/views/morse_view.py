"""
gui/views/morse_view.py — Morse/CW mode: native decoded text stream.

Drives core.modules.morse's decoder directly, polling its rolling text
buffer and appending new characters to a live text widget.
"""

from __future__ import annotations

import sys

import customtkinter as ctk

from gui.themes.theme import ACCENT_GREEN, ACCENT_RED, Theme
from gui.views.base_view import BaseView, CORE_DIR

sys.path.insert(0, CORE_DIR)


class MorseView(BaseView):
    POLL_MS = 200

    def __init__(self, master, theme: Theme, console, **kwargs):
        super().__init__(master, theme, console, **kwargs)
        self._decoder = None
        self._capture = None
        self._running = False
        self._poll_job = None
        self._last_text_len = 0

        header = ctk.CTkLabel(self, text="Morse / CW", font=ctk.CTkFont(size=24, weight="bold"), text_color=theme.text)
        header.pack(anchor="w", pady=(0, 4))
        desc = ctk.CTkLabel(
            self, text="CW / Morse code audio decoder with adaptive WPM timing. "
                       "Receive-only; decodes an on/off keyed tone at the "
                       "configured frequency.",
            font=ctk.CTkFont(size=13), text_color=theme.text_dim,
            wraplength=700, justify="left",
        )
        desc.pack(anchor="w", pady=(0, 16))

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True)
        content.grid_columnconfigure(0, weight=1)
        content.grid_columnconfigure(1, weight=0)
        content.grid_rowconfigure(0, weight=1)

        text_panel = ctk.CTkFrame(content, fg_color=theme.panel, corner_radius=10)
        text_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        ctk.CTkLabel(text_panel, text="DECODED TEXT",
                     font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=14, pady=(12, 6))
        self.text_box = ctk.CTkTextbox(text_panel, fg_color=theme.console_bg,
                                        font=ctk.CTkFont(family="Consolas", size=16))
        self.text_box.pack(fill="both", expand=True, padx=14, pady=(0, 10))
        self.text_box.configure(state="disabled")

        clear_row = ctk.CTkFrame(text_panel, fg_color="transparent")
        clear_row.pack(fill="x", padx=14, pady=(0, 14))
        ctk.CTkButton(clear_row, text="Clear Text", width=110, height=28,
                      command=self._clear_text).pack(side="left")

        controls = ctk.CTkFrame(content, fg_color=theme.panel, corner_radius=10, width=260)
        controls.grid(row=0, column=1, sticky="ns")
        controls.grid_propagate(False)

        ctk.CTkLabel(controls, text="CONTROLS", font=ctk.CTkFont(size=12, weight="bold")
                     ).pack(anchor="w", padx=14, pady=(12, 8))

        ctk.CTkLabel(controls, text="Input source:").pack(anchor="w", padx=14)
        self.source_menu = ctk.CTkOptionMenu(controls, values=["rtl_fm (SDR)", "Microphone"], width=200)
        self.source_menu.pack(padx=14, pady=(4, 10))

        ctk.CTkLabel(controls, text="Frequency:").pack(anchor="w", padx=14)
        self.freq_menu = ctk.CTkOptionMenu(
            controls,
            values=["7.030 MHz (40m)", "14.030 MHz (20m)", "3.530 MHz (80m)", "21.030 MHz (15m)"],
            width=200,
        )
        self.freq_menu.pack(padx=14, pady=(4, 10))

        ctk.CTkLabel(controls, text="Tone (Hz):").pack(anchor="w", padx=14)
        self.tone_menu = ctk.CTkOptionMenu(controls, values=["500", "600", "700", "800", "1000"], width=200)
        self.tone_menu.set("700")
        self.tone_menu.pack(padx=14, pady=(4, 14))

        self.start_btn = ctk.CTkButton(controls, text="Start Listening", height=34,
                                        fg_color=ACCENT_GREEN, hover_color="#2fae45",
                                        text_color="#0a0a0a", command=self._toggle_listen)
        self.start_btn.pack(fill="x", padx=14, pady=(0, 14))

        self.status_label = ctk.CTkLabel(controls, text="Idle", font=ctk.CTkFont(size=12),
                                          text_color=theme.text_dim, wraplength=220, justify="left")
        self.status_label.pack(anchor="w", padx=14, pady=(0, 14))

    def _toggle_listen(self) -> None:
        if self._running:
            self._stop_listening()
        else:
            self._start_listening()

    def _start_listening(self) -> None:
        from modules.morse import MorseDecoder, AudioCaptureController

        tone_hz = float(self.tone_menu.get())
        self._decoder = MorseDecoder(tone_hz=tone_hz)
        self._capture = AudioCaptureController()
        self._last_text_len = 0

        use_mic = self.source_menu.get().startswith("Microphone")
        if use_mic:
            ok = self._capture.start_mic(self._decoder)
        else:
            freq_map = {
                "7.030 MHz (40m)": 7_030_000,
                "14.030 MHz (20m)": 14_030_000,
                "3.530 MHz (80m)": 3_530_000,
                "21.030 MHz (15m)": 21_030_000,
            }
            freq_hz = freq_map.get(self.freq_menu.get(), 7_030_000)
            ok = self._capture.start_rtl_fm(freq_hz, self._decoder)

        if not ok:
            self.console.log(f"Could not start Morse capture: {self._capture.last_error}", "error")
            self.status_label.configure(text=self._capture.last_error, text_color=ACCENT_RED)
            return

        self._running = True
        self.start_btn.configure(text="Stop Listening", fg_color=ACCENT_RED, hover_color="#c94a4a")
        self.status_label.configure(text="Listening...", text_color=ACCENT_GREEN)
        self.console.log("Morse capture started.", "success")
        self._poll_job = self.after(self.POLL_MS, self._poll_decoder)

    def _stop_listening(self) -> None:
        if self._decoder is not None:
            self._decoder.flush_pending()
            self._sync_text()
        if self._capture is not None:
            self._capture.stop()
        self._running = False
        self.start_btn.configure(text="Start Listening", fg_color=ACCENT_GREEN, hover_color="#2fae45")
        self.status_label.configure(text="Stopped.", text_color=self.theme.text_dim)
        self.console.log("Morse capture stopped.", "info")
        if self._poll_job is not None:
            self.after_cancel(self._poll_job)
            self._poll_job = None

    def _poll_decoder(self) -> None:
        if self._decoder is not None:
            self._decoder.process()
            self._sync_text()
            wpm = self._decoder.timing.estimated_wpm()
            self.status_label.configure(
                text=f"Listening...\nEst. WPM: {wpm:.0f}\nChars decoded: {self._decoder.total_chars}",
                text_color=ACCENT_GREEN,
            )
        if self._running:
            self._poll_job = self.after(self.POLL_MS, self._poll_decoder)

    def _sync_text(self) -> None:
        if self._decoder is None:
            return
        full_text = self._decoder.text()
        if len(full_text) <= self._last_text_len:
            return
        new_chars = full_text[self._last_text_len:]
        self._last_text_len = len(full_text)
        self.text_box.configure(state="normal")
        self.text_box.insert("end", new_chars)
        self.text_box.see("end")
        self.text_box.configure(state="disabled")

    def _clear_text(self) -> None:
        self.text_box.configure(state="normal")
        self.text_box.delete("1.0", "end")
        self.text_box.configure(state="disabled")
        self._last_text_len = 0

    def shutdown(self) -> None:
        super().shutdown()
        if self._capture is not None:
            self._capture.stop()
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except Exception:
                pass

    def apply_theme(self, theme: Theme) -> None:
        self.theme = theme
