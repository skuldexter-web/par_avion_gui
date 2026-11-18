"""
iss.py — ISS orbit propagation, ground track and pass prediction.

Stand-alone by design: no API key, no account, and no mandatory network
access. Orbit propagation is done locally with SGP4 (the same model the
published elements are generated for) plus coordinate transforms
implemented here, so nothing is downloaded at runtime — not even an
ephemeris file.

Element sets are resolved in this order:

  1. elements pasted by the operator (`set_manual_tle`)
  2. ``~/.par_avion/cache/iss.tle`` — whatever was last fetched
  3. ``data/tle/iss.tle`` — the set bundled with the repository
  4. a live refresh, if the network is reachable and allowed

Every TLE carries its epoch, so the tracker always reports how old its
elements are. That matters: TLEs decay. For something in low Earth
orbit the along-track error grows by a few kilometres a day, which is
tens of seconds of pass-time error after a couple of weeks. The UI
shows the age rather than quietly presenting a stale prediction as
gospel.

Refresh sources (all keyless, tried in order): CelesTrak, ARISS, AMSAT.

Receive-only/offline: this module reads published orbital elements and
does arithmetic. It controls no hardware and transmits nothing.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Sequence, Tuple

try:
    import numpy as np
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False

try:
    from sgp4.api import Satrec, jday
    HAVE_SGP4 = True
except ImportError:
    Satrec = None
    jday = None
    HAVE_SGP4 = False

from . import appconfig

ISS_NORAD_ID = 25544
ISS_NAME = "ISS (ZARYA)"

# Keyless public element sources, tried in order.
TLE_SOURCES = [
    ("CelesTrak", "https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=TLE"),
    ("ARISS", "https://live.ariss.org/iss.txt"),
    ("AMSAT", "https://www.amsat.org/tle/current/nasabare.txt"),
]

BUNDLED_TLE_PATH = appconfig.data_path("tle", "iss.tle")
CACHED_TLE_NAME = "iss.tle"

# WGS-84
_EARTH_A_KM = 6378.137
_EARTH_F = 1.0 / 298.257223563
_EARTH_E2 = _EARTH_F * (2.0 - _EARTH_F)
_EARTH_B_KM = _EARTH_A_KM * (1.0 - _EARTH_F)
_AU_KM = 149_597_870.7


# ---------------------------------------------------------------------------
# TLE handling
# ---------------------------------------------------------------------------

@dataclass
class TLE:
    name: str
    line1: str
    line2: str
    source: str = "unknown"      # live:<site> | cache | bundled | manual
    loaded_at: float = 0.0

    @property
    def epoch(self) -> Optional[datetime]:
        """Element-set epoch, parsed from columns 19-32 of line 1."""
        try:
            raw = self.line1[18:32]
            yy = int(raw[0:2])
            day = float(raw[2:])
            year = 2000 + yy if yy < 57 else 1900 + yy
            return (datetime(year, 1, 1, tzinfo=timezone.utc)
                    + timedelta(days=day - 1.0))
        except (ValueError, IndexError):
            return None

    @property
    def age_days(self) -> Optional[float]:
        ep = self.epoch
        if ep is None:
            return None
        return (datetime.now(timezone.utc) - ep).total_seconds() / 86400.0

    def age_text(self) -> str:
        age = self.age_days
        if age is None:
            return "unknown epoch"
        if age < 1.0:
            return f"{age * 24:.1f} h old"
        return f"{age:.1f} days old"

    def quality_note(self) -> str:
        """Plain-language accuracy caveat based on element age."""
        age = self.age_days
        if age is None:
            return "Element epoch could not be read."
        if age < 0:
            return "Elements are dated in the future — check the system clock."
        if age <= 3:
            return "Elements are fresh; pass times should be good to a second or two."
        if age <= 14:
            return ("Elements are a few days old; pass times may drift by "
                    "several seconds.")
        if age <= 60:
            return ("Elements are weeks old — expect pass times to be off by "
                    "a minute or more. Refresh when you can.")
        return ("Elements are months old. Positions are indicative only; "
                "refresh before relying on a pass time.")

    def as_text(self) -> str:
        return f"{self.name}\n{self.line1}\n{self.line2}\n"


def tle_checksum(line: str) -> int:
    """Standard TLE modulo-10 checksum over the first 68 columns."""
    total = 0
    for ch in line[:68]:
        if ch.isdigit():
            total += int(ch)
        elif ch == "-":
            total += 1
    return total % 10


def validate_tle(line1: str, line2: str) -> Tuple[bool, str]:
    """Structural + checksum validation, so a truncated download or a
    mangled copy-paste is rejected rather than silently propagated into
    wildly wrong positions."""
    for idx, line in ((1, line1), (2, line2)):
        if len(line) < 69:
            return False, f"line {idx} is too short ({len(line)} chars, expected 69)"
        if not line.startswith(f"{idx} "):
            return False, f"line {idx} does not start with '{idx} '"
        try:
            given = int(line[68])
        except (ValueError, IndexError):
            return False, f"line {idx} has no checksum digit"
        if tle_checksum(line) != given:
            return False, f"line {idx} checksum mismatch (corrupt or truncated)"
    if line1[2:7] != line2[2:7]:
        return False, "the two lines are for different satellites"
    return True, "ok"


def parse_tle_text(text: str, want_norad: Optional[int] = ISS_NORAD_ID,
                   source: str = "unknown") -> Optional[TLE]:
    """Pull a TLE out of arbitrary text: a bare 2/3-line set, or a
    multi-satellite bulletin like AMSAT's nasabare.txt."""
    lines = [ln.rstrip() for ln in text.splitlines()]
    norad_str = f"{want_norad:05d}" if want_norad is not None else None

    for i, line in enumerate(lines):
        if not line.startswith("1 "):
            continue
        if i + 1 >= len(lines) or not lines[i + 1].startswith("2 "):
            continue
        line1, line2 = line, lines[i + 1]
        if norad_str is not None and line1[2:7].strip() != norad_str:
            continue
        ok, _ = validate_tle(line1, line2)
        if not ok:
            continue
        name = ISS_NAME
        if i > 0 and lines[i - 1].strip() and not lines[i - 1].startswith(("1 ", "2 ")):
            name = lines[i - 1].strip().lstrip("0 ").strip() or ISS_NAME
        return TLE(name=name, line1=line1, line2=line2, source=source,
                   loaded_at=time.time())
    return None


