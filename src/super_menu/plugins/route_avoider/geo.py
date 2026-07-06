"""Pure geometry + parsing for area-avoidance routing.

Analogue of free_for_dev's ``fetch.py`` and git_tools' ``gitio.py``: no
``Plugin``/``Command`` imports and no network, so every function here is
unit-testable against literal inputs. The routing engines live in ``adapter.py``;
this module only turns pins + radii into the GeoJSON polygons those engines
consume and answers geometric questions about them.

The one primitive the whole feature hinges on is *avoid-polygons* — a pin with a
radius is just a circle approximated as a polygon (``circle_ring``), and a set of
those circles is what the router is told to route around. Coordinates are emitted
in GeoJSON order — ``[lng, lat]`` — throughout.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

EARTH_KM = 6371.0088  # mean Earth radius (IUGG)

MAX_RADIUS_KM = 500.0  # a single avoid circle wider than this is almost certainly a typo

# GeoJSON ring: a closed list of [lng, lat] pairs.
Ring = list[list[float]]


@dataclass
class AvoidSpec:
    """One avoid zone: a centre + radius. ``lat``/``lng`` are ``None`` while the
    zone is still a named place waiting to be geocoded by the routing adapter."""

    radius_km: float
    label: str = ""
    lat: Optional[float] = None
    lng: Optional[float] = None
    query: Optional[str] = None  # set for a named zone (``"Heathrow@10"``) until resolved

    @property
    def resolved(self) -> bool:
        return self.lat is not None and self.lng is not None


# --------------------------------------------------------------------------- #
# distance + circle geometry
# --------------------------------------------------------------------------- #

def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two points in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_KM * math.asin(min(1.0, math.sqrt(a)))


def destination_point(lat: float, lng: float, bearing_deg: float,
                      dist_km: float) -> tuple[float, float]:
    """Point reached by travelling ``dist_km`` from (lat, lng) on ``bearing_deg``.

    Standard great-circle direct formula — used to trace a geodesic circle so the
    radius holds at any latitude (a naive lat/lng box would squash east-west)."""
    ang = dist_km / EARTH_KM
    brg = math.radians(bearing_deg)
    p1 = math.radians(lat)
    l1 = math.radians(lng)
    p2 = math.asin(math.sin(p1) * math.cos(ang) +
                   math.cos(p1) * math.sin(ang) * math.cos(brg))
    l2 = l1 + math.atan2(math.sin(brg) * math.sin(ang) * math.cos(p1),
                         math.cos(ang) - math.sin(p1) * math.sin(p2))
    # normalise longitude to [-180, 180]
    lng2 = (math.degrees(l2) + 540) % 360 - 180
    return math.degrees(p2), lng2


def circle_ring(lat: float, lng: float, radius_km: float, steps: int = 64) -> Ring:
    """A closed GeoJSON ring approximating a circle of ``radius_km`` around a point.

    The router's "customisable radius" reduces to exactly this: a pin + radius is a
    polygon with ``steps`` vertices. The ring is closed (first point repeated)."""
    if radius_km <= 0:
        raise ValueError("radius must be positive")
    ring: Ring = []
    for i in range(steps):
        plat, plng = destination_point(lat, lng, 360.0 * i / steps, radius_km)
        ring.append([round(plng, 6), round(plat, 6)])
    ring.append(ring[0])  # close it
    return ring


def point_in_ring(lng: float, lat: float, ring: Ring) -> bool:
    """Ray-casting point-in-polygon test on a ``[lng, lat]`` ring."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > lat) != (yj > lat)) and (
            lng < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi
        ):
            inside = not inside
        j = i
    return inside


def point_in_any(lng: float, lat: float, rings: list[Ring]) -> bool:
    return any(point_in_ring(lng, lat, r) for r in rings)


# --------------------------------------------------------------------------- #
# parsing user input
# --------------------------------------------------------------------------- #

