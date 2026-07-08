"""Disaster feeds behind one interface.

The same design decision that carries ``route_avoider`` carries this plugin: get
:class:`HazardFeed` right and adding a source (USGS today, GDACS or a national
flood service tomorrow) costs nothing above the seam — every feed's quirks
(EONET's nested categories, USGS's epoch-ms timestamps, magnitude→severity
scaling) are isolated inside one adapter that emits the same :class:`Hazard`.

Two live feeds ship here, both **keyless** (the free-for-dev zero-setup pattern):

* :class:`EONETFeed` — NASA EONET open natural events (wildfires, storms,
  volcanoes, floods, ice, drought…), already GeoJSON.
* :class:`USGSFeed`  — USGS significant earthquakes, already GeoJSON; magnitude
  maps to the red/orange/green severity the deck colours by.

When ``SUPER_MENU_OFFLINE`` is set, or every live feed fails, collection falls
back to the last good disk cache and then a packaged seed snapshot, so the plugin
installs, demos, and unit-tests with no network — exactly like ``free_for_dev``
serves off its seeded index. Everything a feed can't determine degrades to the
contract's documented defaults (category ``other``, severity ``orange``).
"""
from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from super_menu.core.config import plugin_data_dir

PLUGIN_ID = "hazard-watch"

# The category vocabulary the frontend contract enumerates. Anything a feed can't
# map lands on "other" (still drawn, just with the generic ⚠ marker).
CATEGORIES = ("wildfire", "storm", "volcano", "flood", "earthquake",
              "drought", "ice", "other")

# Danger radius for a *point* hazard, by category (km). The map's avoid-zone
# bridge turns these into route-avoider circles, so they are deliberately
# generous — a hazard you route *just* past is not avoided. Polygon hazards
# carry their own footprint and get no radius.
CATEGORY_RADIUS_KM = {"wildfire": 30, "storm": 100, "volcano": 50, "flood": 40,
                      "earthquake": 50, "drought": 150, "ice": 80, "other": 25}

# Severity is stored 1/2/3 internally and emitted as the GDACS-style word the
# deck colours by (both forms are accepted on the frontend).
SEV_WORD = {1: "green", 2: "orange", 3: "red"}

_CACHE_TTL_S = 15 * 60  # a scan is "fresh" for 15 min; older → refetch, keep as fallback


class HazardFeedError(Exception):
    """A feed could not be fetched or parsed. Never fatal — collection tolerates
    one feed failing and falls back to cache/seed if they all do."""


@dataclass
class Hazard:
    """One event, source-agnostic. :meth:`to_feature` renders the exact GeoJSON
    Feature shape the frontend contract documents."""

    title: str
    category: str
    severity: int              # 1 green · 2 orange · 3 red
    source: str
    geometry: dict             # GeoJSON Point or Polygon
    date: Optional[str] = None  # ISO-8601
    radius_km: Optional[float] = None
    extra: dict = field(default_factory=dict)  # source-specific props (magnitude, url…)

    def to_feature(self) -> dict:
        props = {
            "title": self.title,
            "category": self.category,
            "severity": SEV_WORD.get(self.severity, "orange"),
            "source": self.source,
        }
        if self.date:
            props["date"] = self.date
        if self.radius_km is not None:
            props["radius_km"] = self.radius_km
        props.update(self.extra)
        return {"type": "Feature", "geometry": self.geometry, "properties": props}

    def centroid(self) -> Optional[tuple[float, float]]:
        """(lat, lng) of the event — the point itself, or a polygon's mean vertex."""
        return _geometry_centroid(self.geometry)


class HazardFeed(ABC):
    name: str = "abstract"
    live: bool = True

    @abstractmethod
    def fetch(self, days: int, timeout: float) -> list[Hazard]:
        """Return current hazards from this source, or raise :class:`HazardFeedError`."""


# --------------------------------------------------------------------------- #
# NASA EONET (natural events; keyless)
# --------------------------------------------------------------------------- #