def load_bundled_tle() -> Optional[TLE]:
    try:
        with open(BUNDLED_TLE_PATH, "r", encoding="utf-8") as f:
            return parse_tle_text(f.read(), source="bundled")
    except OSError:
        return None


def load_cached_tle() -> Optional[TLE]:
    try:
        with open(appconfig.cache_path(CACHED_TLE_NAME), "r", encoding="utf-8") as f:
            return parse_tle_text(f.read(), source="cache")
    except OSError:
        return None


def save_cached_tle(tle: TLE) -> bool:
    try:
        with open(appconfig.cache_path(CACHED_TLE_NAME), "w", encoding="utf-8") as f:
            f.write(tle.as_text())
        return True
    except OSError:
        return False


def fetch_tle(timeout_s: float = 10.0) -> Tuple[Optional[TLE], str]:
    """Try each public source in turn. Returns (tle, message)."""
    try:
        import requests
    except ImportError:
        return None, "requests not installed — cannot refresh elements online"

    problems = []
    for site, url in TLE_SOURCES:
        try:
            resp = requests.get(url, timeout=timeout_s,
                                headers={"User-Agent": "PAR-AVION/2.1 (amateur radio)"})
            resp.raise_for_status()
            tle = parse_tle_text(resp.text, source=f"live:{site}")
            if tle is not None:
                return tle, f"Fetched fresh elements from {site}."
            problems.append(f"{site}: no valid ISS element set in response")
        except Exception as e:  # network, DNS, TLS, HTTP, parse
            problems.append(f"{site}: {type(e).__name__}")
    return None, "Could not refresh elements (" + "; ".join(problems) + ")"


# ---------------------------------------------------------------------------
# Astronomy helpers
# ---------------------------------------------------------------------------

