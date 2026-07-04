"""Routing engines behind one interface.

The single highest-leverage decision in the whole feature (per the build brief):
get :class:`RoutingAdapter` right and swapping engines — hosted OpenRouteService
for an MVP, self-hosted Valhalla for an offline production build — costs nothing
above this seam. Every field-name divergence between engines
(``avoid_polygons`` vs ``exclude_polygons``, header vs query-param auth) is
isolated inside an adapter.

Two adapters ship here:

* :class:`ORSAdapter` — live routing via OpenRouteService, using only stdlib
  ``urllib`` so the plugin has no third-party dependency.
* :class:`StubAdapter` — a deterministic, offline straight-line *estimator*. It
  makes the whole plugin work and demo with zero setup (the way free-for-dev
  works off its seeded index) and gives the tests a real engine to swap in,
  satisfying the "verified by a passing swap to a stub engine" acceptance check.
  It is clearly labelled as an estimate everywhere it surfaces.
"""
from __future__ import annotations

import json
import math
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass

from . import geo

DEFAULT_ORS_BASE = "https://api.openrouteservice.org"

# Rough moving speeds for the offline estimator, keyed by ORS profile.
_STUB_SPEED_KMH = {"driving-car": 72.0, "cycling-regular": 16.0, "foot-walking": 4.8}
PROFILES = ("driving-car", "cycling-regular", "foot-walking")

# Well-known places the offline estimator can "geocode" so the demo works with
# names, not just coordinates (the brief's own example is Leeds → Aberystwyth).
DEMO_PLACES: dict[str, tuple[float, float]] = {
    "london": (51.5074, -0.1278),
    "leeds": (53.8008, -1.5491),
    "aberystwyth": (52.4140, -4.0810),
    "manchester": (53.4808, -2.2426),
    "birmingham": (52.4862, -1.8904),
    "bristol": (51.4545, -2.5879),
    "edinburgh": (55.9533, -3.1883),
    "glasgow": (55.8642, -4.2518),
    "cardiff": (51.4816, -3.1791),
    "york": (53.9600, -1.0873),
    "menwith hill": (53.9906, -1.6900),
    "harrogate": (53.9919, -1.5378),
}


class RoutingError(Exception):
    """Any routing/geocoding failure that is not specifically 'no route exists'."""


class NoRouteError(RoutingError):
    """The constraints wall off the destination — an expected outcome, not a bug.

    Carries a machine ``reason`` and a human ``suggestion`` for relaxing a
    constraint, mirroring the brief's ``no_route`` failure response."""

    def __init__(self, reason: str, suggestion: str):
        super().__init__(f"{reason}: {suggestion}")
        self.reason = reason
        self.suggestion = suggestion


@dataclass
class GeoPoint:
    lat: float
    lng: float

    def lnglat(self) -> list[float]:
        return [self.lng, self.lat]


@dataclass
class RouteResult:
    distance_km: float
    duration_min: float
    geometry: dict          # GeoJSON LineString
    waypoints: int
    bbox: list[float] | None


class RoutingAdapter(ABC):
    """Contract every routing engine implements."""

    name: str = "abstract"
    live: bool = False  # True for a real router, False for the offline estimator

    @abstractmethod
    def geocode(self, query: str) -> GeoPoint:
        """Resolve a place name to a point, or raise :class:`RoutingError`."""

    @abstractmethod
    def route(self, origin: GeoPoint, destination: GeoPoint, *,
              avoid_rings: list[geo.Ring], avoid_motorways: bool,
              profile: str) -> RouteResult:
        """Route origin→destination avoiding the given circle rings."""


# --------------------------------------------------------------------------- #
# OpenRouteService (live)
# --------------------------------------------------------------------------- #