class EONETFeed(HazardFeed):
    name = "EONET"

    BASE = "https://eonet.gsfc.nasa.gov/api/v3/events"

    # EONET category id → our vocabulary. Unlisted ids (landslides, dustHaze,
    # manmade, waterColor, tempExtremes…) fall through to "other".
    _CATEGORY = {
        "wildfires": "wildfire", "severeStorms": "storm", "volcanoes": "volcano",
        "floods": "flood", "earthquakes": "earthquake", "drought": "drought",
        "seaLakeIce": "ice", "snow": "ice",
    }

    def fetch(self, days: int, timeout: float) -> list[Hazard]:
        url = f"{self.BASE}?status=open&days={max(1, days)}"
        payload = _get_json(url, timeout)
        out: list[Hazard] = []
        for ev in payload.get("events", []):
            geoms = ev.get("geometry") or []
            if not geoms:
                continue
            geom = geoms[-1]  # events carry a track; the latest fix is "now"
            g = self._geometry(geom)
            if g is None:
                continue
            cats = ev.get("categories") or []
            cat = self._CATEGORY.get((cats[0] or {}).get("id", ""), "other") if cats else "other"
            title = ev.get("title") or cat.title()
            magnitude = geom.get("magnitudeValue")
            out.append(Hazard(
                title=title,
                category=cat,
                severity=self._severity(cat, magnitude),
                source=self.name,
                geometry=g,
                date=_iso(geom.get("date")),
                radius_km=(CATEGORY_RADIUS_KM[cat] if g["type"] == "Point" else None),
                extra=({"event_url": (ev.get("sources") or [{}])[0].get("url", "")}
                       if ev.get("sources") else {}),
            ))
        return out

    @staticmethod
    def _geometry(geom: dict) -> Optional[dict]:
        gtype, coords = geom.get("type"), geom.get("coordinates")
        if gtype == "Point" and isinstance(coords, list) and len(coords) >= 2:
            return {"type": "Point", "coordinates": [coords[0], coords[1]]}
        if gtype == "Polygon" and coords:
            return {"type": "Polygon", "coordinates": coords}
        return None

    @staticmethod
    def _severity(category: str, magnitude) -> int:
        # EONET rarely carries a comparable severity; wildfires/volcanoes that do
        # report a magnitude get a nudge, otherwise the contract default (orange).
        if category == "volcano":
            return 3
        return 2


# --------------------------------------------------------------------------- #
# USGS earthquakes (keyless GeoJSON)
# --------------------------------------------------------------------------- #

class USGSFeed(HazardFeed):
    name = "USGS"

    BASE = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary"

    def fetch(self, days: int, timeout: float) -> list[Hazard]:
        # Keep the signal high: M4.5+ only, over the smallest window covering the
        # requested lookback (the feeds come in day/week/month buckets).
        window = "4.5_day" if days <= 1 else "4.5_week" if days <= 7 else "4.5_month"
        payload = _get_json(f"{self.BASE}/{window}.geojson", timeout)
        out: list[Hazard] = []
        for feat in payload.get("features", []):
            geom = feat.get("geometry") or {}
            coords = geom.get("coordinates") or []
            if geom.get("type") != "Point" or len(coords) < 2:
                continue
            p = feat.get("properties") or {}
            mag = p.get("mag")
            out.append(Hazard(
                title=p.get("place") or f"M{mag} earthquake",
                category="earthquake",
                severity=self._severity(mag),
                source=self.name,
                geometry={"type": "Point", "coordinates": [coords[0], coords[1]]},
                date=_iso_ms(p.get("time")),
                radius_km=self._radius(mag),
                extra={k: v for k, v in (("magnitude", mag), ("event_url", p.get("url"))) if v},
            ))
        return out

    @staticmethod
    def _severity(mag) -> int:
        m = mag or 0
        return 3 if m >= 6 else 2 if m >= 5 else 1

    @staticmethod
    def _radius(mag) -> float:
        # Felt/impact radius grows with magnitude; floor keeps a small quake from
        # producing a meaningless 5 km avoid circle.
        return float(round(max(25.0, (mag or 4.5) * 18.0)))


# --------------------------------------------------------------------------- #
# Offline seed (deterministic; default when SUPER_MENU_OFFLINE or all feeds fail)
# --------------------------------------------------------------------------- #

class SeedFeed(HazardFeed):
    """Serves the packaged snapshot in ``data/seed.json``. Not live — clearly
    labelled everywhere it surfaces — but it makes the plugin work and demo with
    zero setup and gives the tests a real feed with no network."""

    name = "seed"
    live = False

    def fetch(self, days: int, timeout: float) -> list[Hazard]:
        return list(load_seed())


def _seed_path() -> Path:
    return Path(__file__).parent / "data" / "seed.json"


