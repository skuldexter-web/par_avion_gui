#!/usr/bin/env python3
"""
tools/iss_selftest.py — Verify ISS tracking works offline, with no API
key and no network access.

Runs the propagator against the bundled/cached elements with networking
explicitly disabled and checks the results against physical reality:
the ISS orbits at roughly 400-430 km, moves at about 7.66 km/s, circles
the Earth every ~93 minutes, and a mid-latitude observer sees several
passes a day lasting up to ~10 minutes.

    python3 tools/iss_selftest.py [lat lon]

Exit status is non-zero if any check fails.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core")))

from modules import iss  # noqa: E402

CHECKS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def main(argv) -> int:
    lat = float(argv[1]) if len(argv) > 2 else 52.3676
    lon = float(argv[2]) if len(argv) > 2 else 4.9041

    print("PAR AVION — ISS tracker self-test (offline, no API key)")
    print(f"  observer: {lat:+.4f}, {lon:+.4f}\n")

    if not iss.HAVE_SGP4:
        print("  sgp4 is not installed — run: pip install sgp4")
        return 1

    tracker = iss.ISSTracker(observer_lat=lat, observer_lon=lon, allow_network=False)
    check("elements loaded without network", tracker.ready,
          tracker.element_source_text())
    if not tracker.ready:
        return 1
    print(f"         {tracker.tle.quality_note()}\n")

    sp = tracker.subpoint()
    check("sub-satellite point computed", sp is not None)
    if sp is None:
        return 1
    print(f"         lat {sp.lat:+.3f}  lon {sp.lon:+.3f}  alt {sp.alt_km:.1f} km  "
          f"{sp.velocity_kms:.3f} km/s  {'sunlit' if sp.sunlit else 'eclipsed'}")
    check("altitude is plausible for the ISS (370-460 km)",
          370.0 <= sp.alt_km <= 460.0, f"{sp.alt_km:.1f} km")
    check("orbital speed is plausible (7.5-7.8 km/s)",
          7.5 <= sp.velocity_kms <= 7.8, f"{sp.velocity_kms:.3f} km/s")
    check("latitude within the ISS inclination (±51.7°)",
          abs(sp.lat) <= 52.5, f"{sp.lat:+.2f}°")

    # One orbital period should bring it back near the same latitude,
    # shifted west by the Earth's rotation during that time.
    period_min = 1440.0 / float(tracker.tle.line2[52:63])
    later = tracker.subpoint(datetime.now(timezone.utc) + timedelta(minutes=period_min))
    check("orbital period from elements is ~90-95 min",
          88.0 <= period_min <= 96.0, f"{period_min:.2f} min")
    check("position repeats after one period",
          later is not None and abs(later.lat - sp.lat) < 2.0,
          f"lat {sp.lat:+.2f} -> {later.lat:+.2f}" if later else "")

    track = tracker.ground_track(0, period_min, 60)
    check("ground track covers a full orbit", len(track) > 80, f"{len(track)} points")
    if track:
        lats = [p[0] for p in track]
        check("ground track reaches both hemispheres",
              max(lats) > 40 and min(lats) < -40,
              f"lat range {min(lats):+.1f} to {max(lats):+.1f}")

    passes = tracker.next_passes(count=6, horizon_deg=10.0, search_hours=48)
    check("passes predicted for the observer", len(passes) > 0,
          f"{len(passes)} in the next 48 h")
    for p in passes[:6]:
        print(f"         {p.rise_time:%Y-%m-%d %H:%M:%S} UTC  "
              f"az {p.rise_azimuth_deg:5.1f}->{p.set_azimuth_deg:5.1f}  "
              f"max el {p.max_elevation_deg:4.1f}°  {p.duration_text():>8}  "
              f"{'visible' if p.visible else 'not visible'}")
    if passes:
        durations = [p.duration_s for p in passes]
        elevations = [p.max_elevation_deg for p in passes]
        check("pass durations are physical (30 s - 12 min)",
              all(30 <= d <= 720 for d in durations),
              f"{min(durations):.0f}-{max(durations):.0f} s")
        check("peak elevations above the search horizon",
              all(e >= 9.9 for e in elevations),
              f"{min(elevations):.1f}-{max(elevations):.1f}°")
        check("passes are in chronological order",
              all(passes[i].rise_time < passes[i + 1].rise_time
                  for i in range(len(passes) - 1)))

    # Geometry sanity: a satellite directly overhead must be at ~90° and
    # at a slant range equal to its altitude.
    overhead = iss.ISSTracker(observer_lat=sp.lat, observer_lon=sp.lon,
                              allow_network=False)
    la = overhead.look_angles()
    check("observer under the ISS sees it near the zenith",
          la is not None and la[1] > 88.0, f"elevation {la[1]:.2f}°" if la else "")
    check("slant range at zenith matches altitude",
          la is not None and abs(la[2] - sp.alt_km) < 5.0,
          f"range {la[2]:.1f} km vs altitude {sp.alt_km:.1f} km" if la else "")

    failed = [c for c in CHECKS if not c[1]]
    print()
    if failed:
        print(f"{len(failed)} check(s) FAILED.")
        return 1
    print(f"All {len(CHECKS)} checks passed — ISS tracking works offline.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
