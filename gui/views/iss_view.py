"""
gui/views/iss_view.py — ISS Tracker: native pass prediction + launch.

Unlike the radar-style modes, ISS pass data is just a handful of
numbers/timestamps — well suited to real GUI widgets rather than only a
"launch in terminal" button. Calls core.modules.iss directly on a
background thread for TLE fetch + pass prediction.
"""

from __future__ import annotations

import sys

import customtkinter as ctk

from gui.themes.theme import ACCENT_CYAN, ACCENT_GREEN, ACCENT_RED, Theme
from gui.views.base_view import BaseView, CORE_DIR

sys.path.insert(0, CORE_DIR)


class IssView(BaseView):
    def __init__(self, master, theme: Theme, console, **kwargs):
        super().__init__(master, theme, console, **kwargs)
        self._tracker = None
        self._build_ui()

    def _build_ui(self) -> None:
        theme = self.theme
        header = ctk.CTkLabel(self, text="ISS Tracker",
                               font=ctk.CTkFont(size=24, weight="bold"), text_color=theme.text)
        header.pack(anchor="w", pady=(0, 4))
        desc = ctk.CTkLabel(
            self,
            text="NORAD TLE orbit propagation and next-pass prediction, "
                 "using published (public) orbital element data from CelesTrak.",
            font=ctk.CTkFont(size=13), text_color=theme.text_dim,
            wraplength=700, justify="left",
        )
        desc.pack(anchor="w", pady=(0, 16))

        pos_panel = ctk.CTkFrame(self, fg_color=theme.panel, corner_radius=10)
        pos_panel.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(pos_panel, text="CURRENT SUB-SATELLITE POSITION",
                     font=ctk.CTkFont(size=12, weight="bold")
                     ).pack(anchor="w", padx=16, pady=(12, 6))
        self.position_label = ctk.CTkLabel(
            pos_panel, text="Not fetched yet.", font=ctk.CTkFont(size=13),
            text_color=theme.text_dim,
        )
        self.position_label.pack(anchor="w", padx=16, pady=(0, 14))

        pass_panel = ctk.CTkFrame(self, fg_color=theme.panel, corner_radius=10)
        pass_panel.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(pass_panel, text="NEXT PASS (requires GPS fix or manual coordinates)",
                     font=ctk.CTkFont(size=12, weight="bold")
                     ).pack(anchor="w", padx=16, pady=(12, 6))

        coord_row = ctk.CTkFrame(pass_panel, fg_color="transparent")
        coord_row.pack(fill="x", padx=16, pady=(0, 8))
        ctk.CTkLabel(coord_row, text="Latitude:", width=80, anchor="w").pack(side="left")
        self.lat_entry = ctk.CTkEntry(coord_row, width=110, placeholder_text="e.g. 51.5074")
        self.lat_entry.pack(side="left", padx=(0, 16))
        ctk.CTkLabel(coord_row, text="Longitude:", width=80, anchor="w").pack(side="left")
        self.lon_entry = ctk.CTkEntry(coord_row, width=110, placeholder_text="e.g. -0.1278")
        self.lon_entry.pack(side="left")

        self.pass_label = ctk.CTkLabel(
            pass_panel, text="Enter coordinates and press Fetch to compute the next pass.",
            font=ctk.CTkFont(size=13), text_color=theme.text_dim, justify="left",
        )
        self.pass_label.pack(anchor="w", padx=16, pady=(4, 14))

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", pady=(0, 4))
        self.fetch_btn = ctk.CTkButton(btn_row, text="Fetch TLE & Predict Pass",
                                        height=36, command=self._fetch)
        self.fetch_btn.pack(side="left", padx=(0, 8))

    def _fetch(self) -> None:
        lat_text = self.lat_entry.get().strip()
        lon_text = self.lon_entry.get().strip()
        try:
            lat = float(lat_text) if lat_text else None
            lon = float(lon_text) if lon_text else None
        except ValueError:
            self.console.log("Latitude/longitude must be numbers.", "error")
            return

        self.fetch_btn.configure(state="disabled", text="Fetching...")
        self.console.log("Fetching ISS TLE from CelesTrak...", "info")
        self.run_async(self._do_fetch, self._on_fetch_done, lat, lon)

    @staticmethod
    def _do_fetch(lat, lon):
        from modules.iss import ISSTracker
        tracker = ISSTracker(observer_lat=lat or 0.0, observer_lon=lon or 0.0)
        sub_lat, sub_lon, alt_km = tracker.current_subpoint()
        pass_pred = tracker.next_pass() if (lat is not None and lon is not None) else None
        return tracker, sub_lat, sub_lon, alt_km, pass_pred

    def _on_fetch_done(self, result, error) -> None:
        self.fetch_btn.configure(state="normal", text="Fetch TLE & Predict Pass")
        if error is not None:
            self.console.log(f"ISS fetch failed: {error}", "error")
            self.position_label.configure(text=f"Error: {error}", text_color=ACCENT_RED)
            return

        tracker, sub_lat, sub_lon, alt_km, pass_pred = result
        self._tracker = tracker

        if sub_lat is not None:
            self.position_label.configure(
                text=f"Lat {sub_lat:+.2f}, Lon {sub_lon:+.2f}   Altitude: {alt_km:.1f} km",
                text_color=ACCENT_CYAN,
            )
            self.console.log("ISS position updated.", "success")
        else:
            self.position_label.configure(text="Position unavailable (TLE fetch may have failed; "
                                                 "check network connectivity).", text_color=ACCENT_RED)
            self.console.log("Could not determine ISS position.", "warning")

        if pass_pred is None:
            self.pass_label.configure(
                text="Enter latitude and longitude above to compute the next pass.",
                text_color=self.theme.text_dim,
            )
        elif pass_pred.rise_time is None:
            self.pass_label.configure(text="No pass found in the next 48 hours for this location.",
                                       text_color=self.theme.text_dim)
        else:
            elev = f"  (max elevation {pass_pred.max_elevation_deg:.0f} deg)" if pass_pred.max_elevation_deg else ""
            self.pass_label.configure(
                text=(f"Rise: {pass_pred.rise_time.strftime('%Y-%m-%d %H:%M UTC')}\n"
                      f"Peak: {pass_pred.culminate_time.strftime('%H:%M UTC') if pass_pred.culminate_time else '--'}{elev}\n"
                      f"Set:  {pass_pred.set_time.strftime('%H:%M UTC') if pass_pred.set_time else '--'}"),
                text_color=ACCENT_GREEN,
            )
            self.console.log("Next ISS pass computed.", "success")

    def apply_theme(self, theme: Theme) -> None:
        self.theme = theme
        # NEVER call self.__init__() again here — re-running CTkFrame's
        # constructor on an already-placed widget silently clears its
        # own grid placement in its parent, leaving the whole view
        # unmapped (invisible) after the "rebuild". Clear children and
        # re-run the widget-construction method only.
        for child in self.winfo_children():
            child.destroy()
        self._build_ui()
