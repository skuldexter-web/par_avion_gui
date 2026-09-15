"""
gui/components/console.py — Embedded real-time log/terminal panel.

A read-only, colorized scrolling text widget that mimics a terminal:
lines are tagged by level (info/success/warning/error/debug) and colored
accordingly. Backend threads push lines into a thread-safe queue via
LogConsole.log(); the widget drains that queue on the Tk main loop via
`after()`, since Tkinter widgets are not thread-safe and must only be
touched from the main thread.
"""

from __future__ import annotations

import queue
from datetime import datetime

import customtkinter as ctk

from gui.themes.theme import LOG_COLORS, Theme


class LogConsole(ctk.CTkFrame):
    """Embeddable console panel with Clear and Copy buttons."""

    POLL_MS = 80          # how often the queue is drained onto the widget
    MAX_LINES = 2000       # cap so very long sessions don't bloat memory

    def __init__(self, master, theme: Theme, **kwargs):
        super().__init__(master, fg_color=theme.panel, **kwargs)
        self.theme = theme
        self._queue: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self._line_count = 0
        self._all_text_cache: list[str] = []  # plain-text mirror for Copy

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=8, pady=(6, 0))

        title = ctk.CTkLabel(header, text="LIVE LOG / OUTPUT",
                              font=ctk.CTkFont(size=12, weight="bold"),
                              text_color=theme.text_dim)
        title.pack(side="left")

        self.copy_btn = ctk.CTkButton(header, text="Copy Output", width=100,
                                       height=24, command=self._copy_output)
        self.copy_btn.pack(side="right", padx=(6, 0))

        self.clear_btn = ctk.CTkButton(header, text="Clear Logs", width=90,
                                        height=24, command=self.clear)
        self.clear_btn.pack(side="right")

        self.textbox = ctk.CTkTextbox(
            self, fg_color=theme.console_bg, wrap="word",
            font=ctk.CTkFont(family="Consolas", size=12),
            activate_scrollbars=True,
        )
        self.textbox.pack(fill="both", expand=True, padx=8, pady=8)
        self.textbox.configure(state="disabled")

        for level, color in LOG_COLORS.items():
            self.textbox.tag_config(level, foreground=color)

        self._poll_job = self.after(self.POLL_MS, self._drain_queue)

    def log(self, message: str, level: str = "info") -> None:
        """Thread-safe: call this from any thread, including background
        worker threads. The actual widget update happens later on the
        Tk main thread via _drain_queue()."""
        if level not in LOG_COLORS:
            level = "info"
        ts = datetime.now().strftime("%H:%M:%S")
        self._queue.put((f"[{ts}] {message}", level))

    def _drain_queue(self) -> None:
        drained_any = False
        try:
            while True:
                text, level = self._queue.get_nowait()
                self._append_line(text, level)
                drained_any = True
        except queue.Empty:
            pass
        if drained_any:
            self.textbox.see("end")
        self._poll_job = self.after(self.POLL_MS, self._drain_queue)

    def _append_line(self, text: str, level: str) -> None:
        self.textbox.configure(state="normal")
        self.textbox.insert("end", text + "\n", level)
        self._all_text_cache.append(text)
        self._line_count += 1
        if self._line_count > self.MAX_LINES:
            # Trim from the top so memory/widget size doesn't grow
            # unbounded over a long session.
            self.textbox.delete("1.0", "2.0")
            if self._all_text_cache:
                self._all_text_cache.pop(0)
            self._line_count -= 1
        self.textbox.configure(state="disabled")

    def clear(self) -> None:
        self.textbox.configure(state="normal")
        self.textbox.delete("1.0", "end")
        self.textbox.configure(state="disabled")
        self._all_text_cache = []
        self._line_count = 0

    def _copy_output(self) -> None:
        try:
            self.clipboard_clear()
            self.clipboard_append("\n".join(self._all_text_cache))
            self.log("Output copied to clipboard.", "success")
        except Exception as e:
            self.log(f"Could not copy to clipboard: {e}", "error")

    def destroy(self) -> None:
        try:
            self.after_cancel(self._poll_job)
        except Exception:
            pass
        super().destroy()
