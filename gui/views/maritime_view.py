"""
gui/views/maritime_view.py — Maritime: AIS radar, live table and map.

Same embedded pattern as Airplanes: rtl_ais is started and supervised by
`core.modules.maritime.RtlAisController`, decoded vessels are streamed
onto the scope, the table and the offline map, and nothing opens a
terminal window.

The AIS listener binds its UDP port regardless of whether rtl_ais itself
started, so an AIS feed arriving from elsewhere on the network (another
receiver, a shipboard set, an AIS-over-UDP gateway) is decoded just the
same.
"""

from __future__ import annotations

import sys

from gui.themes.theme import ACCENT_BLUE
from gui.views.base_view import CORE_DIR
from gui.views.tracking_view import TrackingView, TrackRow

sys.path.insert(0, CORE_DIR)


class MaritimeView(TrackingView):
    TITLE = "Maritime"
    DESCRIPTION = ("AIS vessel tracking on 161.975 / 162.025 MHz. Receive-only — "
                   "this decodes the position reports ships already broadcast "
                   "in the clear for collision avoidance.")
    FEED_NAME = "rtl_ais"
    CONTACT_NOUN = "Vessels"
    TABLE_TITLE = "LIVE MARITIME TRAFFIC"
    MAP_TITLE = "MARITIME MAP"
    COLUMNS = (("NAME", 16), ("MMSI", 11), ("SOG", 7), ("COG", 6), ("DIST", 8))
    RANGE_CHOICES = ("Auto", "5", "10", "20", "40", "80")
    DEFAULT_SPAN_NM = 20.0
    ACCENT = ACCENT_BLUE
    GLYPH = "▲"

    def _create_backend(self):
        from modules.maritime import AISTracker, RtlAisController

        controller = RtlAisController()
        ok = controller.ensure_running()
        # The UDP listener starts either way: an AIS stream may be fed in
        # from another machine even when there is no local rtl_ais.
        tracker = AISTracker()
        tracker.start()
        if ok:
            message = "rtl_ais is running. Decoding AIS."
        else:
            message = (f"rtl_ais unavailable ({controller.last_error}). "
                       "Still listening on UDP 10110 for an external AIS feed.")
        return controller, tracker, ok, message

    def _rows_from_snapshot(self, vessels) -> list:
        rows = []
        for v in vessels:
            label = (v.name or "").strip() or v.mmsi
            parts = []
            if v.sog_kt is not None:
                parts.append(f"{v.sog_kt:.1f} kn")
            if v.nav_status:
                parts.append(v.nav_status)
            rows.append(TrackRow(
                key=v.mmsi, label=label[:18], lat=v.lat, lon=v.lon,
                heading=v.cog_deg, sublabel="  ".join(parts), source=v,
            ))
        rows.sort(key=lambda r: (r.lat is None, r.label))
        return rows

    def _columns_for(self, row):
        v = row.source
        return (
            row.label[:15],
            v.mmsi[:10],
            self._fmt(v.sog_kt, "{:.1f}"),
            self._fmt(v.cog_deg, "{:.0f}"),
            self._fmt(row.distance_nm, "{:.1f}"),
        )
