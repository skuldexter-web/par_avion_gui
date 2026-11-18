"""
gui/views/tracking_view.py — Shared base for the position-tracking modes.

Airplanes (ADS-B) and Maritime (AIS) differ only in which decoder they
drive and which columns they show; everything else — starting the
feeder process, resolving the operator's position, the radar scope, the
live table, the map underneath, the polling loop and the shutdown
path — is identical, so it lives here once.

Two behaviours are worth calling out, because they are deliberate:

* Nothing is ever plotted at a made-up position. The previous build
  derived a bearing and range from a hash of each callsign whenever
  there was no GPS fix, so the scope looked busy but showed fiction.
  Now a contact is plotted only when it has a real decoded position and
  there is a real reference position to measure from; contacts without
  one still appear in the table, counted and labelled, and the scope
  says why it is empty.

* The reference position comes from GPS if there is a fix and otherwise
  from the home location in Settings, so a station with no GPS dongle
  still gets correct ranges and bearings.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import customtkinter as ctk

from gui.components.map_canvas import MapCanvas, MapContact
from gui.components.radar_canvas import RadarCanvas, RadarContact
from gui.themes.theme import (ACCENT_AMBER, ACCENT_CYAN, ACCENT_GREEN,
                              ACCENT_RED, Theme)
from gui.views.base_view import BaseView, CORE_DIR

sys.path.insert(0, CORE_DIR)

POLL_MS = 1000


@dataclass
class TrackRow:
    """One contact, normalised for display."""
    key: str
    label: str
    lat: Optional[float] = None
    lon: Optional[float] = None
    heading: Optional[float] = None
    sublabel: str = ""
    source: object = None          # the decoder's own record
    distance_nm: Optional[float] = None
    bearing_deg: Optional[float] = None


class TrackingView(BaseView):
    # --- subclass configuration ---------------------------------------
    TITLE = "Tracking"
    DESCRIPTION = ""
    FEED_NAME = "feed"
    CONTACT_NOUN = "Contacts"
    TABLE_TITLE = "LIVE TRAFFIC"
    MAP_TITLE = "MAP"
    COLUMNS: Sequence[Tuple[str, int]] = ()     # (heading, width)
    RANGE_CHOICES = ("Auto", "20", "40", "60", "100", "150")
    DEFAULT_RANGE = "Auto"
    DEFAULT_SPAN_NM = 80.0
    ACCENT = ACCENT_GREEN
    GLYPH = "✈"

    def __init__(self, master, theme: Theme, console, **kwargs):
        super().__init__(master, theme, console, **kwargs)
        self._controller = None
        self._tracker = None
        self._running = False
        self._poll_job = None
        self._ref_lat: Optional[float] = None
        self._ref_lon: Optional[float] = None
        self._ref_source = "none"
        self._auto_range = True
        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        theme = self.theme

        ctk.CTkLabel(self, text=self.TITLE,
                     font=ctk.CTkFont(size=24, weight="bold"),
                     text_color=theme.text).pack(anchor="w", pady=(0, 2))
        ctk.CTkLabel(self, text=self.DESCRIPTION, font=ctk.CTkFont(size=12),
                     text_color=theme.text_dim, wraplength=1000,
                     justify="left").pack(anchor="w", pady=(0, 10))

        ctrl = ctk.CTkFrame(self, fg_color=theme.panel, corner_radius=10)
        ctrl.pack(fill="x", pady=(0, 8))
        inner = ctk.CTkFrame(ctrl, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=10)

        self.start_btn = ctk.CTkButton(inner, text="Start Tracking", width=130,
                                       fg_color=self.ACCENT, hover_color="#2fae45",
                                       text_color="#0a0a0a", command=self._toggle)
        self.start_btn.pack(side="left", padx=(0, 10))

        self.status_label = ctk.CTkLabel(inner, text=f"{self.FEED_NAME}: stopped",
                                         font=ctk.CTkFont(size=12),
                                         text_color=ACCENT_RED)
        self.status_label.pack(side="left", padx=(0, 16))

        ctk.CTkLabel(inner, text="Range:", font=ctk.CTkFont(size=12),
                     text_color=theme.text_dim).pack(side="left")
        self.range_menu = ctk.CTkOptionMenu(inner, values=list(self.RANGE_CHOICES),
                                            width=90, command=self._on_range_change)
        self.range_menu.set(self.DEFAULT_RANGE)
        self.range_menu.pack(side="left", padx=(6, 16))

        self.count_label = ctk.CTkLabel(inner, text=f"{self.CONTACT_NOUN}: 0",
                                        font=ctk.CTkFont(size=12),
                                        text_color=ACCENT_CYAN)
        self.count_label.pack(side="right")

        self.position_label = ctk.CTkLabel(inner, text="position: unknown",
                                           font=ctk.CTkFont(size=11),
                                           text_color=ACCENT_AMBER)
        self.position_label.pack(side="right", padx=(0, 16))

        # Scope + table on top, map underneath.
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True)
        # The scope is square, so its height is what limits it — a wide
        # column just adds black margin. Give the width to the table
        # (which has six columns to fit) and the height to the top row.
        body.grid_columnconfigure(0, weight=2)
        body.grid_columnconfigure(1, weight=3)
        body.grid_rowconfigure(0, weight=5)
        body.grid_rowconfigure(1, weight=4)

        radar_panel = ctk.CTkFrame(body, fg_color=theme.panel, corner_radius=10)
        radar_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=(0, 8))
        self.radar = RadarCanvas(radar_panel, max_range_nm=self.DEFAULT_SPAN_NM,
                                 color=self.ACCENT)
        self.radar.pack(fill="both", expand=True, padx=10, pady=10)

        table_panel = ctk.CTkFrame(body, fg_color=theme.panel, corner_radius=10)
        table_panel.grid(row=0, column=1, sticky="nsew", pady=(0, 8))
        ctk.CTkLabel(table_panel, text=self.TABLE_TITLE,
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=theme.text).pack(anchor="w", padx=14, pady=(12, 6))
        self.table_box = ctk.CTkTextbox(table_panel, fg_color=theme.console_bg,
                                        font=ctk.CTkFont(family="Consolas", size=12))
        self.table_box.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.table_box.configure(state="disabled")

        map_panel = ctk.CTkFrame(body, fg_color=theme.panel, corner_radius=10)
        map_panel.grid(row=1, column=0, columnspan=2, sticky="nsew")
        map_header = ctk.CTkFrame(map_panel, fg_color="transparent")
        map_header.pack(fill="x", padx=14, pady=(10, 4))
        ctk.CTkLabel(map_header, text=self.MAP_TITLE,
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=theme.text).pack(side="left")
        ctk.CTkLabel(map_header,
                     text="offline vector map — no tiles, no API key",
                     font=ctk.CTkFont(size=11),
                     text_color=theme.text_dim).pack(side="left", padx=(10, 0))
        ctk.CTkButton(map_header, text="Recentre", width=90, height=26,
                      command=lambda: self.map.recenter_on_home()).pack(side="right")

        self.map = MapCanvas(map_panel, span_nm=self.DEFAULT_SPAN_NM)
        self.map.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        self._render_table([])
        self._refresh_position_label()
        self.radar.set_message("Press Start Tracking to begin.")

    # ------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------
    def _create_backend(self):
        """-> (controller, tracker, ok, message). Runs on a worker thread."""
        raise NotImplementedError

    def _rows_from_snapshot(self, snapshot) -> List[TrackRow]:
        raise NotImplementedError

    def _columns_for(self, row: "TrackRow") -> Sequence[str]:
        """Table cells for one contact, in COLUMNS order. Called after
        distance/bearing have been filled in, so those can be used."""
        raise NotImplementedError

    def _snapshot(self):
        return self._tracker.snapshot() if self._tracker is not None else []

    def _feed_active(self) -> bool:
        return bool(self._tracker is not None and getattr(self._tracker, "connected", False))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def _toggle(self) -> None:
        if self._running:
            self._stop()
        else:
            self._start()

    def _start(self) -> None:
        self.console.log(f"Starting {self.FEED_NAME} and tracker...", "info")
        self.start_btn.configure(state="disabled")
        self.radar.set_message("Starting...")
        self.run_async(self._start_worker, self._on_started)

    def _start_worker(self):
        from modules import appconfig
        controller, tracker, ok, message = self._create_backend()
        lat, lon, source = appconfig.reference_location()
        return controller, tracker, ok, message, lat, lon, source

    def _on_started(self, result, error) -> None:
        self.start_btn.configure(state="normal")
        if error is not None:
            self.console.log(f"Could not start tracking: {error}", "error")
            self.radar.set_message(f"Start failed: {error}")
            return

        controller, tracker, ok, message, lat, lon, source = result
        self._controller = controller
        self._tracker = tracker
        self._ref_lat, self._ref_lon, self._ref_source = lat, lon, source

        self.console.log(message, "success" if ok else "warning")
        self._refresh_position_label()
        if lat is None:
            self.console.log(
                "No position available — ranges and bearings need one. "
                "Set a home location in Settings, or connect a GPS.", "warning")

        self._running = True
        self.start_btn.configure(text="Stop Tracking", fg_color=ACCENT_RED,
                                 hover_color="#c94a4a")
        self._poll_job = self.after(POLL_MS, self._poll)

    def _stop(self) -> None:
        self._running = False
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except Exception:
                pass
            self._poll_job = None
        if self._tracker is not None:
            try:
                self._tracker.stop()
            except Exception:
                pass
        if self._controller is not None:
            try:
                self._controller.shutdown()
            except Exception:
                pass
        self.start_btn.configure(text="Start Tracking", fg_color=self.ACCENT,
                                 hover_color="#2fae45")
        self.status_label.configure(text=f"{self.FEED_NAME}: stopped",
                                    text_color=ACCENT_RED)
        self.radar.update_contacts([])
        self.radar.set_message("Stopped.")
        self.map.set_contacts([])
        self.count_label.configure(text=f"{self.CONTACT_NOUN}: 0")
        self._render_table([])
        self.console.log("Tracking stopped.", "info")

    # ------------------------------------------------------------------
    # Polling / display
    # ------------------------------------------------------------------
    def _poll(self) -> None:
        if self._tracker is not None:
            try:
                rows = self._rows_from_snapshot(self._snapshot())
            except Exception as e:
                self.console.log(f"Decode error: {e}", "warning")
                rows = []
            self._update_display(rows)
            self._refresh_status()
        if self._running:
            self._poll_job = self.after(POLL_MS, self._poll)

    def _refresh_status(self) -> None:
        if self._feed_active():
            self.status_label.configure(text=f"{self.FEED_NAME}: ACTIVE",
                                        text_color=self.ACCENT)
        elif self._running:
            self.status_label.configure(text=f"{self.FEED_NAME}: waiting for data",
                                        text_color=ACCENT_CYAN)
        else:
            self.status_label.configure(text=f"{self.FEED_NAME}: stopped",
                                        text_color=ACCENT_RED)

    def _refresh_position_label(self) -> None:
        from modules import appconfig
        text = appconfig.describe_location(self._ref_lat, self._ref_lon, self._ref_source)
        self.position_label.configure(
            text=text,
            text_color=ACCENT_CYAN if self._ref_lat is not None else ACCENT_AMBER,
        )
        self.map.set_home(self._ref_lat, self._ref_lon, self._ref_source)

    def _on_range_change(self, value: str) -> None:
        self._auto_range = (value == "Auto")
        if not self._auto_range:
            try:
                span = float(value)
            except ValueError:
                return
            self.radar.set_max_range(span)
            self.map.set_span_nm(span * 2.0)

    def _update_display(self, rows: List[TrackRow]) -> None:
        have_ref = self._ref_lat is not None and self._ref_lon is not None

        positioned = 0
        for row in rows:
            if row.lat is None or row.lon is None:
                continue
            positioned += 1
            if have_ref:
                from modules.mapdata import bearing_deg, haversine_nm
                row.distance_nm = haversine_nm(self._ref_lat, self._ref_lon,
                                               row.lat, row.lon)
                row.bearing_deg = bearing_deg(self._ref_lat, self._ref_lon,
                                              row.lat, row.lon)

        # Scope range follows the furthest contact when set to Auto, so
        # traffic is never clamped onto the outer ring in a heap.
        if self._auto_range and have_ref:
            distances = [r.distance_nm for r in rows if r.distance_nm is not None]
            if distances:
                span = max(5.0, max(distances) * 1.15)
                span = self._round_range(span)
                self.radar.set_max_range(span)
                self.map.set_span_nm(span * 2.0)

        radar_contacts = []
        if have_ref:
            for row in rows:
                if row.distance_nm is None or row.bearing_deg is None:
                    continue
                radar_contacts.append(RadarContact(
                    distance_nm=row.distance_nm, bearing_deg=row.bearing_deg,
                    label_lines=[row.label, row.sublabel] if row.sublabel else [row.label],
                    glyph=self.GLYPH,
                ))
        self.radar.update_contacts(radar_contacts)

        map_contacts = [
            MapContact(lat=row.lat, lon=row.lon, label=row.label,
                       sublabel=row.sublabel, color=self.ACCENT,
                       heading_deg=row.heading, glyph=self.GLYPH)
            for row in rows if row.lat is not None and row.lon is not None
        ]
        self.map.set_contacts(map_contacts)
        if not have_ref and map_contacts:
            # No reference position, but real decoded positions: centre
            # the map on the traffic rather than showing a blank ocean.
            self.map.set_status("No home location — map centred on received traffic")
            self._center_map_on(map_contacts)

        total = len(rows)
        self.count_label.configure(
            text=f"{self.CONTACT_NOUN}: {total}"
                 + (f"  ({positioned} positioned)" if total != positioned else "")
        )

        if not have_ref:
            self.radar.set_message(
                "No reference position — set a home location in Settings "
                "to plot range and bearing.")
        elif total == 0:
            self.radar.set_message(f"No {self.CONTACT_NOUN.lower()} decoded yet.")
        elif positioned == 0:
            self.radar.set_message(
                f"{total} {self.CONTACT_NOUN.lower()} heard, none with a position yet.")
        else:
            self.radar.set_message("")

        self._render_table(rows)

    def _center_map_on(self, contacts) -> None:
        lats = [c.lat for c in contacts]
        lons = [c.lon for c in contacts]
        if not lats:
            return
        self.map._center_lat = sum(lats) / len(lats)
        self.map._center_lon = sum(lons) / len(lons)

    @staticmethod
    def _round_range(span: float) -> float:
        for candidate in (5, 10, 15, 20, 25, 30, 40, 50, 60, 80, 100, 120,
                          150, 200, 250, 300, 400, 500):
            if span <= candidate:
                return float(candidate)
        return float(int(span / 100 + 1) * 100)

    # ------------------------------------------------------------------
    def _header_line(self) -> str:
        return "".join(f"{name:<{width}}" for name, width in self.COLUMNS)

    def _render_table(self, rows: List[TrackRow]) -> None:
        header = self._header_line()
        self.table_box.configure(state="normal")
        self.table_box.delete("1.0", "end")
        self.table_box.insert("end", header + "\n")
        self.table_box.insert("end", "-" * len(header) + "\n")
        for row in rows:
            line = "".join(
                f"{str(value):<{width}}"
                for value, (_name, width) in zip(self._columns_for(row), self.COLUMNS)
            )
            self.table_box.insert("end", line + "\n")
        if not rows:
            self.table_box.insert("end", "\n  (nothing decoded yet)\n")
        self.table_box.configure(state="disabled")

    @staticmethod
    def _fmt(value, spec: str = "{:.0f}", blank: str = "--") -> str:
        if value is None:
            return blank
        try:
            return spec.format(value)
        except (TypeError, ValueError):
            return str(value)

    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        super().shutdown()
        self._running = False
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except Exception:
                pass
        if self._tracker is not None:
            try:
                self._tracker.stop()
            except Exception:
                pass
        if self._controller is not None:
            try:
                self._controller.shutdown()
            except Exception:
                pass
        self.radar.stop_animation()
