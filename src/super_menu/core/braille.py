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
    "route": "bold cyan",
    "avoid": "yellow",
    "origin": "bold green",
    "destination": "bold red3",
}
_PRIORITY = {"coast": 0, "avoid": 1, "route": 3}  # which layer wins a shared cell
_DEFAULT_PRIORITY = 2
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

def render_geojson(geojson: dict, width: int, height: int, *,
                   basemap: bool = True) -> Text:
    """Render ``geojson`` as a coloured braille map of exactly ``height`` lines.

    The bottom two lines are a scale bar and a marker legend (when there is room);
    the map fills the rest. Degenerate inputs (no coordinates, a single point, a
    zero-width span) render without error."""
    width = max(8, int(width))
    height = max(3, int(height))
    legend_lines = _pack(_legend_entries(geojson), width, max_lines=2)
    # reserve a scale-bar row + however many legend rows there are, but only when
    # the map would still have breathing room
    reserve = (1 + len(legend_lines)) if height >= 6 + len(legend_lines) else 0
    map_rows = height - reserve

    pts = _all_points(geojson)
    if not pts:
        return _blank(height)

    mean_lat = sum(p[1] for p in pts) / len(pts)
    cos_lat = max(0.01, math.cos(math.radians(mean_lat)))

    px = [(p[0] * cos_lat, p[1]) for p in pts]
    min_x, max_x = min(p[0] for p in px), max(p[0] for p in px)
    min_y, max_y = min(p[1] for p in px), max(p[1] for p in px)
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

    # world extent visible in this frame (for clipping the basemap)
    view = ((min_x / cos_lat) - _PAD / scale / cos_lat, min_y - _PAD / scale,
            (max_x / cos_lat) + _PAD / scale / cos_lat, max_y + _PAD / scale)

    if basemap:
        _draw_basemap(canvas, project, view)

    markers: list[tuple[float, float, str, str]] = []
    for geom in _iter_geometries(geojson):
        props = geom.get("_props", {})
        if geom.get("type") == "Point" and geom.get("coordinates"):
            lng, lat = geom["coordinates"][:2]
            mx, my = project(lng, lat)
            markers.append((mx, my, _marker_char(props), _STYLE.get(props.get("kind", ""), "bold")))
            continue
        priority, style = _style_for(props)
        for path in _paths(geom):
            proj = [project(pt[0], pt[1]) for pt in path]
            for (x0, y0), (x1, y1) in zip(proj, proj[1:]):
                canvas.stroke(x0, y0, x1, y1, priority, style)

    for mx, my, ch, style in markers:
        canvas.marker(mx, my, ch, style)

    text = canvas.to_text()
    if reserve:
        text.append(_scale_bar(scale, cos_lat, width))
        for line in legend_lines:
            text.append("\n")
            text.append(Text(line, style="dim"))
    return text


def _draw_basemap(canvas: _Canvas, project, view) -> None:
    vminx, vminy, vmaxx, vmaxy = view
    prio, style = _PRIORITY["coast"], _STYLE["coast"]
    for line in _coastline():
        # cheap reject: skip lines whose bbox misses the view entirely
        xs = [p[0] for p in line]
        ys = [p[1] for p in line]
        if max(xs) < vminx or min(xs) > vmaxx or max(ys) < vminy or min(ys) > vmaxy:
            continue
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
