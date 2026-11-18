"""
appconfig.py — Shared, on-disk settings for PAR AVION.

One small JSON file holds the handful of things every mode needs to
agree on: where the operator is (when there's no GPS fix), whether to
trust GPS at all, and whether the app is allowed to reach the network
for optional TLE refreshes.

Why a manual home location matters: without a position, the radar modes
have nothing real to plot against. The previous build invented bearings
and distances from a hash of each callsign so the scope wouldn't look
empty — contacts appeared at positions that were pure fiction. A
manually entered home location is the honest fix: real range and bearing
when GPS is absent, and an explicit "no reference position" state when
the operator hasn't set one, rather than plausible-looking nonsense.

Nothing here requires an API key or an account. The config file is
plain JSON and is safe to edit by hand.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass
from typing import Optional, Tuple

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
CORE_DIR = os.path.dirname(_THIS_DIR)
REPO_ROOT = os.path.dirname(CORE_DIR)
DATA_DIR = os.path.join(REPO_ROOT, "data")

# PAR_AVION_HOME lets the installer (or a test) relocate the whole
# config/cache tree; otherwise it follows the usual XDG-ish convention.
CONFIG_DIR = os.environ.get("PAR_AVION_HOME") or os.path.join(
    os.path.expanduser("~"), ".par_avion"
)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
CACHE_DIR = os.path.join(CONFIG_DIR, "cache")

_lock = threading.Lock()


@dataclass
class AppConfig:
    # Operator location used when GPS has no fix (decimal degrees).
    home_lat: Optional[float] = None
    home_lon: Optional[float] = None
    home_label: str = ""
    home_alt_m: float = 0.0

    # Prefer a live GPS fix over the manual home location when available.
    use_gps: bool = True

    # Allow optional outbound HTTP for TLE refresh. Everything still
    # works with this off — the bundled/cached elements are used instead.
    allow_network: bool = True

    # Last-used radio/waterfall tuning, so the app comes back where the
    # operator left it.
    last_center_hz: int = 0
    last_band_index: int = 0

    def home(self) -> Optional[Tuple[float, float]]:
        if self.home_lat is None or self.home_lon is None:
            return None
        return (self.home_lat, self.home_lon)


_cached: Optional[AppConfig] = None


def ensure_dirs() -> None:
    for path in (CONFIG_DIR, CACHE_DIR):
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            pass


def load(force: bool = False) -> AppConfig:
    """Read the config, falling back to defaults on any problem — a
    corrupt or unreadable config must never stop the app from starting."""
    global _cached
    with _lock:
        if _cached is not None and not force:
            return _cached
        cfg = AppConfig()
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                known = {f for f in AppConfig().__dict__}
                for key, value in raw.items():
                    if key in known:
                        setattr(cfg, key, value)
        except (OSError, ValueError, TypeError):
            pass
        _cached = cfg
        return cfg


def save(cfg: AppConfig) -> bool:
    """Persist the config. Returns False (without raising) if the
    filesystem won't cooperate — the app keeps running on the in-memory
    values either way."""
    global _cached
    with _lock:
        _cached = cfg
        ensure_dirs()
        tmp = CONFIG_PATH + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(asdict(cfg), f, indent=2)
            os.replace(tmp, CONFIG_PATH)
            return True
        except OSError:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            return False


def set_home(lat: Optional[float], lon: Optional[float], label: str = "") -> bool:
    cfg = load()
    cfg.home_lat = lat
    cfg.home_lon = lon
    cfg.home_label = label
    return save(cfg)


def reference_location(gps=None) -> Tuple[Optional[float], Optional[float], str]:
    """
    Resolve the position the radar/pass-prediction modes should use.

    Returns (lat, lon, source) where source is "gps", "manual", or
    "none". A GPS fix wins when one is available and GPS is enabled;
    otherwise the manually configured home location is used. When
    neither exists the caller gets (None, None, "none") and is expected
    to say so plainly rather than invent a position.

    `gps` is an optional hardware.GPSSource; pass one in if you've
    already polled, to avoid probing gpsd twice.
    """
    cfg = load()
    if cfg.use_gps:
        if gps is None:
            try:
                from . import hardware
                gps = hardware.detect_gps()
            except Exception:
                gps = None
        if gps is not None and getattr(gps, "fix", False) and gps.lat is not None:
            return float(gps.lat), float(gps.lon), "gps"

    home = cfg.home()
    if home is not None:
        return home[0], home[1], "manual"
    return None, None, "none"


def describe_location(lat: Optional[float], lon: Optional[float], source: str) -> str:
    if lat is None or lon is None:
        return "no reference position set"
    tag = {"gps": "GPS fix", "manual": "manual home location"}.get(source, source)
    cfg = load()
    label = f" ({cfg.home_label})" if source == "manual" and cfg.home_label else ""
    return f"{lat:+.4f}, {lon:+.4f} — {tag}{label}"


def cache_path(name: str) -> str:
    ensure_dirs()
    return os.path.join(CACHE_DIR, name)


def data_path(*parts: str) -> str:
    return os.path.join(DATA_DIR, *parts)