def _gmst_rad(jd: float, fr: float) -> float:
    """Greenwich Mean Sidereal Time (radians), IAU 1982 series."""
    tut1 = ((jd - 2451545.0) + fr) / 36525.0
    gmst_sec = (67310.54841
                + (876600.0 * 3600.0 + 8640184.812866) * tut1
                + 0.093104 * tut1 * tut1
                - 6.2e-6 * tut1 * tut1 * tut1)
    gmst = math.fmod(gmst_sec, 86400.0) / 240.0   # seconds -> degrees
    if gmst < 0:
        gmst += 360.0
    return math.radians(gmst)


def _teme_to_ecef(r_teme: Sequence[float], gmst: float) -> Tuple[float, float, float]:
    """Rotate TEME coordinates into an Earth-fixed frame. Polar motion is
    ignored — it is well under a metre at these scales."""
    cos_g, sin_g = math.cos(gmst), math.sin(gmst)
    x, y, z = r_teme
    return (cos_g * x + sin_g * y, -sin_g * x + cos_g * y, z)


def _ecef_to_geodetic(x: float, y: float, z: float) -> Tuple[float, float, float]:
    """Earth-fixed km -> (lat_deg, lon_deg, altitude_km) on WGS-84."""
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    lat = math.atan2(z, p * (1.0 - _EARTH_E2))
    alt = 0.0
    for _ in range(6):
        sin_lat = math.sin(lat)
        n = _EARTH_A_KM / math.sqrt(1.0 - _EARTH_E2 * sin_lat * sin_lat)
        alt = (p / math.cos(lat)) - n if abs(math.cos(lat)) > 1e-12 else abs(z) - _EARTH_B_KM
        lat = math.atan2(z, p * (1.0 - _EARTH_E2 * n / (n + alt)))
    lon_deg = (math.degrees(lon) + 540.0) % 360.0 - 180.0
    return math.degrees(lat), lon_deg, alt


def _geodetic_to_ecef(lat_deg: float, lon_deg: float, alt_m: float = 0.0):
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    alt_km = alt_m / 1000.0
    sin_lat = math.sin(lat)
    n = _EARTH_A_KM / math.sqrt(1.0 - _EARTH_E2 * sin_lat * sin_lat)
    return ((n + alt_km) * math.cos(lat) * math.cos(lon),
            (n + alt_km) * math.cos(lat) * math.sin(lon),
            (n * (1.0 - _EARTH_E2) + alt_km) * sin_lat)


def _look_angles(obs_ecef, obs_lat_deg: float, obs_lon_deg: float, sat_ecef):
    """(azimuth_deg, elevation_deg, range_km) from observer to satellite."""
    lat = math.radians(obs_lat_deg)
    lon = math.radians(obs_lon_deg)
    dx = sat_ecef[0] - obs_ecef[0]
    dy = sat_ecef[1] - obs_ecef[1]
    dz = sat_ecef[2] - obs_ecef[2]

    sin_lat, cos_lat = math.sin(lat), math.cos(lat)
    sin_lon, cos_lon = math.sin(lon), math.cos(lon)
    east = -sin_lon * dx + cos_lon * dy
    north = -sin_lat * cos_lon * dx - sin_lat * sin_lon * dy + cos_lat * dz
    up = cos_lat * cos_lon * dx + cos_lat * sin_lon * dy + sin_lat * dz

    rng = math.sqrt(dx * dx + dy * dy + dz * dz)
    el = math.degrees(math.asin(max(-1.0, min(1.0, up / rng)))) if rng > 0 else 0.0
    az = (math.degrees(math.atan2(east, north)) + 360.0) % 360.0
    return az, el, rng


