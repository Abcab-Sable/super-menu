"""Zoom-aware OSM road underlay for the braille map (Overpass API).

``braille.py`` stays a pure rasterizer; this module is the network side of the
"MapSCII for the terminal" goal. A surface (TUI widget, CLI) asks for the road
polylines covering its current view; which highway classes come back depends on
how zoomed-in the view is — motorways at country scale, residential streets at
town scale — exactly how slippy-map tile servers thin their data.

Design constraints, in order:

* **offline-safe** — any failure (no network, Overpass down, rate-limited)
  returns ``[]`` and the map renders exactly as before. ``SUPER_MENU_OFFLINE=1``
  short-circuits without touching the network (tests/CI set this).
* **cache-friendly** — the requested bbox is padded and quantized to a coarse
  lattice, so small pans and repeated renders hit the same key. Parsed +
  decimated polylines persist under ``data_home()/basemap/`` (a few hundred KB,
  vs multi-MB raw Overpass JSON) and are memoized in-process.
* **bounded** — points closer than ~1/1200 of the view span are decimated away,
  so render cost tracks the braille canvas, not OSM's vertex density.

The public entry point is :func:`roads_for_view`; everything it returns is
JSON-serializable ``[highway_class, [[lng, lat], ...]]`` pairs, the shape
``braille.render_geojson(roads=...)`` draws.
"""
from __future__ import annotations

import json
import math
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from . import config

DEFAULT_OVERPASS = "https://overpass-api.de/api/interpreter"

# (max view span in degrees, highway classes to draw). First match wins; wider
# views get fewer classes so country-scale maps stay legible and light.
_TIERS: list[tuple[float, tuple[str, ...]]] = [
    (0.15, ("motorway", "trunk", "primary", "secondary", "tertiary",
            "unclassified", "residential")),
    (0.5, ("motorway", "trunk", "primary", "secondary", "tertiary")),
    (1.5, ("motorway", "trunk", "primary", "secondary")),
    (2.5, ("motorway", "trunk", "primary")),
    (8.0, ("motorway", "trunk")),
]

# Quantization step for the fetch bbox, by view span: coarse when zoomed out so
# a pan across half the country reuses one cached fetch, fine when zoomed in.
_STEPS = [(0.25, 0.05), (1.0, 0.25), (4.0, 1.0), (math.inf, 5.0)]

_PAD = 0.30          # fetch this much extra around the view (fraction per side)
_SLOW_SPAN = 2.5     # above this span an uncached fetch takes ~a minute (60 MB+);
                     # only background callers (allow_slow=True) attempt it
_DECIMATE = 600.0    # keep points no closer than span / this (~half a braille dot)
_ROUND = 5           # cache-file coordinate precision (1e-5° ≈ 1 m)
_MAX_WAYS = 60000    # Overpass reply cap — a braille canvas can't show more anyway
_FAIL_TTL = 120.0    # seconds to back off after a failed fetch
_MEMO_CAP = 32

FetchFn = Callable[[tuple[float, float, float, float], tuple[str, ...]], list]

_memo: dict[str, list] = {}
_failures: dict[str, float] = {}


def disabled() -> bool:
    return os.environ.get("SUPER_MENU_OFFLINE", "").lower() in ("1", "true", "yes")


def classes_for_span(span: float) -> Optional[tuple[str, ...]]:
    """Highway classes for a view ``span`` degrees wide, or ``None`` when the
    view is too wide to sensibly fetch roads (world scale — coastline only)."""
    for max_span, classes in _TIERS:
        if span <= max_span:
            return classes
    return None


def fetch_bbox(view: tuple[float, float, float, float]
               ) -> tuple[float, float, float, float]:
    """Pad ``view`` by ``_PAD`` per side and snap outward to a coarse lattice,
    so nearby views share one cache entry. Same in/out format as ``view``:
    ``(min_lng, min_lat, max_lng, max_lat)``."""
    minlng, minlat, maxlng, maxlat = view
    span = max(maxlng - minlng, maxlat - minlat, 1e-6)
    pad = span * _PAD
    step = next(s for max_span, s in _STEPS if span <= max_span)
    return (math.floor((minlng - pad) / step) * step,
            math.floor((minlat - pad) / step) * step,
            math.ceil((maxlng + pad) / step) * step,
            math.ceil((maxlat + pad) / step) * step)