class ORSAdapter(RoutingAdapter):
    name = "openrouteservice"
    live = True

    def __init__(self, api_key: str, base_url: str = DEFAULT_ORS_BASE,
                 timeout: float = 30.0):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def geocode(self, query: str) -> GeoPoint:
        # ORS's Pelias geocoder takes the key as a query param (v2 directions take
        # it as an Authorization header — see route()); this split is exactly the
        # per-engine drift the adapter exists to hide.
        qs = urllib.parse.urlencode({"api_key": self.api_key, "text": query, "size": 1})
        url = f"{self.base_url}/geocode/search?{qs}"
        payload = self._get(url)
        features = payload.get("features") or []
        if not features:
            raise RoutingError(f"could not geocode '{query}'")
        lng, lat = features[0]["geometry"]["coordinates"][:2]
        return GeoPoint(lat=lat, lng=lng)

    def route(self, origin, destination, *, avoid_rings, avoid_motorways, profile):
        options: dict = {}
        if avoid_rings:
            options["avoid_polygons"] = geo.multipolygon(avoid_rings)
        if avoid_motorways:
            options["avoid_features"] = ["highways"]
        body = {"coordinates": [origin.lnglat(), destination.lnglat()]}
        if options:
            body["options"] = options
        url = f"{self.base_url}/v2/directions/{profile}/geojson"
        headers = {"Authorization": self.api_key, "Content-Type": "application/json"}
        payload = self._post(url, body, headers)

        features = payload.get("features") or []
        if not features:
            raise NoRouteError("no_route", "no route returned for these points")
        feat = features[0]
        summary = feat.get("properties", {}).get("summary", {})
        coords = feat.get("geometry", {}).get("coordinates", [])
        return RouteResult(
            distance_km=round(summary.get("distance", 0.0) / 1000.0, 2),
            duration_min=round(summary.get("duration", 0.0) / 60.0, 1),
            geometry=feat.get("geometry", {"type": "LineString", "coordinates": coords}),
            waypoints=len(coords),
            bbox=payload.get("bbox"),
        )

    # -- HTTP plumbing (stdlib only) ---------------------------------------- #
    def _get(self, url: str) -> dict:
        req = urllib.request.Request(url, headers={"User-Agent": "super-menu/0.1"})
        return self._send(req)

    def _post(self, url: str, body: dict, headers: dict) -> dict:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"User-Agent": "super-menu/0.1", **headers},
        )
        return self._send(req)

    def _send(self, req: urllib.request.Request) -> dict:
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise self._translate_http_error(exc) from exc
        except urllib.error.URLError as exc:
            raise RoutingError(f"routing service unreachable: {exc.reason}") from exc

    @staticmethod
    def _translate_http_error(exc: urllib.error.HTTPError) -> RoutingError:
        """Map an ORS error body to NoRouteError (walled-off destination) or a
        generic RoutingError. ORS error code 2009 = no route between points,
        2010 = a point could not be snapped to the road network."""
        try:
            info = json.loads(exc.read().decode("utf-8")).get("error", {})
        except Exception:
            info = {}
        code = info.get("code")
        message = info.get("message") if isinstance(info, dict) else str(info)
        if code == 2010:
            return NoRouteError(
                "unroutable_point",
                "an endpoint is not near a road — nudge the origin/destination",
            )
        if code == 2009:
            return NoRouteError(
                "no_route_between_points",
                "avoid zones may enclose an endpoint — reduce a radius or remove a zone",
            )
        return RoutingError(message or f"routing failed (HTTP {exc.code})")


# --------------------------------------------------------------------------- #
# Offline estimator (deterministic; default when no API key)
# --------------------------------------------------------------------------- #

