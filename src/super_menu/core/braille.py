"""Rasterize GeoJSON to a braille map for the terminal.

Pure stdlib. Each character cell is a 2x4 grid of individually-addressable
braille dots (U+2800–U+28FF), giving ~4x the resolution of block characters —
the same trick drawille / textual-plot / MapSCII use. A ``kind="geojson"``
``CommandResult`` is rendered through here by both the TUI and the CLI, so any
plugin that returns spatial data gets a map on every surface for free.

The renderer is generic: it draws every ``LineString``/``Polygon`` ring as
braille strokes and drops a single-character marker for each ``Point`` (taken
from the point feature's ``marker`` property, else the first letter of its
``label``/``kind``). Longitudes are corrected by ``cos(latitude)`` and the whole
thing is scaled uniformly, so a circular avoid-zone still looks circular.
"""
from __future__ import annotations

import math
from typing import Iterable, Optional

# Braille dot bit masks, indexed [col in 0..1][row in 0..3].
_DOTS = [[0x01, 0x02, 0x04, 0x40], [0x08, 0x10, 0x20, 0x80]]
_PAD = 2  # dot margin around the drawing


class _Canvas:
    def __init__(self, cols: int, rows: int):
        self.cols, self.rows = cols, rows
        self.dx, self.dy = cols * 2, rows * 4
        self._cells = [[0] * cols for _ in range(rows)]
        self._over = [[" "] * cols for _ in range(rows)]

    def plot(self, x: float, y: float) -> None:
        ix, iy = int(x), int(y)
        if 0 <= ix < self.dx and 0 <= iy < self.dy:
            self._cells[iy // 4][ix // 2] |= _DOTS[ix % 2][iy % 4]

    def marker(self, x: float, y: float, ch: str) -> None:
        cx, cy = int(x) // 2, int(y) // 4
        if 0 <= cx < self.cols and 0 <= cy < self.rows:
            self._over[cy][cx] = ch[:1]

    def line(self, x0: float, y0: float, x1: float, y1: float) -> None:
        steps = int(max(abs(x1 - x0), abs(y1 - y0)))
        for i in range(steps + 1):
            t = i / steps if steps else 0.0
            self.plot(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)

    def to_lines(self) -> list[str]:
        out: list[str] = []
        for r in range(self.rows):
            row = []
            for c in range(self.cols):
                if self._over[r][c] != " ":
                    row.append(self._over[r][c])
                else:
                    bits = self._cells[r][c]
                    row.append(chr(0x2800 + bits) if bits else " ")
            out.append("".join(row).rstrip() or " ")
        return out


def _iter_geometries(node: dict) -> Iterable[dict]:
    """Yield every geometry in a GeoJSON object (FeatureCollection/Feature/geom)."""
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


def _rings(geom: dict) -> list[list[list[float]]]:
    """Coordinate paths (each a list of [lng, lat]) to stroke for this geometry."""
    t, c = geom.get("type"), geom.get("coordinates")
    if not c:
        return []
    if t == "LineString":
        return [c]
    if t == "MultiLineString":
        return list(c)
    if t == "Polygon":
        return list(c)  # exterior + holes
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
            for ring in _rings(geom):
                pts.extend(ring)
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


def render_geojson(geojson: dict, width: int, height: int) -> list[str]:
    """Return ``height`` lines of braille art depicting ``geojson``.

    Distortion-free: longitude is scaled by ``cos(mean latitude)`` and both axes
    share one scale, so shapes keep their proportions. Degenerate inputs (no
    coordinates, a single point, a zero-width span) render without error."""
    width = max(8, int(width))
    height = max(3, int(height))
    pts = _all_points(geojson)
    if not pts:
        return [" "] * height

    mean_lat = sum(p[1] for p in pts) / len(pts)
    cos_lat = max(0.01, math.cos(math.radians(mean_lat)))

    def planar(lng: float, lat: float) -> tuple[float, float]:
        return lng * cos_lat, lat

    px = [planar(p[0], p[1]) for p in pts]
    min_x = min(p[0] for p in px)
    max_x = max(p[0] for p in px)
    min_y = min(p[1] for p in px)
    max_y = max(p[1] for p in px)
    span_x = (max_x - min_x) or 1e-9
    span_y = (max_y - min_y) or 1e-9

    canvas = _Canvas(width, height)
    avail_x = canvas.dx - 2 * _PAD
    avail_y = canvas.dy - 2 * _PAD
    scale = min(avail_x / span_x, avail_y / span_y)
    # centre the drawing within the available dot area
    off_x = _PAD + (avail_x - span_x * scale) / 2
    off_y = _PAD + (avail_y - span_y * scale) / 2

    def project(lng: float, lat: float) -> tuple[float, float]:
        x, y = planar(lng, lat)
        return (off_x + (x - min_x) * scale,
                off_y + (max_y - y) * scale)  # invert lat: north is up

    markers: list[tuple[float, float, str]] = []
    for geom in _iter_geometries(geojson):
        if geom.get("type") == "Point" and geom.get("coordinates"):
            lng, lat = geom["coordinates"][:2]
            mx, my = project(lng, lat)
            markers.append((mx, my, _marker_char(geom.get("_props", {}))))
            continue
        for ring in _rings(geom):
            proj = [project(pt[0], pt[1]) for pt in ring]
            for (x0, y0), (x1, y1) in zip(proj, proj[1:]):
                canvas.line(x0, y0, x1, y1)

    for mx, my, ch in markers:  # markers on top of strokes
        canvas.marker(mx, my, ch)
    return canvas.to_lines()


def legend(geojson: dict) -> Optional[str]:
    """A one-line ``marker label`` legend from the object's Point features."""
    seen: dict[str, str] = {}
    for geom in _iter_geometries(geojson):
        if geom.get("type") != "Point":
            continue
        props = geom.get("_props", {})
        label = props.get("label") or props.get("kind")
        if label:
            seen.setdefault(_marker_char(props), str(label))
    if not seen:
        return None
    return "   ".join(f"{m} {lbl}" for m, lbl in seen.items())