def _sun_ecef(jd: float, fr: float):
    """Low-precision solar position (Astronomical Almanac, ~0.01 deg),
    rotated into the Earth-fixed frame. Good enough to decide whether
    the ISS is sunlit and whether the observer is in darkness."""
    n = (jd - 2451545.0) + fr
    mean_lon = math.radians((280.460 + 0.9856474 * n) % 360.0)
    mean_anom = math.radians((357.528 + 0.9856003 * n) % 360.0)
    ecl_lon = mean_lon + math.radians(1.915) * math.sin(mean_anom) \
        + math.radians(0.020) * math.sin(2 * mean_anom)
    obliquity = math.radians(23.439 - 4.0e-7 * n)
    dist = (1.00014 - 0.01671 * math.cos(mean_anom)
            - 0.00014 * math.cos(2 * mean_anom)) * _AU_KM
    x = dist * math.cos(ecl_lon)
    y = dist * math.cos(obliquity) * math.sin(ecl_lon)
    z = dist * math.sin(obliquity) * math.sin(ecl_lon)
    return _teme_to_ecef((x, y, z), _gmst_rad(jd, fr))


def _is_sunlit(sat_ecef, sun_ecef) -> bool:
    """Cylindrical Earth-shadow test: the satellite is eclipsed when it
    sits on the far side of the planet from the Sun and within one Earth
    radius of the shadow axis."""
    sun_norm = math.sqrt(sum(c * c for c in sun_ecef))
    if sun_norm == 0:
        return True
    ux, uy, uz = (c / sun_norm for c in sun_ecef)
    along = sat_ecef[0] * ux + sat_ecef[1] * uy + sat_ecef[2] * uz
    if along >= 0:
        return True   # same side as the Sun
    perp_sq = sum(c * c for c in sat_ecef) - along * along
    return perp_sq > _EARTH_A_KM * _EARTH_A_KM


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

@dataclass
class SubPoint:
    lat: float
    lon: float
    alt_km: float
    velocity_kms: float
    sunlit: bool = True
    when: Optional[datetime] = None


@dataclass
class PassPrediction:
    rise_time: Optional[datetime] = None
    culminate_time: Optional[datetime] = None
    set_time: Optional[datetime] = None
    max_elevation_deg: Optional[float] = None
    rise_azimuth_deg: Optional[float] = None
    set_azimuth_deg: Optional[float] = None
    visible: bool = False          # sunlit satellite, dark sky at observer

    @property
    def duration_s(self) -> Optional[float]:
        if self.rise_time and self.set_time:
            return (self.set_time - self.rise_time).total_seconds()
        return None

    def duration_text(self) -> str:
        d = self.duration_s
        if d is None:
            return "--"
        return f"{int(d // 60)}m {int(d % 60):02d}s"


# ---------------------------------------------------------------------------
# Tracker
# ---------------------------------------------------------------------------

