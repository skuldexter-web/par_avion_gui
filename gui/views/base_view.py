"""
gui/views/base_view.py — Shared base class for all mode views.

Provides the one thing every view needs: a way to run a backend call on
a background thread without freezing the UI. Every mode is a fully
embedded, native GUI widget — none of them spawn an external terminal
or subprocess window; all decoding/tracking runs as a background
thread whose results are streamed into on-screen Canvas/Textbox widgets.
"""

from __future__ import annotations

import os
import threading
from typing import Callable, Optional

import customtkinter as ctk

from gui.themes.theme import Theme

# Path to the core/ package (containing modules/), relative to this
# file's location — used by every view to import backend decoders.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
CORE_DIR = os.path.normpath(os.path.join(_THIS_DIR, "..", "..", "core"))
REPO_ROOT = os.path.normpath(os.path.join(_THIS_DIR, "..", ".."))


class BaseView(ctk.CTkFrame):
    """
    Subclasses implement their own widgets in __init__ (after calling
    super().__init__), and use `self.run_async()` for any backend call
    that might block (hardware scans, subprocess starts, network I/O).
    """

    def __init__(self, master, theme: Theme, console, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.theme = theme
        self.console = console
        self._threads: list[threading.Thread] = []

    def run_async(self, fn: Callable, on_done: Optional[Callable] = None,
                  *args, **kwargs) -> threading.Thread:
        """Run fn(*args, **kwargs) on a background thread. If on_done is
        given, it's scheduled back on the Tk main thread (via `after`)
        with the result once fn returns — never call Tk widget methods
        directly from fn itself."""

        def _worker():
            try:
                result = fn(*args, **kwargs)
                error = None
            except Exception as e:
                result = None
                error = e
            if on_done is not None:
                self.after(0, lambda: on_done(result, error))

        t = threading.Thread(target=_worker, daemon=True)
        self._threads.append(t)
        t.start()
        return t

    def apply_theme(self, theme: Theme) -> None:
        """Override in subclasses that hold their own themed widgets;
        base implementation just updates the stored reference. The
        application is strictly dark-mode only, so this is never
        actually invoked at runtime, but views may still use it as a
        one-time initializer hook."""
        self.theme = theme

    def shutdown(self) -> None:
        """Override in subclasses that own background threads/processes
        needing explicit cleanup on app close (stop trackers, terminate
        spawned decoders like dump1090/rtl_ais/rtl_fm, cancel after()
        polling jobs). No-op by default."""
        pass
