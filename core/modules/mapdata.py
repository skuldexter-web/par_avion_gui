"""
mapdata.py — Loader for PAR AVION's bundled offline vector map.

The map that ships with PAR AVION is vector data, not raster tiles:
coastlines, national borders and lakes from Natural Earth (public
domain), converted by tools/build_map_data.py into compact gzipped
files under data/map/.

That choice is deliberate. Raster map tiles would mean either an API
key, or a tile server to call at runtime, or several gigabytes on disk
for anything approaching offline coverage. A few megabytes of vector
coastline draws instantly on a Raspberry Pi, scales to any zoom without
blurring, needs no account, no key and no network connection — ever.

Three levels of detail are bundled and selected automatically by zoom:

    110m   whole-world view (ISS ground track)
    50m    regional view, a few hundred kilometres across
    10m    close-in view, roughly 120 m positional detail

Layers are cached in memory after first use and shared between every
widget that draws a map.
"""

from __future__ import annotations

import gzip
import json
import math
import os
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from . import appconfig

MAP_DIR = appconfig.data_path("map")

# layer name -> (filename, kind) where kind drives styling.
AVAILABLE_LAYERS = {
    "land_110m": ("land_110m.json.gz", "land"),
    "land_50m": ("land_50m.json.gz", "land"),
    "coastline_110m": ("coastline_110m.json.gz", "coast"),
    "countries_110m": ("countries_110m.json.gz", "border"),
    "coastline_50m": ("coastline_50m.json.gz", "coast"),
    "countries_50m": ("countries_50m.json.gz", "border"),
    "lakes_50m": ("lakes_50m.json.gz", "water"),
    "coastline_10m": ("coastline_10m.json.gz", "coast"),
}

_cache: Dict[str, "MapLayer"] = {}
_cache_lock = threading.Lock()


@dataclass
class MapLayer:
    """One map layer: a list of polylines in lon/lat degrees, plus each
    path's bounding box so off-screen paths are skipped without walking
    their points."""
    name: str
    kind: str
    paths: List = field(default_factory=list)      # each: flat [lon, lat, ...]
    bboxes: List = field(default_factory=list)     # each: (min_lon, min_lat, max_lon, max_lat)

    def __len__(self) -> int:
        return len(self.paths)

    def visible_paths(self, min_lon: float, min_lat: float,
                      max_lon: float, max_lat: float):
        """Yield the paths whose bounding box intersects the view."""
        for path, (p_min_lon, p_min_lat, p_max_lon, p_max_lat) in zip(self.paths, self.bboxes):
            if (p_max_lon < min_lon or p_min_lon > max_lon
                    or p_max_lat < min_lat or p_min_lat > max_lat):
                continue
            yield path


def layer_path(name: str) -> Optional[str]:
    entry = AVAILABLE_LAYERS.get(name)
    if entry is None:
        return None
    return os.path.join(MAP_DIR, entry[0])


def have_map_data() -> bool:
    """True when at least the world-overview layer is present."""
    path = layer_path("coastline_110m")
    return bool(path and os.path.exists(path))


def missing_layers() -> List[str]:
    return [name for name in AVAILABLE_LAYERS
            if not os.path.exists(layer_path(name) or "")]


def load_layer(name: str) -> Optional[MapLayer]:
    """Load (and cache) one layer. Returns None if the file is absent or
    unreadable — the caller draws whatever else it has rather than
    failing, so a partial install still shows a usable map."""
    with _cache_lock:
        if name in _cache:
            return _cache[name]

    path = layer_path(name)
    if not path or not os.path.exists(path):
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError):
        return None

    kind = AVAILABLE_LAYERS[name][1]
    layer = MapLayer(name=name, kind=kind)
    for flat in payload.get("paths", []):
        if len(flat) < 4:
            continue
        lons = flat[0::2]
        lats = flat[1::2]
        layer.paths.append(flat)
        layer.bboxes.append((min(lons), min(lats), max(lons), max(lats)))

    with _cache_lock:
        _cache[name] = layer
    return layer


def layers_for_span(span_deg: float) -> List[str]:
    """Pick a detail level for the current zoom. Span is the width of
    the view in degrees of longitude."""
    if span_deg > 60.0:
        return ["land_110m", "coastline_110m", "countries_110m"]
    if span_deg > 6.0:
        return ["land_50m", "coastline_50m", "countries_50m", "lakes_50m"]
    # Close in, the 10 m coastline carries the detail and a filled land
    # mass would just be a flat colour behind it, so the fill layer is
    # left out rather than paying to load it.
    return ["land_50m", "coastline_10m", "lakes_50m", "countries_50m"]


def preload(names: Sequence[str]) -> None:
    """Warm the cache (call from a background thread at startup)."""
    for name in names:
        load_layer(name)


# ---------------------------------------------------------------------------
# Geodesy helpers shared by the map widgets
# ---------------------------------------------------------------------------

EARTH_RADIUS_NM = 3440.065
NM_PER_DEG_LAT = 60.0


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in nautical miles."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_NM * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing, degrees true."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def offset_position(lat: float, lon: float, distance_nm: float,
                    bearing: float) -> Tuple[float, float]:
    """Point `distance_nm` from (lat, lon) along a true bearing."""
    ang = distance_nm / EARTH_RADIUS_NM
    br = math.radians(bearing)
    p1 = math.radians(lat)
    l1 = math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(ang)
                   + math.cos(p1) * math.sin(ang) * math.cos(br))
    l2 = l1 + math.atan2(math.sin(br) * math.sin(ang) * math.cos(p1),
                         math.cos(ang) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540.0) % 360.0 - 180.0


def horizon_radius_nm(altitude_km: float) -> float:
    """Radius of the circle on the ground from which an object at this
    altitude is above the horizon — the ISS 'footprint'."""
    r = EARTH_RADIUS_NM
    h = altitude_km / 1.852
    if h <= 0:
        return 0.0
    return r * math.acos(r / (r + h))