class ISSTracker:
    """
    Propagates the ISS locally from a TLE. Construction never touches
    the network: it loads cached or bundled elements immediately so the
    UI has something to show at once, and an online refresh is an
    explicit, separate call that can be run on a background thread.
    """

    def __init__(self, observer_lat: Optional[float] = None,
                 observer_lon: Optional[float] = None,
                 observer_alt_m: float = 0.0,
                 allow_network: Optional[bool] = None):
        self.observer_lat = observer_lat
        self.observer_lon = observer_lon
        self.observer_alt_m = observer_alt_m
        cfg = appconfig.load()
        self.allow_network = cfg.allow_network if allow_network is None else allow_network

        self.tle: Optional[TLE] = None
        self._sat = None
        self.last_error = ""
        self.last_message = ""

        if not HAVE_SGP4:
            self.last_error = ("sgp4 is not installed — ISS tracking is unavailable. "
                               "Install it with: pip install sgp4")

        self._load_best_available()

    # -- element management -------------------------------------------
    def _load_best_available(self) -> None:
        cached = load_cached_tle()
        bundled = load_bundled_tle()
        best = None
        if cached and bundled:
            ca, ba = cached.age_days, bundled.age_days
            best = cached if (ca is not None and ba is not None and ca <= ba) else bundled
        else:
            best = cached or bundled
        if best is not None:
            self._use(best)
        else:
            self.last_error = ("No orbital elements available — no cached or "
                               "bundled TLE found.")

    def _use(self, tle: TLE) -> bool:
        if not HAVE_SGP4:
            self.tle = tle
            return False
        try:
            self._sat = Satrec.twoline2rv(tle.line1, tle.line2)
        except Exception as e:
            self.last_error = f"Could not load elements: {e}"
            return False
        self.tle = tle
        return True

    def set_manual_tle(self, text: str) -> Tuple[bool, str]:
        """Accept elements pasted by the operator — the fully offline
        path for a machine that never touches a network."""
        tle = parse_tle_text(text, want_norad=None, source="manual")
        if tle is None:
            return False, ("Could not read a valid element set. Paste both "
                           "numbered lines (69 characters each); the name "
                           "line is optional.")
        if not self._use(tle):
            return False, self.last_error or "Elements rejected by the propagator."
        save_cached_tle(tle)
        self.last_message = f"Using manually entered elements ({tle.age_text()})."
        return True, self.last_message

    def refresh_tle(self, force: bool = False) -> Tuple[bool, str]:
        """Fetch fresh elements if allowed. Safe to call on a worker
        thread; returns (changed, human-readable message)."""
        if not self.allow_network:
            return False, ("Network access is disabled in settings — using "
                           f"{self.element_source_text()}.")
        age = self.tle.age_days if self.tle else None
        if not force and age is not None and age < 0.5:
            return False, "Elements are less than 12 hours old; no refresh needed."

        tle, message = fetch_tle()
        if tle is None:
            self.last_message = message
            return False, message + f" Continuing with {self.element_source_text()}."
        if not self._use(tle):
            return False, self.last_error
        save_cached_tle(tle)
        self.last_message = message
        return True, message

    def element_source_text(self) -> str:
        if self.tle is None:
            return "no elements"
        label = {
            "cache": "cached elements",
            "bundled": "the bundled elements",
            "manual": "manually entered elements",
        }.get(self.tle.source, self.tle.source.replace("live:", "elements from "))
        return f"{label} ({self.tle.age_text()})"

    @property
    def ready(self) -> bool:
        return HAVE_SGP4 and self._sat is not None

    # -- propagation ---------------------------------------------------
    def _propagate(self, when: datetime):
        """-> (ecef_km, velocity_kms, jd, fr) or None."""
        if not self.ready:
            return None
        when = when.astimezone(timezone.utc)
        jd, fr = jday(when.year, when.month, when.day,
                      when.hour, when.minute, when.second + when.microsecond / 1e6)
        err, r, v = self._sat.sgp4(jd, fr)
        if err != 0:
            self.last_error = f"SGP4 propagation error {err}"
            return None
        gmst = _gmst_rad(jd, fr)
        ecef = _teme_to_ecef(r, gmst)
        speed = math.sqrt(sum(c * c for c in v))
        return ecef, speed, jd, fr

    def subpoint(self, when: Optional[datetime] = None) -> Optional[SubPoint]:
        when = when or datetime.now(timezone.utc)
        result = self._propagate(when)
        if result is None:
            return None
        ecef, speed, jd, fr = result
        lat, lon, alt = _ecef_to_geodetic(*ecef)
        return SubPoint(lat=lat, lon=lon, alt_km=alt, velocity_kms=speed,
                        sunlit=_is_sunlit(ecef, _sun_ecef(jd, fr)), when=when)

    def current_subpoint(self):
        """Backwards-compatible tuple form: (lat, lon, alt_km)."""
        sp = self.subpoint()
        if sp is None:
            return None, None, None
        return sp.lat, sp.lon, sp.alt_km

    def look_angles(self, when: Optional[datetime] = None):
        """(az_deg, el_deg, range_km) from the observer, or None."""
        if self.observer_lat is None or self.observer_lon is None:
            return None
        when = when or datetime.now(timezone.utc)
        result = self._propagate(when)
        if result is None:
            return None
        obs = _geodetic_to_ecef(self.observer_lat, self.observer_lon, self.observer_alt_m)
        return _look_angles(obs, self.observer_lat, self.observer_lon, result[0])

    def ground_track(self, minutes_before: float = 30.0, minutes_after: float = 90.0,
                     step_s: float = 30.0) -> List[Tuple[float, float]]:
        """Sub-satellite points around now, for drawing on a map."""
        if not self.ready:
            return []
        now = datetime.now(timezone.utc)
        points: List[Tuple[float, float]] = []
        t = -minutes_before * 60.0
        end = minutes_after * 60.0
        while t <= end:
            sp = self.subpoint(now + timedelta(seconds=t))
            if sp is not None:
                points.append((sp.lat, sp.lon))
            t += step_s
        return points

    # -- pass prediction -----------------------------------------------
    def _elevation_at(self, when: datetime, obs_ecef) -> float:
        result = self._propagate(when)
        if result is None:
            return -90.0
        return _look_angles(obs_ecef, self.observer_lat, self.observer_lon, result[0])[1]

    def next_passes(self, count: int = 3, horizon_deg: float = 10.0,
                    search_hours: float = 48.0,
                    coarse_step_s: float = 30.0) -> List[PassPrediction]:
        """
        Find upcoming passes above `horizon_deg`.

        Coarse-steps elevation to find horizon crossings, then bisects
        each crossing to one second and refines the culmination, so a
        30-second scan step doesn't cost 30 seconds of accuracy.
        """
        if not self.ready or self.observer_lat is None or self.observer_lon is None:
            return []

        obs = _geodetic_to_ecef(self.observer_lat, self.observer_lon, self.observer_alt_m)
        start = datetime.now(timezone.utc)
        total_s = search_hours * 3600.0

        def elev(offset_s: float) -> float:
            return self._elevation_at(start + timedelta(seconds=offset_s), obs)

        # Vectorised coarse scan when numpy + sgp4's array API are both
        # available; it is ~50x faster on a Pi than stepping in Python.
        offsets = [i * coarse_step_s for i in range(int(total_s / coarse_step_s) + 1)]
        elevations = self._elevations_bulk(offsets, start, obs)
        if elevations is None:
            elevations = [elev(o) for o in offsets]

        passes: List[PassPrediction] = []
        i = 1
        while i < len(offsets) and len(passes) < count:
            if elevations[i - 1] < horizon_deg <= elevations[i]:
                rise_s = self._bisect_crossing(elev, offsets[i - 1], offsets[i], horizon_deg)
                # Walk forward to the set crossing.
                j = i
                while j < len(offsets) - 1 and elevations[j] >= horizon_deg:
                    j += 1
                set_s = self._bisect_crossing(elev, offsets[j - 1], offsets[j],
                                              horizon_deg, falling=True)
                peak_s, peak_el = self._refine_peak(elev, rise_s, set_s)

                rise_dt = start + timedelta(seconds=rise_s)
                peak_dt = start + timedelta(seconds=peak_s)
                set_dt = start + timedelta(seconds=set_s)

                rise_az = self._azimuth_at(rise_dt, obs)
                set_az = self._azimuth_at(set_dt, obs)

                passes.append(PassPrediction(
                    rise_time=rise_dt, culminate_time=peak_dt, set_time=set_dt,
                    max_elevation_deg=peak_el,
                    rise_azimuth_deg=rise_az, set_azimuth_deg=set_az,
                    visible=self._is_visible_pass(peak_dt, obs),
                ))
                i = j + 1
            else:
                i += 1
        return passes

    def _elevations_bulk(self, offsets, start: datetime, obs):
        """Elevation at many offsets at once via sgp4's array API."""
        if not HAVE_NUMPY or not self.ready:
            return None
        try:
            base = start.astimezone(timezone.utc)
            jd0, fr0 = jday(base.year, base.month, base.day,
                            base.hour, base.minute,
                            base.second + base.microsecond / 1e6)
            offs = np.asarray(offsets, dtype=np.float64) / 86400.0
            jd = np.full(offs.size, jd0)
            fr = fr0 + offs
            err, r, _v = self._sat.sgp4_array(jd, fr)
        except Exception:
            return None

        total = jd + fr
        tut1 = (total - 2451545.0) / 36525.0
        gmst_sec = (67310.54841
                    + (876600.0 * 3600.0 + 8640184.812866) * tut1
                    + 0.093104 * tut1 ** 2
                    - 6.2e-6 * tut1 ** 3)
        gmst = np.radians(np.mod(gmst_sec, 86400.0) / 240.0)

        cos_g, sin_g = np.cos(gmst), np.sin(gmst)
        x = cos_g * r[:, 0] + sin_g * r[:, 1]
        y = -sin_g * r[:, 0] + cos_g * r[:, 1]
        z = r[:, 2]

        lat = math.radians(self.observer_lat)
        lon = math.radians(self.observer_lon)
        dx, dy, dz = x - obs[0], y - obs[1], z - obs[2]
        up = (math.cos(lat) * math.cos(lon) * dx
              + math.cos(lat) * math.sin(lon) * dy
              + math.sin(lat) * dz)
        rng = np.sqrt(dx * dx + dy * dy + dz * dz)
        with np.errstate(invalid="ignore", divide="ignore"):
            el = np.degrees(np.arcsin(np.clip(up / rng, -1.0, 1.0)))
        el = np.where(np.asarray(err) == 0, el, -90.0)
        return el.tolist()

    @staticmethod
    def _bisect_crossing(elev_fn, lo_s: float, hi_s: float, horizon_deg: float,
                         falling: bool = False, tol_s: float = 1.0) -> float:
        """Narrow a horizon crossing bracketed by [lo_s, hi_s] to ~1 s."""
        for _ in range(40):
            if hi_s - lo_s <= tol_s:
                break
            mid = (lo_s + hi_s) / 2.0
            above = elev_fn(mid) >= horizon_deg
            if above != falling:
                hi_s = mid
            else:
                lo_s = mid
        return (lo_s + hi_s) / 2.0

    @staticmethod
    def _refine_peak(elev_fn, rise_s: float, set_s: float) -> Tuple[float, float]:
        """Ternary search for maximum elevation (the curve is unimodal
        across a single pass)."""
        lo, hi = rise_s, set_s
        for _ in range(30):
            if hi - lo < 1.0:
                break
            a = lo + (hi - lo) / 3.0
            b = hi - (hi - lo) / 3.0
            if elev_fn(a) < elev_fn(b):
                lo = a
            else:
                hi = b
        peak = (lo + hi) / 2.0
        return peak, elev_fn(peak)

    def _azimuth_at(self, when: datetime, obs) -> Optional[float]:
        result = self._propagate(when)
        if result is None:
            return None
        return _look_angles(obs, self.observer_lat, self.observer_lon, result[0])[0]

    def _is_visible_pass(self, when: datetime, obs) -> bool:
        """A pass is naked-eye visible when the station is in sunlight
        but the observer is in darkness."""
        result = self._propagate(when)
        if result is None:
            return False
        ecef, _speed, jd, fr = result
        sun = _sun_ecef(jd, fr)
        if not _is_sunlit(ecef, sun):
            return False
        _az, sun_el, _r = _look_angles(obs, self.observer_lat, self.observer_lon, sun)
        return sun_el < -6.0     # civil twilight or darker

    def next_pass(self, search_hours: float = 48.0) -> PassPrediction:
        """Single-pass convenience wrapper."""
        found = self.next_passes(count=1, search_hours=search_hours)
        return found[0] if found else PassPrediction()