class StubAdapter(RoutingAdapter):
    """Straight-line estimator with a detour penalty per avoid zone crossed.

    Not a real router — it cannot see roads — but it is deterministic, offline,
    and honest: it detects the walled-off-destination case, grows the estimate for
    each zone the direct line passes through, and every result it feeds back is
    labelled an estimate."""

    name = "offline-estimate"
    live = False

    def __init__(self, places: dict[str, tuple[float, float]] | None = None):
        self.places = {k.lower(): v for k, v in (places or DEMO_PLACES).items()}

    def geocode(self, query: str) -> GeoPoint:
        hit = self.places.get(query.strip().lower())
        if hit is None:
            raise RoutingError(
                f"offline engine does not know '{query}' — use 'lat,lng' or set "
                "ORS_API_KEY for live geocoding"
            )
        return GeoPoint(lat=hit[0], lng=hit[1])

    def route(self, origin, destination, *, avoid_rings, avoid_motorways, profile):
        # Endpoint inside an avoid zone ⇒ genuinely no route (the brief's core
        # failure case), regardless of engine.
        if geo.point_in_any(origin.lng, origin.lat, avoid_rings):
            raise NoRouteError("origin_enclosed",
                               "the origin sits inside an avoid zone — shrink or move it")
        if geo.point_in_any(destination.lng, destination.lat, avoid_rings):
            raise NoRouteError("destination_enclosed",
                               "the destination sits inside an avoid zone — reduce a radius "
                               "or remove a zone")

        # Weave a polyline that steers around each avoid circle instead of drawing
        # a straight line through it, so the estimate — and the map — reflect the
        # detour. Road distance ≈ path length × a winding factor.
        polyline = self._avoiding_path(origin, destination, avoid_rings)
        distance = round(_path_km(polyline) * 1.30, 2)
        if avoid_motorways:
            distance = round(distance * 1.12, 2)  # motorway-free routes wander more
        speed = _STUB_SPEED_KMH.get(profile, _STUB_SPEED_KMH["driving-car"])
        return RouteResult(
            distance_km=distance,
            duration_min=round(distance / speed * 60.0, 1),
            geometry={"type": "LineString", "coordinates": polyline},
            waypoints=len(polyline),
            bbox=geo.bbox_of(polyline),
        )

    def _avoiding_path(self, origin: GeoPoint, destination: GeoPoint,
                       avoid_rings: list[geo.Ring]) -> list[list[float]]:
        """Insert detour waypoints until no segment cuts through an avoid circle."""
        circles = [_ring_circle(r) for r in avoid_rings]
        path: list[list[float]] = [[origin.lng, origin.lat],
                                   [destination.lng, destination.lat]]
        for _ in range(24):  # bounded: each pass detours the worst crossing per segment
            changed = False
            out = [path[0]]
            for a, b in zip(path, path[1:]):
                wp = _detour_waypoint(a, b, circles)
                if wp is not None:
                    out.append(wp)
                    changed = True
                out.append(b)
            path = out
            if not changed or len(path) > 64:
                break
        return [[round(x, 6), round(y, 6)] for x, y in path]


def _ring_circle(ring: geo.Ring) -> tuple[float, float, float]:
    """Approximate a ring as (centre_lng, centre_lat, radius_deg)."""
    pts = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    rad = max(math.hypot(p[0] - cx, p[1] - cy) for p in pts)
    return cx, cy, rad


def _detour_waypoint(a: list[float], b: list[float],
                     circles: list[tuple[float, float, float]]) -> list[float] | None:
    """If segment a→b penetrates a circle, a waypoint just outside its worst
    offender that bends the path around it; else ``None``."""
    abx, aby = b[0] - a[0], b[1] - a[1]
    len2 = abx * abx + aby * aby or 1e-12
    worst = None
    for cx, cy, rad in circles:
        t = max(0.0, min(1.0, ((cx - a[0]) * abx + (cy - a[1]) * aby) / len2))
        px, py = a[0] + abx * t, a[1] + aby * t
        depth = math.hypot(px - cx, py - cy)
        if depth < rad * 0.98 and (worst is None or depth < worst[0]):
            worst = (depth, cx, cy, rad, px, py)
    if worst is None:
        return None
    _, cx, cy, rad, px, py = worst
    dx, dy = px - cx, py - cy
    dl = math.hypot(dx, dy)
    if dl < 1e-9:  # segment runs through the centre: offset perpendicular to it
        dx, dy = -aby, abx
        dl = math.hypot(dx, dy) or 1e-9
    return [cx + dx / dl * rad * 1.6, cy + dy / dl * rad * 1.6]


def _path_km(path: list[list[float]]) -> float:
    return sum(geo.haversine_km(a[1], a[0], b[1], b[0])
               for a, b in zip(path, path[1:]))
