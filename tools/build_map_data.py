#!/usr/bin/env python3
"""
tools/build_map_data.py — Convert Natural Earth GeoJSON into the compact
map files PAR AVION ships in data/map/.

You do not need to run this to use PAR AVION: the converted files are
already in the repository. It exists so the bundled map data is
reproducible rather than a mystery blob, and so anyone can rebuild it
(at a different detail level, or for a different region) from the
original public-domain source.

    # fetch the sources (needs network, no account or key)
    python3 tools/build_map_data.py --fetch /tmp/ne
    # convert them into data/map/
    python3 tools/build_map_data.py --build /tmp/ne

Source: Natural Earth (naturalearthdata.com), public domain, mirrored at
github.com/nvkelso/natural-earth-vector.

Output format — one gzipped JSON object per layer:

    {"name": ..., "detail": "50m", "tolerance_deg": ...,
     "paths": [[lon, lat, lon, lat, ...], ...]}

Coordinates are flat lon/lat pairs rounded to a fixed number of decimal
places, which is both smaller and faster to load than nested GeoJSON.
"""

from __future__ import annotations

import gzip
import json
import math
import os
import sys
import urllib.request

NE_BASE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson"

# (source layer, output name, simplification tolerance in degrees, decimals)
# Tolerances are chosen so each layer stays visually clean at the zoom it
# is used for: ~2 km for the world view, ~500 m for the regional view,
# ~120 m for the close-in view a radar range circle sits on.
LAYERS = [
    ("ne_110m_land", "land_110m", 0.02, 3),
    ("ne_50m_land", "land_50m", 0.005, 4),
    ("ne_110m_coastline", "coastline_110m", 0.02, 3),
    ("ne_110m_admin_0_countries", "countries_110m", 0.02, 3),
    ("ne_50m_coastline", "coastline_50m", 0.005, 4),
    ("ne_50m_admin_0_countries", "countries_50m", 0.005, 4),
    ("ne_50m_lakes", "lakes_50m", 0.005, 4),
    ("ne_10m_coastline", "coastline_10m", 0.001, 4),
]

OUT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "map")
)


def fetch(dest: str) -> int:
    os.makedirs(dest, exist_ok=True)
    for source, _name, _tol, _dec in LAYERS:
        url = f"{NE_BASE}/{source}.geojson"
        path = os.path.join(dest, f"{source}.geojson")
        if os.path.exists(path):
            print(f"  have {source}")
            continue
        print(f"  fetching {source} ...", end="", flush=True)
        urllib.request.urlretrieve(url, path)
        print(f" {os.path.getsize(path) / 1e6:.1f} MB")
    return 0


def _rings(geometry):
    """Yield coordinate rings from any GeoJSON geometry type."""
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if gtype == "LineString":
        yield coords
    elif gtype == "MultiLineString":
        yield from coords
    elif gtype == "Polygon":
        yield from coords
    elif gtype == "MultiPolygon":
        for poly in coords:
            yield from poly
    elif gtype == "GeometryCollection":
        for sub in geometry.get("geometries", []):
            yield from _rings(sub)


def simplify(points, tolerance: float):
    """Ramer-Douglas-Peucker, iterative so deep coastlines can't blow the
    Python recursion limit."""
    if len(points) < 3 or tolerance <= 0:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]

    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue
        x1, y1 = points[start]
        x2, y2 = points[end]
        dx, dy = x2 - x1, y2 - y1
        norm = math.hypot(dx, dy)
        best_i, best_d = -1, 0.0
        for i in range(start + 1, end):
            px, py = points[i]
            if norm == 0:
                dist = math.hypot(px - x1, py - y1)
            else:
                dist = abs(dy * px - dx * py + x2 * y1 - y2 * x1) / norm
            if dist > best_d:
                best_d, best_i = dist, i
        if best_d > tolerance and best_i > 0:
            keep[best_i] = True
            stack.append((start, best_i))
            stack.append((best_i, end))

    return [p for p, k in zip(points, keep) if k]


def build(src_dir: str) -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    total_in = total_out = 0

    for source, name, tolerance, decimals in LAYERS:
        path = os.path.join(src_dir, f"{source}.geojson")
        if not os.path.exists(path):
            print(f"  SKIP {name}: {path} not found (run --fetch first)")
            continue
        total_in += os.path.getsize(path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        paths = []
        raw_points = kept_points = 0
        for feature in data.get("features", []):
            geometry = feature.get("geometry") or {}
            for ring in _rings(geometry):
                pts = [(float(c[0]), float(c[1])) for c in ring if len(c) >= 2]
                if len(pts) < 2:
                    continue
                raw_points += len(pts)
                pts = simplify(pts, tolerance)
                kept_points += len(pts)
                flat = []
                for lon, lat in pts:
                    flat.append(round(lon, decimals))
                    flat.append(round(lat, decimals))
                paths.append(flat)

        out_path = os.path.join(OUT_DIR, f"{name}.json.gz")
        payload = {
            "name": name,
            "source": f"Natural Earth {source} (public domain)",
            "tolerance_deg": tolerance,
            "decimals": decimals,
            "paths": paths,
        }
        with gzip.open(out_path, "wt", encoding="utf-8", compresslevel=9) as f:
            json.dump(payload, f, separators=(",", ":"))
        size = os.path.getsize(out_path)
        total_out += size
        print(f"  {name:<18} {len(paths):>6} paths  "
              f"{raw_points:>8} -> {kept_points:>7} points  {size / 1024:>7.0f} KB")

    print(f"\n  total: {total_in / 1e6:.1f} MB of GeoJSON -> {total_out / 1e6:.2f} MB bundled")
    return 0


def main(argv) -> int:
    if "--fetch" in argv:
        return fetch(argv[argv.index("--fetch") + 1])
    if "--build" in argv:
        return build(argv[argv.index("--build") + 1])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
