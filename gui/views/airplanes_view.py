"""
gui/views/airplanes_view.py — Airplanes: ADS-B radar, live table and map.

Everything runs inside the window. The ADS-B feeder (dump1090) is
started and supervised by `core.modules.airplanes.Dump1090Controller` —
which prefers the copy built from the vendored source in third_party/,
so the app works on a machine where dump1090 was never installed — and
decoded aircraft are streamed onto the scope, the table and the offline
map by the shared TrackingView base.
"""

from __future__ import annotations

import sys

from gui.themes.theme import ACCENT_GREEN
from gui.views.base_view import CORE_DIR
from gui.views.tracking_view import TrackingView, TrackRow

sys.path.insert(0, CORE_DIR)


class AirplanesView(TrackingView):
    TITLE = "Airplanes"
    DESCRIPTION = ("ADS-B 1090 MHz aircraft tracking. Receive-only — this decodes "
                   "the position reports aircraft already broadcast in the clear "
                   "for collision avoidance.")
    FEED_NAME = "dump1090"
    CONTACT_NOUN = "Aircraft"
    TABLE_TITLE = "LIVE AIR TRAFFIC"
    MAP_TITLE = "AIR TRAFFIC MAP"
    COLUMNS = (("CALLSIGN", 10), ("ALT", 8), ("SPD", 7), ("TRK", 6),
               ("DIST", 8), ("BRG", 5))
    RANGE_CHOICES = ("Auto", "20", "40", "60", "100", "150", "250")
    DEFAULT_SPAN_NM = 80.0
    ACCENT = ACCENT_GREEN
    GLYPH = "✈"

    def _create_backend(self):
        from modules.airplanes import AircraftTracker, Dump1090Controller

        controller = Dump1090Controller()
        ok = controller.ensure_running()
        tracker = None
        if ok:
            tracker = AircraftTracker()
            tracker.start()
            if controller.owns_process:
                message = (f"dump1090 started ({controller.launched_binary}). "
                           "Decoding ADS-B.")
            else:
                message = "dump1090 was already running. Decoding ADS-B."
        else:
            message = (f"Could not start dump1090: {controller.last_error} "
                       "Build the bundled copy with: ./install.sh --dump1090-only")
        return controller, tracker, ok, message

    def _rows_from_snapshot(self, aircraft) -> list:
        rows = []
        for ac in aircraft:
            label = (ac.callsign or "").strip() or ac.icao
            parts = []
            if ac.altitude_ft is not None:
                parts.append(f"{ac.altitude_ft} ft")
            if ac.speed_kt is not None:
                parts.append(f"{ac.speed_kt:.0f} kt")
            rows.append(TrackRow(
                key=ac.icao, label=label, lat=ac.lat, lon=ac.lon,
                heading=ac.heading_deg, sublabel="  ".join(parts), source=ac,
            ))
        # Positioned aircraft first, then alphabetically — the ones you
        # can actually see on the scope belong at the top of the list.
        rows.sort(key=lambda r: (r.lat is None, r.label))
        return rows

    def _columns_for(self, row):
        ac = row.source
        return (
            row.label[:9],
            self._fmt(ac.altitude_ft),
            self._fmt(ac.speed_kt),
            self._fmt(ac.heading_deg),
            self._fmt(row.distance_nm, "{:.1f}"),
            self._fmt(row.bearing_deg, "{:.0f}"),
        )
