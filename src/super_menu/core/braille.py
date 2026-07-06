"""Rasterize GeoJSON to a coloured braille map for the terminal.

Pure-stdlib geometry; the only third-party import is ``rich.text`` (always
present via Textual) to carry colour. Each character cell is a 2x4 grid of
individually-addressable braille dots (U+2800–U+28FF), ~4x the resolution of
block characters — the trick drawille / textual-plot / MapSCII use. A
``kind="geojson"`` ``CommandResult`` is rendered through here by both the TUI and
the CLI, so any plugin returning spatial data becomes a map on every surface.

What makes the output readable rather than dots-in-a-void:

* a bundled world **coastline basemap** is drawn faintly under the data, clipped
  to the view, so you can see *where* the geometry is;
* callers may pass real **OSM road polylines** (``roads=``, fetched zoom-aware
  by ``core/roads.py``) drawn as a second underlay — major roads warm, minor
  roads grey — which is what turns the map from a sketch into MapSCII;
* layers are **coloured** by feature ``kind`` (coast dim, routes bright, avoid
  zones amber, endpoints green/red) so they are distinguishable;
* ``Point`` features become **labelled markers** with a legend;
* a **scale bar** gives real-world distance.

Longitude is corrected by ``cos(latitude)`` and both axes share one scale, so a
circular avoid-zone stays circular.
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

from rich.text import Text

_DOTS = [[0x01, 0x02, 0x04, 0x40], [0x08, 0x10, 0x20, 0x80]]  # [col 0..1][row 0..3]
_PAD = 2

# Style per feature ``kind`` (Rich style strings). Unknown kinds fall back to plain.
_STYLE = {
    "coast": "grey37",
    "baseline": "dark_cyan",   # the unconstrained ghost route, under everything
    "route": "bold cyan",
    "avoid": "yellow",
    "origin": "bold green",
    "destination": "bold red3",
}
_PRIORITY = {"coast": 0, "baseline": 1, "avoid": 2, "route": 4}  # shared-cell winner
_DEFAULT_PRIORITY = 3

# Road underlay styling by OSM highway class (drawn at coast priority, after the
# coastline, so roads win shared basemap cells but never cover plugin data).
_ROAD_STYLE = {
    "motorway": "orange3",
    "trunk": "dark_orange3",
    "primary": "grey66",
    "secondary": "grey50",
}
_ROAD_DEFAULT_STYLE = "grey35"
_SCALE_STEPS = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000]
_KM_PER_DEG = 111.32


# --------------------------------------------------------------------------- #
# bundled coastline basemap
# --------------------------------------------------------------------------- #

@lru_cache(maxsize=1)
def _coastline() -> list[list[list[float]]]:
    """The bundled world coastline as a list of ``[lng, lat]`` polylines."""
    path = Path(__file__).parent / "data" / "coastline.json"
    try:
        fc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return fc["features"][0]["geometry"]["coordinates"]


# --------------------------------------------------------------------------- #
# canvas
# --------------------------------------------------------------------------- #

class _Canvas:
    def __init__(self, cols: int, rows: int):
        self.cols, self.rows = cols, rows
        self.dx, self.dy = cols * 2, rows * 4
        self._bits = [[0] * cols for _ in range(rows)]
        self._style = [[""] * cols for _ in range(rows)]
        self._prio = [[-1] * cols for _ in range(rows)]
        self._over: list[list[Optional[tuple[str, str]]]] = [[None] * cols for _ in range(rows)]

    def plot(self, x: float, y: float, priority: int, style: str) -> None:
        ix, iy = int(x), int(y)
        if not (0 <= ix < self.dx and 0 <= iy < self.dy):
            return
        r, c = iy // 4, ix // 2
        self._bits[r][c] |= _DOTS[ix % 2][iy % 4]
        if priority >= self._prio[r][c]:
            self._prio[r][c] = priority
            self._style[r][c] = style

    def stroke(self, x0, y0, x1, y1, priority, style) -> None:
        steps = int(max(abs(x1 - x0), abs(y1 - y0)))
        for i in range(steps + 1):
            t = i / steps if steps else 0.0
            self.plot(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, priority, style)

    def marker(self, x: float, y: float, ch: str, style: str) -> None:
        cx, cy = int(x) // 2, int(y) // 4
        if 0 <= cx < self.cols and 0 <= cy < self.rows:
            self._over[cy][cx] = (ch[:1], style)

    def to_text(self) -> Text:
        text = Text(no_wrap=True, overflow="crop")
        for r in range(self.rows):
            run, run_style = "", None
            for c in range(self.cols):
                over = self._over[r][c]
                if over is not None:
                    ch, style = over
                elif self._bits[r][c]:
                    ch, style = chr(0x2800 + self._bits[r][c]), self._style[r][c]
                else:
                    ch, style = " ", ""
                if style != run_style:
                    if run:
                        text.append(run, run_style or None)
                    run, run_style = ch, style
                else:
                    run += ch
            if run:
                text.append(run, run_style or None)
            text.append("\n")
        return text


# --------------------------------------------------------------------------- #
# GeoJSON walking
# --------------------------------------------------------------------------- #

def _iter_geometries(node: dict) -> Iterable[dict]:
    t = node.get("type")
    if t == "FeatureCollection":
        for f in node.get("features", []):
            yield from _iter_geometries(f)
    elif t == "Feature":
        geom = node.get("geometry")
        if geom:
            g = dict(geom)
            g["_props"] = node.get("properties", {}) or {}
            yield g
    elif t:
        yield node


def _paths(geom: dict) -> list[list[list[float]]]:
    t, c = geom.get("type"), geom.get("coordinates")
    if not c:
        return []
    if t == "LineString":
        return [c]
    if t == "MultiLineString":
        return list(c)
    if t == "Polygon":
        return list(c)
    if t == "MultiPolygon":
        return [ring for poly in c for ring in poly]
    return []


def _all_points(geojson: dict) -> list[list[float]]:
    pts: list[list[float]] = []
    for geom in _iter_geometries(geojson):
        t, c = geom.get("type"), geom.get("coordinates")
        if not c:
            continue
        if t == "Point":
            pts.append(c)
        else:
            for path in _paths(geom):
                pts.extend(path)
    return pts


def _marker_char(props: dict) -> str:
    m = props.get("marker")
    if isinstance(m, str) and m:
        return m[:1]
    for key in ("label", "kind"):
        v = props.get(key)
        if isinstance(v, str) and v:
            return v[0].upper()
    return "•"


def _style_for(props: dict) -> tuple[int, str]:
    kind = props.get("kind", "")
    return _PRIORITY.get(kind, _DEFAULT_PRIORITY), _STYLE.get(kind, "")


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #

def data_bbox(geojson: dict) -> Optional[tuple[float, float, float, float]]:
    """``(min_lng, min_lat, max_lng, max_lat)`` over every coordinate, or ``None``."""
    pts = _all_points(geojson)
    if not pts:
        return None
    lngs = [p[0] for p in pts]
    lats = [p[1] for p in pts]
    return (min(lngs), min(lats), max(lngs), max(lats))


def render_geojson(geojson: dict, width: int, height: int, *, basemap: bool = True,
                   view: Optional[tuple[float, float, float, float]] = None,
                   waypoints: bool = False, roads: Optional[list] = None) -> Text:
    """Render ``geojson`` as a coloured braille map of exactly ``height`` lines.

    ``view`` frames a specific ``(min_lng, min_lat, max_lng, max_lat)`` window
    (for zoom/pan); anything outside it is clipped. ``waypoints`` overlays a
    marker on every line vertex. ``roads`` is an optional underlay of
    ``[highway_class, [[lng, lat], ...]]`` pairs (see ``core/roads.py``) drawn
    between the coastline and the data. The bottom rows are a scale bar and a
    marker legend when there is room; the map fills the rest. Degenerate inputs
    (no coordinates, a single point, a zero-width span) render without error."""
    width = max(8, int(width))
    height = max(3, int(height))
    legend_lines = _pack(_legend_entries(geojson), width, max_lines=2)
    reserve = (1 + len(legend_lines)) if height >= 6 + len(legend_lines) else 0
    map_rows = height - reserve

    frame = view or data_bbox(geojson)
    if frame is None:
        return _blank(height)
    fminlng, fminlat, fmaxlng, fmaxlat = frame
    cos_lat = max(0.01, math.cos(math.radians((fminlat + fmaxlat) / 2)))
    min_x, max_x = fminlng * cos_lat, fmaxlng * cos_lat
    min_y, max_y = fminlat, fmaxlat
    span_x = (max_x - min_x) or 1e-9
    span_y = (max_y - min_y) or 1e-9

    canvas = _Canvas(width, map_rows)
    avail_x, avail_y = canvas.dx - 2 * _PAD, canvas.dy - 2 * _PAD
    scale = min(avail_x / span_x, avail_y / span_y)
    off_x = _PAD + (avail_x - span_x * scale) / 2
    off_y = _PAD + (avail_y - span_y * scale) / 2

    def project(lng: float, lat: float) -> tuple[float, float]:
        return (off_x + (lng * cos_lat - min_x) * scale,
                off_y + (max_y - lat) * scale)     # invert lat: north up

    if basemap:
        _draw_basemap(canvas, project, frame)
    if roads:
        _draw_roads(canvas, project, frame, roads)

    markers: list[tuple[float, float, str, str]] = []
    wp_markers: list[tuple[float, float]] = []
    for geom in _iter_geometries(geojson):
        props = geom.get("_props", {})
        if geom.get("type") == "Point" and geom.get("coordinates"):
            lng, lat = geom["coordinates"][:2]
            mx, my = project(lng, lat)
            markers.append((mx, my, _marker_char(props), _STYLE.get(props.get("kind", ""), "bold")))
            continue
        priority, style = _style_for(props)
        is_line = geom.get("type") in ("LineString", "MultiLineString")
        for path in _paths(geom):
            proj = [project(pt[0], pt[1]) for pt in path]
            for (x0, y0), (x1, y1) in zip(proj, proj[1:]):
                canvas.stroke(x0, y0, x1, y1, priority, style)
            if waypoints and is_line:  # zone-polygon rings aren't waypoints
                wp_markers.extend(proj)

    for mx, my in wp_markers:            # waypoint dots first — named markers win ties
        canvas.marker(mx, my, "◇", "bold bright_white")
    for mx, my, ch, style in markers:
        canvas.marker(mx, my, ch, style)

    text = canvas.to_text()
    if reserve:
        text.append(_scale_bar(scale, cos_lat, width))
        if roads:
            text.append(Text("  roads © OSM", style="dim"))
        for line in legend_lines:
            text.append("\n")
            text.append(Text(line, style="dim"))
    return text


def _draw_basemap(canvas: _Canvas, project, frame) -> None:
    fminlng, fminlat, fmaxlng, fmaxlat = frame
    prio, style = _PRIORITY["coast"], _STYLE["coast"]
    for line in _coastline():
        # cheap reject: skip lines whose bbox misses the framed window entirely
        xs = [p[0] for p in line]
        ys = [p[1] for p in line]
        if max(xs) < fminlng or min(xs) > fmaxlng or max(ys) < fminlat or min(ys) > fmaxlat:
            continue
        proj = [project(p[0], p[1]) for p in line]
        for (x0, y0), (x1, y1) in zip(proj, proj[1:]):
            canvas.stroke(x0, y0, x1, y1, prio, style)


def _draw_roads(canvas: _Canvas, project, frame, roads: list) -> None:
    fminlng, fminlat, fmaxlng, fmaxlat = frame
    prio = _PRIORITY["coast"]
    for cls, line in roads:
        # cheap reject, same as the coastline: skip lines fully outside the view
        xs = [p[0] for p in line]
        ys = [p[1] for p in line]
        if max(xs) < fminlng or min(xs) > fmaxlng or max(ys) < fminlat or min(ys) > fmaxlat:
            continue
        style = _ROAD_STYLE.get(cls, _ROAD_DEFAULT_STYLE)
        proj = [project(p[0], p[1]) for p in line]
        for (x0, y0), (x1, y1) in zip(proj, proj[1:]):
            canvas.stroke(x0, y0, x1, y1, prio, style)


def _scale_bar(scale: float, cos_lat: float, width: int) -> Text:
    """A ``├──┤ N km`` bar sized to the projection (dim)."""
    km_per_dot = _KM_PER_DEG / scale                       # planar unit ≈ 1° lat
    target = _SCALE_STEPS[0]
    for step in _SCALE_STEPS:
        if step / km_per_dot / 2 <= width * 0.4:           # bar cells within 40% width
            target = step
    cells = max(3, round(target / km_per_dot / 2))
    bar = "├" + "─" * max(1, cells - 2) + "┤"
    return Text(f"{bar} {target} km", style="dim")


def _blank(height: int) -> Text:
    text = Text(no_wrap=True)
    for _ in range(height):
        text.append(" \n")
    return text


def _legend_entries(geojson: dict) -> list[str]:
    """``marker label`` strings, one per distinct Point marker."""
    seen: dict[str, str] = {}
    for geom in _iter_geometries(geojson):
        if geom.get("type") != "Point":
            continue
        props = geom.get("_props", {})
        label = props.get("label") or props.get("kind")
        if label:
            seen.setdefault(_marker_char(props), str(label))
    return [f"{m} {lbl}" for m, lbl in seen.items()]


def _pack(entries: list[str], width: int, max_lines: int) -> list[str]:
    """Greedily pack ``entries`` into up to ``max_lines`` lines of ``width``; the
    final line absorbs any overflow and is truncated with an ellipsis."""
    if not entries:
        return []
    lines: list[str] = []
    cur, i = "", 0
    while i < len(entries):
        piece = entries[i] if not cur else f"{cur}   {entries[i]}"
        if len(piece) <= width or not cur:
            cur, i = piece, i + 1
        elif len(lines) == max_lines - 1:
            break                              # last line: stop wrapping, dump rest below
        else:
            lines.append(cur)
            cur = ""
    if i < len(entries):                        # overflow onto the current line
        rest = "   ".join(entries[i:])
        cur = f"{cur}   {rest}" if cur else rest
    if cur:
        lines.append(cur)
    return [ln if len(ln) <= width else ln[: max(1, width - 1)] + "…" for ln in lines]


def legend(geojson: dict) -> Optional[str]:
    """One-line ``marker label`` legend from the object's Point features."""
    entries = _legend_entries(geojson)
    return "   ".join(entries) if entries else None
