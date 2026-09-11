#!/usr/bin/env python3
"""
main.py — Universal entry point for PAR AVION GUI.

Launches the CustomTkinter application. Run from the repository root
(or via the install.sh-generated desktop launcher / venv):

    python3 main.py
"""

from __future__ import annotations

import os
import sys

# Make both core/ (backend modules) and the repo root (for `gui`)
# importable regardless of the working directory this is launched from.
_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR = os.path.join(_REPO_ROOT, "core")
for path in (_REPO_ROOT, _CORE_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)


def _check_dependencies() -> list[str]:
    missing = []
    try:
        import customtkinter  # noqa: F401
    except ImportError:
        missing.append("customtkinter")
    try:
        import tkinter  # noqa: F401
    except ImportError:
        missing.append("tkinter (install the 'python3-tk' system package)")
    return missing


def main() -> int:
    missing = _check_dependencies()
    if missing:
        print("PAR AVION GUI cannot start — missing dependencies:")
        for m in missing:
            print(f"  - {m}")
        print("\nRun install.sh, or: pip install -r requirements.txt")
        return 1

    from gui.app import ParAvionApp

    app = ParAvionApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
