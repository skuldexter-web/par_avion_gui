"""
gui/components/map_canvas.py — Embedded offline vector map.

Draws the bundled Natural Earth coastlines, borders and lakes on a
Tkinter canvas, centred on the operator's position, with range rings in
nautical miles and live contacts plotted at their real latitude and
longitude. Used under the radar in Airplanes and Maritime, and in
world-view mode for the ISS ground track.

No tiles, no tile server, no API key, no network: the vector data is in
data/map/ and everything here is local arithmetic. See
core/modules/mapdata.py for why vector rather than raster.

Interaction: scroll to zoom, drag to pan, double-click to recentre on
the operator position.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import customtkinter as ctk

from gui.themes.theme import (ACCENT_AMBER, ACCENT_GREEN, ACCENT_RED,
                              DARK_TEXT_DIM)

try:
    from modules import mapdata
    HAVE_MAPDATA = True
except ImportError:  # pragma: no cover - import path differs before sys.path setup
    mapdata = None
    HAVE_MAPDATA = False

# Palette: land/coast reads brightest, political borders sit behind it,
# water is a cool tone so it never competes with contact symbols.
COLOR_LAND = "#0e1a16"
COLOR_COAST = "#2f7d4f"
COLOR_BORDER = "#2a3b52"
COLOR_WATER = "#12283a"
COLOR_GRID = "#1d2733"
COLOR_RING = "#2c4a38"
COLOR_HOME = ACCENT_AMBER

MIN_SPAN_NM = 2.0
MAX_SPAN_NM = 12000.0


@dataclass
class MapContact:
    """A thing to plot at a real position."""
    lat: float
    lon: float
    label: str = ""
    glyph: str = "✈"
    color: str = ACCENT_GREEN
    heading_deg: Optional[float] = None
    sublabel: str = ""


class MapCanvas(ctk.CTkCanvas):
    def __init__(self, master, span_nm: float = 80.0, world: bool = False,
                 show_rings: bool = True, **kwargs):
        kwargs.setdefault("bg", "#060a0e")
        kwargs.setdefault("highlightthickness", 0)
        super().__init__(master, **kwargs)

        self.span_nm = span_nm
        self.world = world
        self.show_rings = show_rings

        self._center_lat = 0.0
        self._center_lon = 0.0
        self._home: Optional[Tuple[float, float]] = None
        self._home_source = "none"
        self._contacts: List[MapContact] = []
        self._tracks: List[Tuple[Sequence[Tuple[float, float]], str]] = []
        self._markers: List[MapContact] = []
        self._circles: List[Tuple[float, float, float, str]] = []  # lat, lon, radius_nm, color
        self._status_text = ""
        self._loaded_layers = set()
        self._loading = False
        self._load_done = threading.Event()
        self._drag_origin: Optional[Tuple[int, int]] = None

        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag_origin", None))
        self.bind("<Double-Button-1>", lambda e: self.recenter_on_home())
        # Linux reports wheel events as buttons 4/5; Windows/macOS use
        # <MouseWheel> with a delta.
        self.bind("<Button-4>", lambda e: self.zoom(1 / 1.3))
        self.bind("<Button-5>", lambda e: self.zoom(1.3))
        self.bind("<MouseWheel>", lambda e: self.zoom(1 / 1.3 if e.delta > 0 else 1.3))

        self._ensure_layers_async()

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------
    def set_home(self, lat: Optional[float], lon: Optional[float],
                 source: str = "none") -> None:
        """Set the operator position the map centres on and measures
        range rings from. None clears it — the map then shows the world
        rather than pretending to know where the operator is."""
        self._home_source = source
        if lat is None or lon is None:
            self._home = None
            if not self.world:
                self._status_text = "No position set — set a home location in Settings"
                # Without a position, a close-in view would be centred on
                # (0, 0) — a real spot in the Gulf of Guinea — and look
                # like a meaningless sliver of coastline. Pull back to a
                # whole-world view instead, which is at least honest
                # about knowing nothing.
                self._center_lat, self._center_lon = 20.0, 0.0
                self.span_nm = 7000.0
                self._ensure_layers_async()
        else:
            first = self._home is None
            self._home = (lat, lon)
            self._status_text = ""
            # The world view always shows the whole globe centred on
            # (0, 0); only the local view follows the operator.
            if first and not self.world:
                self._center_lat, self._center_lon = lat, lon
        self.redraw()

    def recenter_on_home(self) -> None:
        if self._home is not None:
            self._center_lat, self._center_lon = self._home
            self.redraw()

    def set_contacts(self, contacts: Sequence[MapContact]) -> None:
        self._contacts = list(contacts)
        self.redraw()

    def set_markers(self, markers: Sequence[MapContact]) -> None:
        """Standalone symbols (e.g. the ISS) drawn above contacts."""
        self._markers = list(markers)
        self.redraw()

    def set_tracks(self, tracks: Sequence[Tuple[Sequence[Tuple[float, float]], str]]) -> None:
        """Polylines in (lat, lon) order, each with a colour — e.g. a
        satellite ground track."""
        self._tracks = list(tracks)
        self.redraw()

    def set_circles(self, circles: Sequence[Tuple[float, float, float, str]]) -> None:
        """Great-circle radius rings: (lat, lon, radius_nm, colour)."""
        self._circles = list(circles)
        self.redraw()

    def set_status(self, text: str) -> None:
        self._status_text = text
        self.redraw()

    def zoom(self, factor: float) -> None:
        if self.world:
            return
        self.span_nm = max(MIN_SPAN_NM, min(MAX_SPAN_NM, self.span_nm * factor))
        self._ensure_layers_async()
        self.redraw()

    def set_span_nm(self, span_nm: float) -> None:
        self.span_nm = max(MIN_SPAN_NM, min(MAX_SPAN_NM, span_nm))
        self._ensure_layers_async()
        self.redraw()

    # ------------------------------------------------------------------
    # Layer loading
    # ------------------------------------------------------------------
    def _wanted_layers(self) -> List[str]:
        if not HAVE_MAPDATA:
            return []
        return mapdata.layers_for_span(360.0 if self.world else self._span_lon_deg())

    def _ensure_layers_async(self) -> None:
        """Load map layers off the UI thread. The 10 m coastline is a
        couple of megabytes of JSON; decompressing that on the Tk thread
        would visibly stall the window on a Pi."""
        if not HAVE_MAPDATA or self._loading:
            return
        wanted = [n for n in self._wanted_layers() if n not in self._loaded_layers]
        if not wanted:
            return
        self._loading = True
        self._load_done.clear()

        def _work():
            try:
                mapdata.preload(wanted)
            finally:
                self._load_done.set()

        threading.Thread(target=_work, daemon=True).start()
        # The worker signals an Event and the main thread polls it.
        # Calling after() from the worker itself is not safe: Tkinter is
        # single-threaded, and it raises outright if the window is
        # closing while a load is in flight.
        self._poll_loading(wanted)

    def _poll_loading(self, wanted) -> None:
        if not self._load_done.is_set():
            try:
                self.after(80, lambda: self._poll_loading(wanted))
            except Exception:
                pass
            return
        self._loading = False
        self._loaded_layers.update(wanted)
        try:
            self.redraw()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Projection
    # ------------------------------------------------------------------
    def _geometry(self):
        w = max(1, self.winfo_width())
        h = max(1, self.winfo_height())
        return w, h

    def _scale(self):
        """(pixels per degree latitude, pixels per degree longitude)."""
        w, h = self._geometry()
        if self.world:
            px_lon = w / 360.0
            px_lat = min(h / 180.0, px_lon)
            return px_lat, px_lon
        span_lat_deg = self.span_nm / mapdata.NM_PER_DEG_LAT
        px_lat = h / max(1e-6, span_lat_deg)
        # Equirectangular with local longitude compression, so shapes
        # stay roughly correct near the operator's latitude.
        px_lon = px_lat * math.cos(math.radians(max(-85.0, min(85.0, self._center_lat))))
        return px_lat, px_lon

    def _span_lon_deg(self) -> float:
        w, _h = self._geometry()
        _px_lat, px_lon = self._scale()
        return w / max(1e-6, px_lon)

    def _project(self, lat: float, lon: float) -> Tuple[float, float]:
        w, h = self._geometry()
        px_lat, px_lon = self._scale()
        dlon = (lon - self._center_lon + 540.0) % 360.0 - 180.0
        return (w / 2.0 + dlon * px_lon,
                h / 2.0 - (lat - self._center_lat) * px_lat)

    def _view_bounds(self):
        w, h = self._geometry()
        px_lat, px_lon = self._scale()
        half_lat = (h / 2.0) / px_lat
        half_lon = (w / 2.0) / px_lon
        return (self._center_lon - half_lon, self._center_lat - half_lat,
                self._center_lon + half_lon, self._center_lat + half_lat)

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------
    def _on_press(self, event) -> None:
        self._drag_origin = (event.x, event.y)

    def _on_drag(self, event) -> None:
        if self._drag_origin is None:
            return
        dx = event.x - self._drag_origin[0]
        dy = event.y - self._drag_origin[1]
        self._drag_origin = (event.x, event.y)
        px_lat, px_lon = self._scale()
        self._center_lon -= dx / px_lon
        self._center_lat += dy / px_lat
        self._center_lat = max(-85.0, min(85.0, self._center_lat))
        self._center_lon = (self._center_lon + 540.0) % 360.0 - 180.0
        self.redraw()

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------
    def redraw(self) -> None:
        try:
            self.delete("all")
        except Exception:
            return
        w, h = self._geometry()
        if w < 20 or h < 20:
            return

        self._draw_graticule()
        self._draw_layers()
        self._draw_circles()
        self._draw_tracks()
        if self.show_rings and self._home is not None and not self.world:
            self._draw_range_rings()
        self._draw_home()
        self._draw_contacts()
        self._draw_markers()
        self._draw_scalebar()
        self._draw_status()

    def _draw_graticule(self) -> None:
        min_lon, min_lat, max_lon, max_lat = self._view_bounds()
        span = max(max_lat - min_lat, (max_lon - min_lon) / 2.0)
        for step in (30.0, 10.0, 5.0, 2.0, 1.0, 0.5, 0.2, 0.1, 0.05):
            if span / step <= 10:
                break
        lat = math.floor(min_lat / step) * step
        while lat <= max_lat:
            # A canvas taller than the projected globe would otherwise
            # draw parallels at "+120°", which do not exist.
            if -90.0 <= lat <= 90.0:
                _x, y = self._project(lat, self._center_lon)
                self.create_line(0, y, self.winfo_width(), y, fill=COLOR_GRID)
                self.create_text(3, y - 7, text=f"{lat:+.2f}", anchor="w",
                                 fill=COLOR_GRID, font=("Consolas", 8))
            lat += step
        lon = math.floor(min_lon / step) * step
        while lon <= max_lon:
            x, _y = self._project(self._center_lat, lon)
            self.create_line(x, 0, x, self.winfo_height(), fill=COLOR_GRID)
            lon += step

    def _draw_layers(self) -> None:
        if not HAVE_MAPDATA:
            return
        min_lon, min_lat, max_lon, max_lat = self._view_bounds()
        w, h = self._geometry()
        styles = {
            "coast": (COLOR_COAST, 1),
            "border": (COLOR_BORDER, 1),
            "water": (COLOR_WATER, 1),
        }
        # Filled land first, then political borders and lakes, and the
        # coastline last so it reads on top of everything else.
        order = {"land": 0, "border": 1, "water": 2, "coast": 3}
        layers = []
        for name in self._wanted_layers():
            layer = mapdata.load_layer(name) if name in self._loaded_layers else None
            if layer is not None:
                layers.append(layer)
        layers.sort(key=lambda lyr: order.get(lyr.kind, 9))

        for layer in layers:
            if layer.kind == "land":
                for flat in layer.visible_paths(min_lon, min_lat, max_lon, max_lat):
                    for segment in self._project_flat(flat, w, h):
                        # A ring needs at least three points to be a
                        # polygon; anything shorter is a clipped sliver.
                        if len(segment) >= 6:
                            self.create_polygon(*segment, fill=COLOR_LAND, outline="")
                continue
            if layer.kind == "water":
                for flat in layer.visible_paths(min_lon, min_lat, max_lon, max_lat):
                    for segment in self._project_flat(flat, w, h):
                        if len(segment) >= 6:
                            self.create_polygon(*segment, fill=COLOR_WATER,
                                                outline=COLOR_WATER)
                continue
            color, width = styles.get(layer.kind, (COLOR_COAST, 1))
            for flat in layer.visible_paths(min_lon, min_lat, max_lon, max_lat):
                for segment in self._project_flat(flat, w, h):
                    if len(segment) >= 4:
                        self.create_line(*segment, fill=color, width=width)

    def _project_flat(self, flat, w: int, h: int):
        """Project a flat [lon, lat, ...] path into canvas coordinates,
        splitting it wherever it would wrap around the antimeridian (a
        join there would otherwise draw a line straight across the map)."""
        out: List[float] = []
        prev_dlon = None
        margin = 50
        for i in range(0, len(flat) - 1, 2):
            lon = flat[i]
            lat = flat[i + 1]
            # Split on a jump in the *projected* longitude offset, not
            # the raw longitude: the seam sits opposite whatever the view
            # is centred on, so testing raw longitude only works for a
            # map centred on the prime meridian and otherwise draws a
            # line straight across the world.
            dlon = (lon - self._center_lon + 540.0) % 360.0 - 180.0
            if prev_dlon is not None and abs(dlon - prev_dlon) > 180.0:
                if len(out) >= 4:
                    yield out
                out = []
            prev_dlon = dlon
            x, y = self._project(lat, lon)
            # Guard against absurd coordinates at extreme zoom; Tk will
            # happily try to draw a line a million pixels long.
            if x < -margin * 100 or x > w + margin * 100 or y < -margin * 100 or y > h + margin * 100:
                if len(out) >= 4:
                    yield out
                out = []
                continue
            out.extend((x, y))
        if len(out) >= 4:
            yield out

    def _draw_range_rings(self) -> None:
        if self._home is None:
            return
        lat, lon = self._home
        px_lat, _px_lon = self._scale()
        for step in (1, 2, 5, 10, 20, 25, 50, 100, 200, 500, 1000):
            if self.span_nm / step <= 6:
                break
        ring = step
        cx, cy = self._project(lat, lon)
        diag = math.sqrt(0.5)   # label on the 135° radial
        while ring <= self.span_nm:
            r_px = (ring / mapdata.NM_PER_DEG_LAT) * px_lat
            self.create_oval(cx - r_px, cy - r_px, cx + r_px, cy + r_px,
                             outline=COLOR_RING)
            # Labelling straight up pushes the outer rings' text off the
            # top of the canvas; the down-right radial stays in view for
            # every ring that is itself visible at all.
            self.create_text(cx + r_px * diag + 2, cy + r_px * diag + 2,
                             text=f"{ring} NM", fill=COLOR_RING, anchor="nw",
                             font=("Consolas", 8))
            ring += step

    def _draw_circles(self) -> None:
        """Great-circle rings (e.g. the ISS footprint), drawn as real
        projected polygons rather than screen-space ellipses so they stay
        correct at high latitudes and large radii."""
        for lat, lon, radius_nm, color in self._circles:
            pts: List[float] = []
            prev_x = None
            for bearing in range(0, 361, 4):
                plat, plon = mapdata.offset_position(lat, lon, radius_nm, bearing)
                x, y = self._project(plat, plon)
                if prev_x is not None and abs(x - prev_x) > self.winfo_width():
                    if len(pts) >= 4:
                        self.create_line(*pts, fill=color)
                    pts = []
                prev_x = x
                pts.extend((x, y))
            if len(pts) >= 4:
                self.create_line(*pts, fill=color)

    def _draw_tracks(self) -> None:
        w, h = self._geometry()
        for points, color in self._tracks:
            flat: List[float] = []
            for lat, lon in points:
                flat.extend((lon, lat))
            for segment in self._project_flat(flat, w, h):
                if len(segment) >= 4:
                    self.create_line(*segment, fill=color, width=2)

    def _draw_home(self) -> None:
        if self._home is None:
            return
        lat, lon = self._home
        x, y = self._project(lat, lon)
        self.create_line(x - 7, y, x + 7, y, fill=COLOR_HOME, width=2)
        self.create_line(x, y - 7, x, y + 7, fill=COLOR_HOME, width=2)
        self.create_oval(x - 4, y - 4, x + 4, y + 4, outline=COLOR_HOME)
        tag = {"gps": "GPS", "manual": "HOME"}.get(self._home_source, "REF")
        self.create_text(x + 10, y + 9, text=tag, fill=COLOR_HOME,
                         anchor="w", font=("Consolas", 8, "bold"))

    def _draw_contacts(self) -> None:
        w, h = self._geometry()
        occupied: List[tuple] = []
        show_sublabels = len(self._contacts) <= 10

        for c in self._contacts:
            x, y = self._project(c.lat, c.lon)
            if x < -40 or x > w + 40 or y < -40 or y > h + 40:
                continue
            if c.heading_deg is not None:
                self._draw_heading_symbol(x, y, c.heading_deg, c.color)
            else:
                self.create_text(x, y, text=c.glyph, fill=c.color,
                                 font=("Consolas", 11, "bold"))
            if not c.label:
                continue

            anchor = "e" if x > w * 0.75 else "w"
            dx = -9 if anchor == "e" else 9
            lines = [c.label] + ([c.sublabel] if (c.sublabel and show_sublabels) else [])
            width_px = max(len(ln) for ln in lines) * 5.6
            left = x + dx - width_px if anchor == "e" else x + dx
            box = (left, y - 12, left + width_px, y + 6 + 12 * (len(lines) - 1))
            # Traffic bunches together near an airport or a harbour; a
            # pile of overlapping callsigns is worse than none, so a
            # label is dropped when its box collides with one already
            # placed. The symbol itself is always drawn.
            if any(self._overlaps(box, other) for other in occupied):
                continue
            occupied.append(box)

            self.create_text(x + dx, y - 6, text=lines[0], fill=c.color,
                             anchor=anchor, font=("Consolas", 8))
            if len(lines) > 1:
                self.create_text(x + dx, y + 6, text=lines[1],
                                 fill=DARK_TEXT_DIM, anchor=anchor,
                                 font=("Consolas", 8))

    @staticmethod
    def _overlaps(a, b) -> bool:
        return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])

    def _draw_heading_symbol(self, x: float, y: float, heading: float,
                             color: str) -> None:
        """A small triangle pointing along the contact's track — far more
        readable than a dot when you want to see where traffic is going."""
        ang = math.radians(heading)
        size = 6.0
        tip = (x + size * math.sin(ang), y - size * math.cos(ang))
        left = (x + size * 0.6 * math.sin(ang + 2.4), y - size * 0.6 * math.cos(ang + 2.4))
        right = (x + size * 0.6 * math.sin(ang - 2.4), y - size * 0.6 * math.cos(ang - 2.4))
        self.create_polygon(tip[0], tip[1], left[0], left[1], x, y, right[0], right[1],
                            fill=color, outline=color)

    def _draw_markers(self) -> None:
        for m in self._markers:
            x, y = self._project(m.lat, m.lon)
            self.create_oval(x - 6, y - 6, x + 6, y + 6, outline=m.color, width=2)
            self.create_text(x, y, text=m.glyph, fill=m.color,
                             font=("Consolas", 10, "bold"))
            if m.label:
                self.create_text(x + 10, y - 8, text=m.label, fill=m.color,
                                 anchor="w", font=("Consolas", 9, "bold"))
            if m.sublabel:
                self.create_text(x + 10, y + 4, text=m.sublabel, fill=DARK_TEXT_DIM,
                                 anchor="w", font=("Consolas", 8))

    def _draw_scalebar(self) -> None:
        w, h = self._geometry()
        px_lat, _px_lon = self._scale()
        target_px = min(140, w * 0.3)
        nm = (target_px / px_lat) * mapdata.NM_PER_DEG_LAT
        # Round to a readable 1/2/5 x 10^n value.
        exp = math.floor(math.log10(nm)) if nm > 0 else 0
        base = nm / (10 ** exp)
        nice = 1 if base < 1.5 else 2 if base < 3.5 else 5 if base < 7.5 else 10
        nm = nice * (10 ** exp)
        bar_px = (nm / mapdata.NM_PER_DEG_LAT) * px_lat

        x0, y0 = 12, h - 16
        self.create_line(x0, y0, x0 + bar_px, y0, fill=DARK_TEXT_DIM, width=2)
        self.create_line(x0, y0 - 4, x0, y0 + 4, fill=DARK_TEXT_DIM)
        self.create_line(x0 + bar_px, y0 - 4, x0 + bar_px, y0 + 4, fill=DARK_TEXT_DIM)
        label = f"{nm:g} NM  ({nm * 1.852:g} km)"
        self.create_text(x0 + bar_px / 2, y0 - 9, text=label, fill=DARK_TEXT_DIM,
                         font=("Consolas", 8))

    def _draw_status(self) -> None:
        w, _h = self._geometry()
        if self._loading:
            self.create_text(w - 8, 10, text="loading map data...", anchor="e",
                             fill=DARK_TEXT_DIM, font=("Consolas", 8))
        elif HAVE_MAPDATA and not mapdata.have_map_data():
            self.create_text(w / 2, 20, text="Map data missing from data/map/",
                             fill=ACCENT_RED, font=("Consolas", 10))
        if self._status_text:
            self.create_text(w / 2, 12, text=self._status_text, fill=ACCENT_AMBER,
                             font=("Consolas", 9))
        if not self.world:
            self.create_text(w - 8, self.winfo_height() - 10,
                             text="scroll: zoom   drag: pan   double-click: recentre",
                             anchor="e", fill=COLOR_GRID, font=("Consolas", 8))
