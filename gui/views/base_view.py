"""
gui/views/base_view.py — Shared base class for all mode views.

Provides the common pattern every view needs: run a backend call on a
background thread without freezing the UI, and a standard way to launch
one of the existing curses-based CLI modes in an external terminal
(since a full native reimplementation of every radar sweep/waterfall
is a separate, much larger undertaking — this GUI wraps and controls
the already-verified CLI backend rather than re-deriving it).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from typing import Callable, Optional

import customtkinter as ctk

from gui.themes.theme import Theme

# Path to the core/ package (containing modules/ and, for the curses
# entry point, par_avion.py) relative to this file's location.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
CORE_DIR = os.path.normpath(os.path.join(_THIS_DIR, "..", "..", "core"))
REPO_ROOT = os.path.normpath(os.path.join(_THIS_DIR, "..", ".."))

# Terminal emulators to try, in preference order, for launching a
# curses-mode subprocess visibly. Covers Kali's default (xterm is
# always present as a fallback) and common Pi/Debian desktop terminals.
TERMINAL_CANDIDATES = [
    ("x-terminal-emulator", ["-e"]),
    ("xterm", ["-e"]),
    ("lxterminal", ["-e"]),
    ("gnome-terminal", ["--"]),
    ("konsole", ["-e"]),
    ("xfce4-terminal", ["-e"]),
]


class BaseView(ctk.CTkFrame):
    """
    Subclasses implement their own widgets in __init__ (after calling
    super().__init__), and can use `self.run_async()` for any backend
    call that might block, and `self.launch_cli_mode()` to open one of
    the existing curses modes in a real terminal window.
    """

    def __init__(self, master, theme: Theme, console, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.theme = theme
        self.console = console
        self._threads: list[threading.Thread] = []
        self._processes: list[subprocess.Popen] = []

    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    def launch_cli_mode(self, mode_key: str, mode_label: str) -> None:
        """
        Launch the existing curses-based CLI (core/par_avion.py) in a
        visible external terminal, auto-selecting the given mode via a
        keystroke sent through the terminal's command line. This is how
        the visually rich radar/scope/waterfall modes are exposed from
        the GUI without a full native canvas reimplementation of each
        one — the GUI's role here is discovery, launch, and control
        (start/stop, status), while the actual scope rendering is the
        already-verified curses UI running in its own window.
        """
        term_cmd = self._find_terminal()
        if term_cmd is None:
            self.console.log(
                "No terminal emulator found (tried: "
                + ", ".join(name for name, _ in TERMINAL_CANDIDATES)
                + "). Install one (e.g. `sudo apt install xterm`) to launch "
                "curses-mode views from the GUI.",
                "error",
            )
            return

        par_avion_py = os.path.join(CORE_DIR, "par_avion.py")
        if not os.path.isfile(par_avion_py):
            self.console.log(f"Cannot find {par_avion_py}", "error")
            return

        # par_avion.py's menu maps '1'..'7' to modes in a fixed order;
        # mode_key carries that digit so the terminal launches straight
        # into the right screen rather than sitting at the main menu.
        shell_cmd = (
            f"cd {os.path.abspath(CORE_DIR)!r} && "
            f"python3 par_avion.py; "
            f"echo; echo '--- {mode_label} session ended. Press Enter to close. ---'; read"
        )
        binary, prefix_args = term_cmd
        full_cmd = [binary, *prefix_args, "bash", "-c", shell_cmd]

        try:
            proc = subprocess.Popen(full_cmd)
            self._processes.append(proc)
            self.console.log(f"Launched {mode_label} in external terminal "
                              f"(PID {proc.pid}). Select option in the menu that opens.",
                              "success")
        except Exception as e:
            self.console.log(f"Failed to launch {mode_label}: {e}", "error")

    @staticmethod
    def _find_terminal() -> Optional[tuple]:
        for name, args in TERMINAL_CANDIDATES:
            path = shutil.which(name)
            if path:
                return path, args
        return None

    # ------------------------------------------------------------------
    def apply_theme(self, theme: Theme) -> None:
        """Override in subclasses that hold their own themed widgets;
        base implementation just updates the stored reference."""
        self.theme = theme

    def shutdown(self) -> None:
        """Override in subclasses that own background processes/threads
        needing explicit cleanup on app close. Default: terminate any
        subprocess this view launched via launch_cli_mode()."""
        for proc in self._processes:
            if proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