def parse_point(text: str) -> Optional[tuple[float, float]]:
    """Parse ``"lat,lng"`` into ``(lat, lng)``; return ``None`` if it is not a
    coordinate pair (so the caller can treat it as a place name to geocode)."""
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 2:
        return None
    try:
        lat, lng = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    _validate_latlng(lat, lng)
    return lat, lng


def parse_avoid_spec(spec: str) -> list[AvoidSpec]:
    """Parse the ``avoid`` string into a list of :class:`AvoidSpec`.

    Grammar (zones separated by ``;``)::

        lat,lng,radius_km[,label]      e.g.  53.99,-1.69,12,Menwith Hill
        name@radius_km                 e.g.  Heathrow@10

    Raises ``ValueError`` with an actionable message on malformed input."""
    specs: list[AvoidSpec] = []
    for raw in spec.split(";"):
        token = raw.strip()
        if not token:
            continue
        if "@" in token:
            name, _, r = token.rpartition("@")
            name = name.strip()
            if not name:
                raise ValueError(f"avoid zone '{token}' is missing a place name before '@'")
            radius = _parse_radius(r, token)
            specs.append(AvoidSpec(radius_km=radius, label=name, query=name))
            continue
        parts = [p.strip() for p in token.split(",")]
        if len(parts) < 3:
            raise ValueError(
                f"avoid zone '{token}' must be 'lat,lng,radius_km' or 'name@radius_km'"
            )
        try:
            lat, lng = float(parts[0]), float(parts[1])
        except ValueError as exc:
            raise ValueError(f"avoid zone '{token}' has a non-numeric coordinate") from exc
        _validate_latlng(lat, lng)
        radius = _parse_radius(parts[2], token)
        label = ",".join(parts[3:]).strip()  # a label may legitimately contain commas
        specs.append(AvoidSpec(radius_km=radius, label=label, lat=lat, lng=lng))
    return specs


def _parse_radius(text: str, token: str) -> float:
    try:
        radius = float(text.strip())
    except ValueError as exc:
        raise ValueError(f"avoid zone '{token}' has a non-numeric radius") from exc
    if radius <= 0:
        raise ValueError(f"avoid zone '{token}' needs a radius greater than 0 km")
    if radius > MAX_RADIUS_KM:
        raise ValueError(
            f"avoid zone '{token}' radius {radius} km exceeds the {MAX_RADIUS_KM:g} km cap"
        )
    return radius


def _validate_latlng(lat: float, lng: float) -> None:
    if not -90.0 <= lat <= 90.0:
        raise ValueError(f"latitude {lat} is out of range (-90..90)")
    if not -180.0 <= lng <= 180.0:
        raise ValueError(f"longitude {lng} is out of range (-180..180)")


# --------------------------------------------------------------------------- #
# assembling polygons for the router
# --------------------------------------------------------------------------- #

def specs_to_rings(specs: list[AvoidSpec], steps: int = 64) -> list[Ring]:
    """Circle-approximate every *resolved* spec into a GeoJSON ring.

    Named specs must be geocoded (``lat``/``lng`` filled in) before this is called.
    Overlap-merging/simplification (proposal Phase 2, needs shapely) is deliberately
    not done here — the adapter is handed the raw circle set."""
    rings: list[Ring] = []
    for s in specs:
        if not s.resolved:
            raise ValueError(f"avoid zone '{s.query or s.label}' was never geocoded")
        rings.append(circle_ring(s.lat, s.lng, s.radius_km, steps))  # type: ignore[arg-type]
    return rings


def multipolygon(rings: list[Ring]) -> dict:
    """Wrap circle rings as a single GeoJSON MultiPolygon geometry — the shape the
    router's avoid-polygons option expects."""
    return {"type": "MultiPolygon", "coordinates": [[ring] for ring in rings]}


def bbox_of(coords: list[list[float]]) -> Optional[list[float]]:
    """``[min_lng, min_lat, max_lng, max_lat]`` for a list of ``[lng, lat]`` points."""
    if not coords:
        return None
    lngs = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return [min(lngs), min(lats), max(lngs), max(lats)]