def load_seed() -> list[Hazard]:
    try:
        fc = json.loads(_seed_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [_hazard_from_feature(f, "seed") for f in fc.get("features", [])]


# --------------------------------------------------------------------------- #
# Source selection + collection
# --------------------------------------------------------------------------- #

def active_feeds() -> list[HazardFeed]:
    """The feeds this run will poll. Offline (env flag) ⇒ just the seed; a
    module-level seam so tests can swap in a fake feed."""
    if os.environ.get("SUPER_MENU_OFFLINE"):
        return [SeedFeed()]
    return [EONETFeed(), USGSFeed()]


def collect(days: int = 30, timeout: float = 20.0) -> dict:
    """Poll every active feed, merge, and return a bundle:

    ``{hazards: [Hazard], sources: [str], live: bool, errors: {feed: msg},
       fetched_at: iso, from_cache: bool}``.

    One feed failing is tolerated (its error is recorded); if *every* live feed
    fails we fall back to the last good cache, then the packaged seed, so a
    caller always gets a usable answer offline.
    """
    feeds = active_feeds()
    hazards: list[Hazard] = []
    sources: list[str] = []
    errors: dict[str, str] = {}
    live_any = False
    for feed in feeds:
        try:
            got = feed.fetch(days, timeout)
        except HazardFeedError as exc:
            errors[feed.name] = str(exc)
            continue
        except Exception as exc:  # a feed must never take the whole scan down
            errors[feed.name] = f"{type(exc).__name__}: {exc}"
            continue
        hazards += got
        sources.append(feed.name)
        live_any = live_any or feed.live

    if hazards:
        bundle = {"hazards": hazards, "sources": sources, "live": live_any,
                  "errors": errors, "fetched_at": _now_iso(), "from_cache": False}
        if live_any:
            _write_cache(bundle)  # remember this good live scan for offline fallback
        return bundle

    # Every feed came back empty/failed → last good cache, then the seed.
    cached = _read_cache()
    if cached is not None:
        cached["errors"] = errors
        cached["from_cache"] = True
        return cached
    seed = load_seed()
    return {"hazards": seed, "sources": ["seed"] if seed else [], "live": False,
            "errors": errors, "fetched_at": _now_iso(), "from_cache": False}


# --- disk cache (mirrors free_for_dev's index cache) ----------------------- #

def _cache_path() -> Path:
    return plugin_data_dir(PLUGIN_ID) / "last_scan.json"


def _write_cache(bundle: dict) -> None:
    fc = {
        "type": "FeatureCollection",
        "fetched_at": bundle["fetched_at"],
        "sources": bundle["sources"],
        "features": [h.to_feature() for h in bundle["hazards"]],
    }
    try:
        _cache_path().write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass  # a read-only data dir must not fail a scan


def _read_cache(max_age_s: int = 24 * 3600) -> Optional[dict]:
    path = _cache_path()
    try:
        stat = path.stat()
    except OSError:
        return None
    if time.time() - stat.st_mtime > max_age_s:
        return None  # too stale to be worth showing as "live-ish"
    try:
        fc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    hazards = [_hazard_from_feature(f, "cache") for f in fc.get("features", [])]
    return {"hazards": hazards, "sources": fc.get("sources", []), "live": False,
            "errors": {}, "fetched_at": fc.get("fetched_at") or _now_iso(),
            "from_cache": True}


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #

def _hazard_from_feature(feat: dict, source_fallback: str) -> Hazard:
    """Rebuild a :class:`Hazard` from a stored/seed Feature (inverse of
    :meth:`Hazard.to_feature`)."""
    p = dict(feat.get("properties") or {})
    sev_raw = str(p.pop("severity", "orange")).lower()
    severity = 3 if sev_raw in ("red", "3") else 1 if sev_raw in ("green", "1") else 2
    known = {"title", "category", "source", "date", "radius_km"}
    return Hazard(
        title=p.pop("title", "event"),
        category=p.pop("category", "other"),
        severity=severity,
        source=p.pop("source", source_fallback),
        geometry=feat.get("geometry") or {},
        date=p.pop("date", None),
        radius_km=p.pop("radius_km", None),
        extra={k: v for k, v in p.items() if k not in known},
    )


def _get_json(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "super-menu/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise HazardFeedError(f"feed returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise HazardFeedError(f"feed unreachable: {exc.reason}") from exc
    except (ValueError, json.JSONDecodeError) as exc:
        raise HazardFeedError(f"feed sent malformed JSON: {exc}") from exc


def _geometry_centroid(geometry: dict) -> Optional[tuple[float, float]]:
    gtype, coords = geometry.get("type"), geometry.get("coordinates")
    if gtype == "Point" and isinstance(coords, list) and len(coords) >= 2:
        return float(coords[1]), float(coords[0])
    if gtype == "Polygon" and coords:
        ring = coords[0]
        pts = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
        if not pts:
            return None
        return (sum(p[1] for p in pts) / len(pts), sum(p[0] for p in pts) / len(pts))
    return None


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _iso(value) -> Optional[str]:
    """Pass through an ISO string (EONET already gives one)."""
    return value if isinstance(value, str) and value else None


def _iso_ms(ms) -> Optional[str]:
    """USGS epoch-milliseconds → ISO-8601 UTC."""
    if not isinstance(ms, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError):
        return None


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