def roads_for_view(view: tuple[float, float, float, float], *,
                   fetch: Optional[FetchFn] = None, allow_slow: bool = False) -> list:
    """Road polylines covering ``view`` as ``[class, [[lng, lat], ...]]`` pairs.

    Never raises: no network, an Overpass error, or a too-wide view all yield
    ``[]`` (and failures are negative-cached for ``_FAIL_TTL`` seconds so an
    interactive surface doesn't hammer a down endpoint on every pan).

    Country-scale fetches (span > ``_SLOW_SPAN``) can take ~a minute on the
    public Overpass API, so an uncached one only runs when ``allow_slow`` is
    set — the TUI's background worker does; the synchronous CLI doesn't, and
    simply renders roadless until some slow caller has filled the cache."""
    if disabled():
        return []
    span = max(view[2] - view[0], view[3] - view[1])
    classes = classes_for_span(span)
    if classes is None:
        return []
    bbox = fetch_bbox(view)
    key = f"t{len(classes)}-{bbox[0]:.2f}_{bbox[1]:.2f}_{bbox[2]:.2f}_{bbox[3]:.2f}"

    hit = _memo.get(key)
    if hit is not None:
        return hit
    path = _cache_dir() / f"roads-{key}.json"
    try:
        lines = json.loads(path.read_text(encoding="utf-8"))
        _remember(key, lines)
        return lines
    except (OSError, json.JSONDecodeError):
        pass
    if time.monotonic() - _failures.get(key, -_FAIL_TTL) < _FAIL_TTL:
        return []
    if span > _SLOW_SPAN and not allow_slow:
        return []                              # not a failure — a slow caller may fill it

    try:
        lines = (fetch or _fetch_overpass)(bbox, classes)
        lines = decimate(lines, max(bbox[2] - bbox[0], bbox[3] - bbox[1]) / _DECIMATE)
    except Exception:
        _failures[key] = time.monotonic()
        return []
    if lines:                                  # an empty reply is suspect (rate limit,
        try:                                   # truncation) — memoize it, never persist it
            path.write_text(json.dumps(lines), encoding="utf-8")
        except OSError:
            pass                               # cache is an optimization, not a need
    _remember(key, lines)
    return lines


def parse_overpass(payload: dict) -> list:
    """``[class, [[lng, lat], ...]]`` pairs from an Overpass ``out geom`` reply."""
    lines = []
    for el in payload.get("elements", []):
        geometry = el.get("geometry") or []
        if el.get("type") != "way" or len(geometry) < 2:
            continue
        cls = (el.get("tags") or {}).get("highway", "")
        lines.append([cls, [[p["lon"], p["lat"]] for p in geometry]])
    return lines


def decimate(lines: list, tol: float) -> list:
    """Drop consecutive points closer than ``tol`` (L∞), keeping endpoints, and
    round coordinates to ``_ROUND`` decimals — halves the cache-file size."""
    out = []
    for cls, pts in lines:
        kept = [pts[0]]
        for p in pts[1:-1]:
            if max(abs(p[0] - kept[-1][0]), abs(p[1] - kept[-1][1])) >= tol:
                kept.append(p)
        kept.append(pts[-1])
        if len(kept) >= 2:
            out.append([cls, [[round(p[0], _ROUND), round(p[1], _ROUND)]
                              for p in kept]])
    return out


# --------------------------------------------------------------------------- #
# plumbing
# --------------------------------------------------------------------------- #

def _fetch_overpass(bbox: tuple[float, float, float, float],
                    classes: tuple[str, ...]) -> list:
    minlng, minlat, maxlng, maxlat = bbox
    query = (
        f"[out:json][timeout:90];"
        f'way["highway"~"^({"|".join(classes)})$"]'
        f"({minlat},{minlng},{maxlat},{maxlng});"
        f"out geom {_MAX_WAYS};"
    )
    url = os.environ.get("OVERPASS_URL", DEFAULT_OVERPASS)
    req = urllib.request.Request(
        url,
        data=urllib.parse.urlencode({"data": query}).encode("utf-8"),
        method="POST",
        headers={"User-Agent": "super-menu/0.1",
                 "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
        payload = json.loads(resp.read().decode("utf-8"))
    # Overpass reports server-side timeouts/truncation as a 200 with a "remark";
    # surface it as a failure so a partial reply is never cached as the truth.
    if payload.get("remark"):
        raise RuntimeError(str(payload["remark"]))
    return parse_overpass(payload)


def _cache_dir() -> Path:
    d = config.data_home() / "basemap"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _remember(key: str, lines: list) -> None:
    if len(_memo) >= _MEMO_CAP:
        _memo.pop(next(iter(_memo)))
    _memo[key] = lines