def _format_eta(target: Optional[datetime]) -> str:
    if target is None:
        return "unknown"
    delta = target - datetime.now(timezone.utc)
    if delta.total_seconds() < 0:
        return "now"
    total_min = int(delta.total_seconds() // 60)
    h, m = divmod(total_min, 60)
    return f"{h:02d}h {m:02d}m"


# ---------------------------------------------------------------------------
# Curses UI (standalone CLI mode)
# ---------------------------------------------------------------------------

def run(stdscr, ref_lat: Optional[float] = None, ref_lon: Optional[float] = None) -> None:
    """
    ref_lat/ref_lon: pass None when there's no position. Pass prediction
    is meaningless without a real observer location, so this mode says so
    rather than silently substituting (0,0) — a real place in the Gulf of
    Guinea that would produce plausible-looking but wrong pass times.
    """
    import curses
    from . import radar_ui

    curses.curs_set(0)
    radar_ui.init_colors()

    if ref_lat is None or ref_lon is None:
        ref_lat, ref_lon, _src = appconfig.reference_location()
    gps_available = ref_lat is not None and ref_lon is not None

    height, width = stdscr.getmaxyx()
    globe_w = max(20, width // 2)
    globe_win = curses.newwin(height - 1, globe_w, 0, 0)
    info_win = curses.newwin(height - 1, width - globe_w, 0, globe_w)
    status_win = curses.newwin(1, width, height - 1, 0)
    globe = radar_ui.SpinningGlobe(height - 1, globe_w, radar_ui.PAIR_CYAN)

    def _status(text: str) -> None:
        status_win.erase()
        try:
            status_win.addstr(0, 0, text[: width - 1],
                              curses.color_pair(radar_ui.PAIR_YELLOW))
        except curses.error:
            pass
        status_win.refresh()

    _status(" Loading orbital elements... ")
    tracker = ISSTracker(observer_lat=ref_lat, observer_lon=ref_lon)
    passes = tracker.next_passes(count=3) if gps_available else []
    last_pass_check = time.time()

    stdscr.nodelay(True)
    stdscr.timeout(200)

    while True:
        key = stdscr.getch()
        if key in (ord("q"), ord("Q"), 27):
            break
        if key in (ord("r"), ord("R")):
            _status(" Refreshing elements... ")
            _changed, msg = tracker.refresh_tle(force=True)
            _status(" " + msg[: width - 2] + " ")
            if gps_available:
                passes = tracker.next_passes(count=3)

        if gps_available and time.time() - last_pass_check > 300:
            passes = tracker.next_passes(count=3)
            last_pass_check = time.time()

        globe.tick()
        sp = tracker.subpoint()
        globe.draw(globe_win, sub_lat=sp.lat if sp else None,
                   sub_lon=sp.lon if sp else None)

        info_win.erase()
        info_win.border()
        info_win.addstr(1, 2, " ISS — ORBITAL TELEMETRY ",
                        curses.color_pair(radar_ui.PAIR_CYAN) | curses.A_BOLD)
        inner_w = (width - globe_w) - 4
        row = 3
        if sp is not None:
            info_win.addstr(row, 2, f"Sub-satellite point: {sp.lat:+.2f}, {sp.lon:+.2f}")
            row += 1
            info_win.addstr(row, 2, f"Altitude: {sp.alt_km:.1f} km   "
                                    f"Speed: {sp.velocity_kms:.2f} km/s")
            row += 1
            info_win.addstr(row, 2, f"Illumination: {'sunlit' if sp.sunlit else 'eclipsed'}")
            row += 2
        else:
            reason = "sgp4 not installed" if not HAVE_SGP4 else (tracker.last_error or "no elements")
            info_win.addstr(row, 2, f"Position unavailable ({reason})"[:inner_w])
            row += 2

        if not gps_available:
            info_win.addstr(row, 2, "NEXT PASS: unavailable (no position)",
                            curses.color_pair(radar_ui.PAIR_RED) | curses.A_UNDERLINE)
            row += 1
            info_win.addstr(row, 2, "Set a home location in the GUI settings.")
            row += 2
        else:
            info_win.addstr(row, 2, "NEXT PASSES (your location):", curses.A_UNDERLINE)
            row += 1
            if not passes:
                info_win.addstr(row, 2, "None above 10 deg in the next 48 h.")
                row += 1
            for p in passes:
                if row >= height - 5:
                    break
                flag = "visible" if p.visible else "daylight/eclipsed"
                info_win.addstr(row, 2,
                                f"in {_format_eta(p.rise_time)}  "
                                f"max {p.max_elevation_deg:.0f}deg  "
                                f"{p.duration_text()}  {flag}"[:inner_w])
                row += 1
            row += 1

        if tracker.tle is not None and row < height - 4:
            info_win.addstr(row, 2, f"Elements: {tracker.element_source_text()}"[:inner_w])
            row += 1
            info_win.addstr(row, 2, tracker.tle.quality_note()[:inner_w],
                            curses.color_pair(radar_ui.PAIR_YELLOW))
        info_win.noutrefresh()

        status_win.erase()
        status_win.addstr(0, 0, " [Q] Quit  |  [R] Refresh elements  ",
                          curses.color_pair(radar_ui.PAIR_CYAN))
        status_win.noutrefresh()
        curses.doupdate()