def feature_collection(route_geometry: dict, specs: list[AvoidSpec],
                       origin: tuple[float, float], destination: tuple[float, float],
                       origin_label: str = "start",
                       destination_label: str = "end",
                       baseline_geometry: dict | None = None) -> dict:
    """Bundle the route line, avoid circles, and labelled endpoints into one
    GeoJSON FeatureCollection — the renderer draws it as a map, and it drops
    straight into geojson.io. Each avoid circle gets a numbered centre marker and
    the endpoints get ``A``/``B`` markers so the map has a legend.

    ``baseline_geometry`` is the *unconstrained* route (no zones, no motorway
    flag): renderers draw it as a ghost line under the real route, so the user
    can see where the avoidance actually bent the path."""
    features: list[dict] = []
    if baseline_geometry:   # first, so everything above draws over the ghost
        features.append({"type": "Feature", "properties": {"kind": "baseline"},
                         "geometry": baseline_geometry})
    features.append(
        {"type": "Feature", "properties": {"kind": "route"}, "geometry": route_geometry})
    for i, s in enumerate((s for s in specs if s.resolved), start=1):
        features.append({
            "type": "Feature",
            "properties": {"kind": "avoid", "label": s.label, "radius_km": s.radius_km},
            "geometry": {"type": "Polygon",
                         "coordinates": [circle_ring(s.lat, s.lng, s.radius_km)]},  # type: ignore[arg-type]
        })
        features.append({  # numbered centre marker so the zone shows up in the legend
            "type": "Feature",
            "properties": {"kind": "avoid", "marker": str(i), "label": s.label or f"zone {i}"},
            "geometry": {"type": "Point", "coordinates": [s.lng, s.lat]},
        })
    for kind, marker, label, (lat, lng) in (
        ("origin", "A", origin_label, origin),
        ("destination", "B", destination_label, destination),
    ):
        features.append({
            "type": "Feature",
            "properties": {"kind": kind, "marker": marker, "label": label},
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
        })
    return {"type": "FeatureCollection", "features": features}


# --------------------------------------------------------------------------- #
# seeded preset avoid layers (proposal Phase 1: ship 1–2 static layers)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    note: str
    zones: tuple[tuple[float, float, float, str], ...]  # (lat, lng, radius_km, label)


PRESETS: dict[str, Preset] = {
    "uk_cities": Preset(
        key="uk_cities",
        label="UK major urban cores",
        note="Central London, Birmingham, Manchester, Leeds, Bristol, Glasgow.",
        zones=(
            (51.5074, -0.1278, 20.0, "London"),
            (52.4862, -1.8904, 12.0, "Birmingham"),
            (53.4808, -2.2426, 12.0, "Manchester"),
            (53.8008, -1.5491, 10.0, "Leeds"),
            (51.4545, -2.5879, 10.0, "Bristol"),
            (55.8642, -4.2518, 12.0, "Glasgow"),
        ),
    ),
    "london_congestion": Preset(
        key="london_congestion",
        label="Central London congestion zone (approx.)",
        note="A single ~6 km circle over the central charging area.",
        zones=((51.5140, -0.1300, 6.0, "Central London"),),
    ),
}


def preset_specs(key: str) -> list[AvoidSpec]:
    """Resolved avoid specs for a bundled preset. Raises ``ValueError`` if unknown."""
    preset = PRESETS.get(key)
    if preset is None:
        raise ValueError(
            f"unknown preset '{key}' — choose from: {', '.join(PRESETS)}"
        )
    return [AvoidSpec(radius_km=radius, label=label, lat=lat, lng=lng)
            for (lat, lng, radius, label) in preset.zones]


def preset_rows() -> list[dict]:
    return [{"key": p.key, "label": p.label, "zones": len(p.zones), "note": p.note}
            for p in PRESETS.values()]
