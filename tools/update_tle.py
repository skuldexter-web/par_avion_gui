#!/usr/bin/env python3
"""
tools/update_tle.py — Refresh the cached ISS orbital elements.

Fetches a current two-line element set from the public keyless sources
(CelesTrak, then ARISS, then AMSAT) and writes it to
``~/.par_avion/cache/iss.tle``, which PAR AVION prefers over the
elements bundled in the repository.

    python3 tools/update_tle.py           # fetch and cache
    python3 tools/update_tle.py --status  # just report what's in use

install.sh runs this once at the end of a normal install, so a machine
that is online at install time is fully self-sufficient afterwards. On
an air-gapped machine, paste elements into the ISS tab's "Manual TLE"
box instead — no network required at all.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core")))

from modules import appconfig, iss  # noqa: E402


def describe(tle, label: str) -> None:
    if tle is None:
        print(f"  {label:<10} (none)")
        return
    epoch = tle.epoch
    stamp = epoch.strftime("%Y-%m-%d %H:%M UTC") if epoch else "unreadable epoch"
    print(f"  {label:<10} epoch {stamp}  ({tle.age_text()})")


def main(argv) -> int:
    status_only = "--status" in argv

    print("PAR AVION — ISS orbital elements")
    print(f"  cache file: {appconfig.cache_path(iss.CACHED_TLE_NAME)}")
    describe(iss.load_bundled_tle(), "bundled:")
    describe(iss.load_cached_tle(), "cached:")

    if status_only:
        tracker = iss.ISSTracker(allow_network=False)
        print(f"\nIn use: {tracker.element_source_text()}")
        print(f"  {tracker.tle.quality_note() if tracker.tle else 'no elements available'}")
        return 0

    print("\nFetching fresh elements...")
    tle, message = iss.fetch_tle()
    print(f"  {message}")
    if tle is None:
        print("\nNo network? That is fine — PAR AVION falls back to the cached or")
        print("bundled elements, and you can paste a current element set straight")
        print("into the ISS tab. Sources, none of which need an account or key:")
        for name, url in iss.TLE_SOURCES:
            print(f"  {name}: {url}")
        return 1

    ok, reason = iss.validate_tle(tle.line1, tle.line2)
    if not ok:
        print(f"  Rejected: {reason}")
        return 1

    if iss.save_cached_tle(tle):
        print(f"  Cached to {appconfig.cache_path(iss.CACHED_TLE_NAME)}")
    else:
        print("  WARNING: could not write the cache file (permissions?).")
        return 1

    describe(tle, "now:")
    print(f"  {tle.quality_note()}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
