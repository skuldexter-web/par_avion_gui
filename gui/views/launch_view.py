"""
gui/views/launch_view.py — Reusable view template for modes that launch
the existing curses CLI in an external terminal (Airplanes, Waterfalls,
Maritime, ISS — the modes whose UI is a live-updating radar/scope/globe
best served by the already-verified curses renderer rather than a
from-scratch canvas reimplementation).

Each concrete view subclasses this with its own title/description/
keybinding text and any mode-specific controls (e.g. Airplanes' dump1090
start/stop, which calls the real core.modules.airplanes controller
directly for status, while the actual radar view launches in-terminal).
"""

from __future__ import annotations

import customtkinter as ctk

from gui.themes.theme import Theme
from gui.views.base_view import BaseView


class LaunchModeView(BaseView):
    mode_key = "dashboard"       # override in subclass
    mode_label = "Mode"          # override in subclass
    description = ""             # override in subclass
    keybindings: list[tuple[str, str]] = []  # override in subclass

    def __init__(self, master, theme: Theme, console, **kwargs):
        super().__init__(master, theme, console, **kwargs)
        self._build_ui()

    def _build_ui(self) -> None:
        header = ctk.CTkLabel(self, text=self.mode_label,
                               font=ctk.CTkFont(size=24, weight="bold"))
        header.pack(anchor="w", pady=(0, 4))

        if self.description:
            desc = ctk.CTkLabel(self, text=self.description,
                                 font=ctk.CTkFont(size=13),
                                 text_color=self.theme.text_dim,
                                 wraplength=700, justify="left")
            desc.pack(anchor="w", pady=(0, 16))

        panel = ctk.CTkFrame(self, fg_color=self.theme.panel, corner_radius=10)
        panel.pack(fill="x", pady=(0, 16))

        info = ctk.CTkLabel(
            panel,
            text=(
                f"This mode's live radar/scope display runs in the "
                f"proven terminal (curses) interface, opened in its own "
                f"window. Use the button below to launch it — the "
                f"external terminal gives the full-detail live view; "
                f"this panel is for quick access and status."
            ),
            font=ctk.CTkFont(size=12), text_color=self.theme.text_dim,
            wraplength=700, justify="left",
        )
        info.pack(anchor="w", padx=16, pady=(14, 10))

        btn_row = ctk.CTkFrame(panel, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(0, 16))
        self.launch_btn = ctk.CTkButton(
            btn_row, text=f"Launch {self.mode_label}", height=38,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._on_launch,
        )
        self.launch_btn.pack(side="left")

        self._build_extra_controls(panel)

        if self.keybindings:
            kb_label = ctk.CTkLabel(self, text="KEYBINDINGS (inside the launched terminal)",
                                     font=ctk.CTkFont(size=12, weight="bold"))
            kb_label.pack(anchor="w", pady=(4, 6))
            kb_panel = ctk.CTkFrame(self, fg_color=self.theme.panel, corner_radius=10)
            kb_panel.pack(fill="x")
            for key, action in self.keybindings:
                row = ctk.CTkFrame(kb_panel, fg_color="transparent")
                row.pack(fill="x", padx=16, pady=4)
                ctk.CTkLabel(row, text=key, font=ctk.CTkFont(size=12, weight="bold"),
                             width=90, anchor="w").pack(side="left")
                ctk.CTkLabel(row, text=action, font=ctk.CTkFont(size=12),
                             text_color=self.theme.text_dim, anchor="w").pack(side="left")
            ctk.CTkFrame(kb_panel, fg_color="transparent", height=8).pack()

    def _build_extra_controls(self, panel: ctk.CTkFrame) -> None:
        """Hook for subclasses to add mode-specific controls to the
        launch panel (e.g. a start/stop feed button). No-op by default."""
        pass

    def _on_launch(self) -> None:
        self.launch_cli_mode(self.mode_key, self.mode_label)

    def apply_theme(self, theme: Theme) -> None:
        self.theme = theme
        for child in self.winfo_children():
            child.destroy()
        self._build_ui()
