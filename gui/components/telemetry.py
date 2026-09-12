"""
gui/components/telemetry.py — Live CPU/RAM/status telemetry widget.

Polls psutil on a background thread (psutil's calls can block briefly,
particularly cpu_percent's sampling interval) and pushes results back
to the Tk main thread via `after()`, matching the same
thread-safety pattern used by LogConsole: never touch widgets from a
non-Tk thread.
"""

from __future__ import annotations

import queue
import threading
import time

import customtkinter as ctk

try:
    import psutil
    HAVE_PSUTIL = True
except ImportError:
    HAVE_PSUTIL = False

from gui.themes.theme import ACCENT_GREEN, ACCENT_AMBER, ACCENT_RED, Theme


class TelemetryWidget(ctk.CTkFrame):
    """Compact sidebar block showing CPU%, RAM%, and an overall status
    dot. Designed to be cheap enough to poll continuously even on a
    Raspberry Pi — psutil's own overhead is the main cost, sampled on a
    background thread so it never blocks the UI."""

    POLL_INTERVAL_S = 2.0

    def __init__(self, master, theme: Theme, **kwargs):
        super().__init__(master, fg_color=theme.panel, corner_radius=8, **kwargs)
        self.theme = theme
        self._stop = threading.Event()
        self._queue: "queue.Queue[tuple[float, float]]" = queue.Queue()

        title = ctk.CTkLabel(self, text="SYSTEM TELEMETRY",
                              font=ctk.CTkFont(size=11, weight="bold"),
                              text_color=theme.text_dim)
        title.pack(anchor="w", padx=10, pady=(8, 2))

        self.cpu_label = ctk.CTkLabel(self, text="CPU: --%", anchor="w",
                                       font=ctk.CTkFont(size=12))
        self.cpu_label.pack(fill="x", padx=10)
        self.cpu_bar = ctk.CTkProgressBar(self, height=8)
        self.cpu_bar.pack(fill="x", padx=10, pady=(2, 6))
        self.cpu_bar.set(0)

        self.ram_label = ctk.CTkLabel(self, text="RAM: --%", anchor="w",
                                       font=ctk.CTkFont(size=12))
        self.ram_label.pack(fill="x", padx=10)
        self.ram_bar = ctk.CTkProgressBar(self, height=8)
        self.ram_bar.pack(fill="x", padx=10, pady=(2, 8))
        self.ram_bar.set(0)

        if not HAVE_PSUTIL:
            self.cpu_label.configure(text="CPU: psutil not installed")
            self.ram_label.configure(text="RAM: psutil not installed")
            return

        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        self._poll_job = self.after(500, self._drain_queue)

    def _poll_loop(self) -> None:
        # psutil.cpu_percent(interval=X) blocks for X seconds while it
        # samples — that's fine here since it runs on its own thread,
        # never the Tk main thread.
        while not self._stop.is_set():
            try:
                cpu = psutil.cpu_percent(interval=1.0)
                ram = psutil.virtual_memory().percent
                self._queue.put((cpu, ram))
            except Exception:
                pass
            self._stop.wait(self.POLL_INTERVAL_S - 1.0 if self.POLL_INTERVAL_S > 1.0 else 0.1)

    def _drain_queue(self) -> None:
        latest = None
        try:
            while True:
                latest = self._queue.get_nowait()
        except queue.Empty:
            pass
        if latest is not None:
            cpu, ram = latest
            self._update_display(cpu, ram)
        self._poll_job = self.after(500, self._drain_queue)

    def _update_display(self, cpu: float, ram: float) -> None:
        self.cpu_label.configure(text=f"CPU: {cpu:.0f}%")
        self.cpu_bar.set(min(1.0, cpu / 100.0))
        self.cpu_bar.configure(progress_color=self._color_for(cpu))

        self.ram_label.configure(text=f"RAM: {ram:.0f}%")
        self.ram_bar.set(min(1.0, ram / 100.0))
        self.ram_bar.configure(progress_color=self._color_for(ram))

    @staticmethod
    def _color_for(pct: float) -> str:
        if pct >= 90:
            return ACCENT_RED
        if pct >= 70:
            return ACCENT_AMBER
        return ACCENT_GREEN

    def apply_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.configure(fg_color=theme.panel)

    def shutdown(self) -> None:
        """Call before destroying the widget/closing the app so the
        polling thread exits promptly instead of lingering as a daemon
        thread until process exit."""
        self._stop.set()
        try:
            self.after_cancel(self._poll_job)
        except Exception:
            pass

    def destroy(self) -> None:
        self.shutdown()
        super().destroy()
