"""
gui/views/sstv_view.py — SSTV mode: native decode progress + image preview.

Drives core.modules.sstv's decoder directly on a background thread and
renders the in-progress decoded image on a Tkinter Canvas, polling the
decoder's partial image buffer periodically — since SSTV images decode
line-by-line over many seconds, a live-updating preview is much more
useful here than a static "launch in terminal" button.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

import customtkinter as ctk

from gui.themes.theme import ACCENT_GREEN, ACCENT_RED, Theme
from gui.views.base_view import BaseView, CORE_DIR

sys.path.insert(0, CORE_DIR)

try:
    from PIL import Image, ImageTk
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False


class SstvView(BaseView):
    POLL_MS = 500

    def __init__(self, master, theme: Theme, console, **kwargs):
        super().__init__(master, theme, console, **kwargs)
        self._decoder = None
        self._capture = None
        self._running = False
        self._poll_job = None
        self._tk_image = None

        header = ctk.CTkLabel(self, text="SSTV", font=ctk.CTkFont(size=24, weight="bold"), text_color=theme.text)
        header.pack(anchor="w", pady=(0, 4))
        desc = ctk.CTkLabel(
            self, text="Slow Scan TV image decoder (Martin, Scottie, Robot modes). "
                       "Receive-only; listens for the VIS header on the tuned "
                       "frequency or microphone input.",
            font=ctk.CTkFont(size=13), text_color=theme.text_dim,
            wraplength=700, justify="left",
        )
        desc.pack(anchor="w", pady=(0, 16))

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True)
        content.grid_columnconfigure(0, weight=1)
        content.grid_columnconfigure(1, weight=0)
        content.grid_rowconfigure(0, weight=1)

        preview_panel = ctk.CTkFrame(content, fg_color=theme.panel, corner_radius=10)
        preview_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        ctk.CTkLabel(preview_panel, text="LIVE PREVIEW",
                     font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=14, pady=(12, 6))
        self.canvas = ctk.CTkCanvas(preview_panel, bg="#0a0a0a", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        # The canvas has no real width/height yet at this point — Tk's
        # geometry manager hasn't laid it out on screen — so drawing the
        # placeholder text immediately would center it against a
        # near-zero (unrealized) size and land it in the top-left
        # corner instead of the middle. Deferring one tick via after_idle
        # lets Tk finish the initial layout pass first.
        self.after_idle(lambda: self._draw_placeholder("Listening for VIS header..."))
        # Re-center the placeholder if the window is resized before any
        # image has started decoding (once decoding starts, _render_preview
        # takes over and this binding becomes a no-op for real image frames).
        self.canvas.bind("<Configure>", self._on_canvas_resize)

        controls = ctk.CTkFrame(content, fg_color=theme.panel, corner_radius=10, width=260)
        controls.grid(row=0, column=1, sticky="ns")
        controls.grid_propagate(False)

        ctk.CTkLabel(controls, text="CONTROLS", font=ctk.CTkFont(size=12, weight="bold")
                     ).pack(anchor="w", padx=14, pady=(12, 8))

        ctk.CTkLabel(controls, text="Input source:").pack(anchor="w", padx=14)
        self.source_menu = ctk.CTkOptionMenu(controls, values=["rtl_fm (SDR)", "Microphone"],
                                              width=200)
        self.source_menu.pack(padx=14, pady=(4, 10))

        ctk.CTkLabel(controls, text="Frequency:").pack(anchor="w", padx=14)
        self.freq_menu = ctk.CTkOptionMenu(
            controls,
            values=["14.230 MHz (HF)", "145.800 MHz (ISS)", "144.500 MHz (2m R1)"],
            width=200,
        )
        self.freq_menu.pack(padx=14, pady=(4, 14))

        self.start_btn = ctk.CTkButton(controls, text="Start Listening", height=34,
                                        fg_color=ACCENT_GREEN, hover_color="#2fae45",
                                        text_color="#0a0a0a", command=self._toggle_listen)
        self.start_btn.pack(fill="x", padx=14, pady=(0, 8))

        self.save_btn = ctk.CTkButton(controls, text="Save Image", height=34,
                                       command=self._save_image, state="disabled")
        self.save_btn.pack(fill="x", padx=14, pady=(0, 8))

        self.reset_btn = ctk.CTkButton(controls, text="Reset", height=34,
                                        command=self._reset)
        self.reset_btn.pack(fill="x", padx=14, pady=(0, 14))

        self.status_label = ctk.CTkLabel(controls, text="Idle", font=ctk.CTkFont(size=12),
                                          text_color=theme.text_dim, wraplength=220, justify="left")
        self.status_label.pack(anchor="w", padx=14, pady=(0, 14))

    def _draw_placeholder(self, text: str) -> None:
        self.canvas.delete("all")
        w = self.canvas.winfo_width() or 400
        h = self.canvas.winfo_height() or 300
        self.canvas.create_text(w // 2, h // 2, text=text, fill="#666666",
                                 font=("Arial", 13))

    def _on_canvas_resize(self, event) -> None:
        # Only re-center the placeholder text when there's no decoded
        # image on screen yet — once a real image is being rendered,
        # _render_preview owns the canvas contents and redraws on its
        # own poll cycle, so this resize handler should leave it alone.
        has_image = (
            self._decoder is not None
            and self._decoder.image is not None
            and self._decoder.current_row > 0
        )
        if not has_image:
            self._draw_placeholder("Listening for VIS header...")

    def _toggle_listen(self) -> None:
        if self._running:
            self._stop_listening()
        else:
            self._start_listening()

    def _start_listening(self) -> None:
        from modules.sstv import SstvDecoder, AudioCaptureController

        if not HAVE_PIL:
            self.console.log("Pillow (PIL) not installed — SSTV preview requires it. "
                              "Install with: pip install Pillow", "warning")

        self._decoder = SstvDecoder()
        self._capture = AudioCaptureController()

        use_mic = self.source_menu.get().startswith("Microphone")
        if use_mic:
            ok = self._capture.start_mic(self._decoder)
        else:
            freq_map = {
                "14.230 MHz (HF)": 14_230_000,
                "145.800 MHz (ISS)": 145_800_000,
                "144.500 MHz (2m R1)": 144_500_000,
            }
            freq_hz = freq_map.get(self.freq_menu.get(), 14_230_000)
            ok = self._capture.start_rtl_fm(freq_hz, self._decoder)

        if not ok:
            self.console.log(f"Could not start SSTV capture: {self._capture.last_error}", "error")
            self.status_label.configure(text=self._capture.last_error, text_color=ACCENT_RED)
            return

        self._running = True
        self.start_btn.configure(text="Stop Listening", fg_color=ACCENT_RED, hover_color="#c94a4a")
        self.status_label.configure(text="Listening for VIS header...", text_color=ACCENT_GREEN)
        self.console.log("SSTV capture started.", "success")
        self._poll_job = self.after(self.POLL_MS, self._poll_decoder)

    def _stop_listening(self) -> None:
        if self._capture is not None:
            self._capture.stop()
        self._running = False
        self.start_btn.configure(text="Start Listening", fg_color=ACCENT_GREEN, hover_color="#2fae45")
        self.status_label.configure(text="Stopped.", text_color=self.theme.text_dim)
        self.console.log("SSTV capture stopped.", "info")
        if self._poll_job is not None:
            self.after_cancel(self._poll_job)
            self._poll_job = None

    def _reset(self) -> None:
        if self._decoder is not None:
            self._decoder.state = "listening"
            self._decoder.mode = None
            self._decoder.image = None
            self._decoder.current_row = 0
        self._draw_placeholder("Listening for VIS header...")
        self.save_btn.configure(state="disabled")
        self.status_label.configure(text="Reset — listening for a new VIS header.",
                                     text_color=self.theme.text_dim)

    def _poll_decoder(self) -> None:
        if self._decoder is not None:
            self._decoder.process()
            state, mode, image, current_row = self._decoder.snapshot()
            self._render_preview(state, mode, image, current_row)
        if self._running:
            self._poll_job = self.after(self.POLL_MS, self._poll_decoder)

    def _render_preview(self, state, mode, image, current_row) -> None:
        if image is None:
            return
        if mode is not None:
            pct = int(100 * current_row / mode.image_lines) if mode.image_lines else 0
            self.status_label.configure(
                text=f"Mode: {mode.name}\nProgress: {current_row}/{mode.image_lines} lines ({pct}%)",
                text_color=ACCENT_GREEN,
            )
            if state == "done":
                self.save_btn.configure(state="normal")
                self.console.log(f"SSTV decode complete: {mode.name}.", "success")

        if not HAVE_PIL or image.pixels is None or current_row == 0:
            return

        canvas_w = self.canvas.winfo_width() or 400
        try:
            pil_img = Image.fromarray(image.pixels[:current_row], "RGB")
            pil_img = pil_img.resize((canvas_w, max(1, int(canvas_w * current_row / image.width))),
                                      Image.NEAREST)
            self._tk_image = ImageTk.PhotoImage(pil_img)
            self.canvas.delete("all")
            self.canvas.create_image(0, 0, anchor="nw", image=self._tk_image)
        except Exception as e:
            self.console.log(f"Preview render error: {e}", "warning")

    def _save_image(self) -> None:
        if self._decoder is None or self._decoder.image is None:
            return
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        mode_slug = self._decoder.mode.name.lower().replace(" ", "") if self._decoder.mode else "sstv"
        capture_dir = os.path.join(CORE_DIR, "captures", "sstv")
        os.makedirs(capture_dir, exist_ok=True)
        path = os.path.join(capture_dir, f"{mode_slug}-{ts}.png")
        ok = self._decoder.image.save_png(path)
        if ok:
            self.console.log(f"Image saved: {path}", "success")
        else:
            self.console.log("Failed to save image.", "error")

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
