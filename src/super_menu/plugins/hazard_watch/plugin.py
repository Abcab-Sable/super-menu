"""hazard-watch plugin: a live global disaster feed on the command deck.

Open-source intelligence for prepping — polls keyless public feeds (NASA EONET
natural events + USGS earthquakes) and emits them as GeoJSON, so the same payload
lights up as a braille map in the TUI/CLI, a threat board on the web deck (the
``active`` command drives the dedicated hazard view), and a raw FeatureCollection
for MCP / ``--json`` consumers — with **no surface-specific code**, exactly like
``route_avoider``'s routes.

The point of a hazard *on this deck* is composition: ``near`` answers "what's
active around me", and every hazard the web view shows drops straight into
route-avoider as an avoid zone ("route me to Leeds around the flooding"). Sources
sit behind an adapter (see ``feeds.py``) and need no API key; offline, the plugin
falls back to a cached scan and then a packaged seed, so it installs, demos, and
tests with zero setup. Run ``hazard-watch sources`` to see what's live.
"""
from __future__ import annotations

from super_menu.core.plugin import Plugin, Command, Param, CommandResult
from . import feeds
from .feeds import CATEGORIES, SEV_WORD, haversine_km

# Cap the emitted FeatureCollection so a busy global day (EONET open events +
# a month of M4.5 quakes can top a few hundred) stays a snappy map payload.
# When trimming is needed we keep the most severe, then the most recent.
DEFAULT_LIMIT = 250

_SEV_RANK = {"red": 3, "orange": 2, "green": 1}

# Region focus presets (lat_min, lat_max, lng_min, lng_max). The global feed is
# severity-ranked and capped, which can bury a country's low-severity regional
# warnings (e.g. Poland's drought advisories) behind more-severe global events;
# focusing on a region filters *before* the cap so that country's detail shows.
REGIONS: dict[str, tuple[float, float, float, float]] = {
    "uk": (49.8, 61.0, -8.7, 2.1),
    "poland": (49.0, 54.9, 14.1, 24.2),
    "europe": (34.0, 72.0, -25.0, 45.0),
}

# A small offline gazetteer so ``near`` works with names, not just coordinates —
# the same honest-stub approach route_avoider's estimator uses for geocoding.
# Unknown names ask for 'lat,lng' rather than guessing.
PLACES: dict[str, tuple[float, float]] = {
    "london": (51.5074, -0.1278), "manchester": (53.4808, -2.2426),
    "birmingham": (52.4862, -1.8904), "leeds": (53.8008, -1.5491),
    "edinburgh": (55.9533, -3.1883), "glasgow": (55.8642, -4.2518),
    "cardiff": (51.4816, -3.1791), "dublin": (53.3498, -6.2603),
    "paris": (48.8566, 2.3522), "berlin": (52.5200, 13.4050),
    "madrid": (40.4168, -3.7038), "rome": (41.9028, 12.4964),
    "new york": (40.7128, -74.0060), "los angeles": (34.0522, -118.2437),
    "san francisco": (37.7749, -122.4194), "seattle": (47.6062, -122.3321),
    "tokyo": (35.6762, 139.6503), "sydney": (-33.8688, 151.2093),
    "singapore": (1.3521, 103.8198), "mumbai": (19.0760, 72.8777),
}


def _parse_point(text: str) -> tuple[float, float] | None:
    parts = text.replace(" ", "").split(",")
    if len(parts) != 2:
        return None
    try:
        lat, lng = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    if -90 <= lat <= 90 and -180 <= lng <= 180:
        return lat, lng
    return None


def _in_region(h: feeds.Hazard, bbox: tuple[float, float, float, float]) -> bool:
    c = h.centroid()
    if c is None:
        return False
    lat, lng = c
    lat_min, lat_max, lng_min, lng_max = bbox
    return lat_min <= lat <= lat_max and lng_min <= lng <= lng_max


def _filter(hazards: list[feeds.Hazard], category: str | None,
            min_severity: str | None, region: str | None = None) -> list[feeds.Hazard]:
    floor = _SEV_RANK.get((min_severity or "").lower(), 1)
    cat = (category or "").lower().strip()
    bbox = REGIONS.get((region or "").lower().strip())
    out = []
    for h in hazards:
        if h.severity < floor:
            continue
        if cat and h.category != cat:
            continue
        if bbox and not _in_region(h, bbox):
            continue
        out.append(h)
    return out


def _trim(hazards: list[feeds.Hazard], limit: int) -> list[feeds.Hazard]:
    """Most severe first, then most recent — so a cap keeps the events that matter."""
    ordered = sorted(hazards, key=lambda h: (h.severity, h.date or ""), reverse=True)
    return ordered[: max(1, limit)] if limit and limit > 0 else ordered


def _collection(hazards: list[feeds.Hazard], bundle: dict, **extra) -> dict:
    """Build the GeoJSON FeatureCollection the frontend contract documents.
    Top-level scalars (sources, window_days, live…) ride along as foreign members
    and render as metric chips on the deck."""
    fc = {
        "type": "FeatureCollection",
        "fetched_at": bundle.get("fetched_at"),
        "sources": ", ".join(bundle.get("sources") or []) or "none",
        "live": bundle.get("live", False),
        "features": [h.to_feature() for h in hazards],
    }
    fc.update(extra)
    return fc


def _note(bundle: dict) -> str:
    """Trailing summary annotation for degraded/offline scans."""
    if bundle.get("from_cache"):
        return "  [offline — showing last cached scan]"
    if not bundle.get("live"):
        return "  [offline seed — no live feed reached; live feeds are keyless]"
    if bundle.get("errors"):
        return f"  [{len(bundle['errors'])} feed(s) unavailable]"
    return ""


def cmd_active(category: str | None = None, min_severity: str | None = None,
               region: str | None = None, days: int = 30,
               limit: int = DEFAULT_LIMIT) -> CommandResult:
    if category and category.lower() not in CATEGORIES:
        return CommandResult.err(
            f"unknown category '{category}' — choose one of {', '.join(CATEGORIES)}")
    if region and region.lower() not in REGIONS:
        return CommandResult.err(
            f"unknown region '{region}' — choose one of {', '.join(REGIONS)}")
    bundle = feeds.collect(days=days)
    matched = _filter(bundle["hazards"], category, min_severity, region)
    hazards = _trim(matched, limit)
    fc = _collection(hazards, bundle, window_days=days)
    if region:
        fc["region"] = region.lower()
    reds = sum(1 for h in hazards if h.severity == 3)
    scope = f" {category}" if category else ""
    where = f" in {region.lower()}" if region else ""
    shown = f" (showing {len(hazards)} of {len(matched)})" if len(matched) > len(hazards) else ""
    summary = (f"{len(matched)}{scope} active event(s){where}, {reds} red"
               f" via {fc['sources']}{shown}{_note(bundle)}")
    return CommandResult.ok_(data=fc, summary=summary, kind="geojson")


def cmd_near(location: str, radius_km: float = 500.0,
             category: str | None = None, min_severity: str | None = None,
             days: int = 30) -> CommandResult:
    if category and category.lower() not in CATEGORIES:
        return CommandResult.err(
            f"unknown category '{category}' — choose one of {', '.join(CATEGORIES)}")
    point = _parse_point(location) or PLACES.get(location.strip().lower())
    if point is None:
        return CommandResult.err(
            f"don't know '{location}' — pass 'lat,lng' or a major city "
            f"(e.g. {', '.join(list(PLACES)[:4])}…)")
    lat, lng = point

    bundle = feeds.collect(days=days)
    within: list[feeds.Hazard] = []
    for h in _filter(bundle["hazards"], category, min_severity):
        c = h.centroid()
        if c is None:
            continue
        dist = round(haversine_km(lat, lng, c[0], c[1]), 1)
        if dist <= radius_km:
            h.extra = {**h.extra, "distance_km": dist}
            within.append(h)
    within.sort(key=lambda h: h.extra.get("distance_km", 1e9))

    # Mark the query point so the map centres on "you" among the hazards.
    origin = {"type": "Feature",
              "geometry": {"type": "Point", "coordinates": [lng, lat]},
              "properties": {"title": location, "category": "origin", "kind": "origin"}}
    fc = _collection(within, bundle, window_days=days, center=[lat, lng],
                     within_km=radius_km)
    fc["features"].insert(0, origin)
    nearest = f"; nearest {within[0].extra['distance_km']} km" if within else ""
    summary = (f"{len(within)} event(s) within {radius_km:g} km of {location}"
               f"{nearest} via {fc['sources']}{_note(bundle)}")
    return CommandResult.ok_(data=fc, summary=summary, kind="geojson")


def cmd_sources() -> CommandResult:
    active = feeds.active_feeds()
    bundle = feeds.collect()
    counts: dict[str, int] = {}
    for h in bundle["hazards"]:
        counts[h.source] = counts.get(h.source, 0) + 1
    data = {
        "feeds": [{"name": f.name, "live": f.live} for f in active],
        "reached": bundle.get("sources", []),
        "counts": counts,
        "live": bundle.get("live", False),
        "from_cache": bundle.get("from_cache", False),
        "errors": bundle.get("errors", {}),
        "fetched_at": bundle.get("fetched_at"),
        "categories": list(CATEGORIES),
        "severities": list(SEV_WORD.values()),
        "note": ("Live keyless feeds: NASA EONET + USGS. Set SUPER_MENU_OFFLINE=1 "
                 "to force the packaged seed."),
    }
    reached = ", ".join(bundle.get("sources", [])) or "none"
    return CommandResult.ok_(data=data, summary=f"feeds reached: {reached}", kind="json")


class HazardWatchPlugin(Plugin):
    id = "hazard-watch"
    name = "Hazard Watch"
    description = "Live global disaster feed (NASA EONET + USGS) as a threat map."
    icon = "🛰️"

    def commands(self) -> list[Command]:
        sev_choices = list(SEV_WORD.values())  # green / orange / red
        return [
            Command(
                name="active",
                help="Active global hazards as a GeoJSON threat map.",
                handler=cmd_active,
                params=[
                    Param("category", choices=list(CATEGORIES),
                          help="Restrict to one hazard category."),
                    Param("min_severity", choices=sev_choices,
                          help="Only events at this severity or worse."),
                    Param("region", choices=list(REGIONS),
                          help="Focus on a region (uk/poland/europe) before the cap."),
                    Param("days", type="int", default=30,
                          help="Look back this many days for open events."),
                    Param("limit", type="int", default=DEFAULT_LIMIT,
                          help="Max events (most severe/recent kept)."),
                ],
            ),
            Command(
                name="near",
                help="Active hazards within a radius of a place or 'lat,lng'.",
                handler=cmd_near,
                params=[
                    Param("location", required=True,
                          help="'lat,lng' or a major city name, e.g. 'Manchester'."),
                    Param("radius_km", type="float", default=500.0,
                          help="Search radius in kilometres."),
                    Param("category", choices=list(CATEGORIES),
                          help="Restrict to one hazard category."),
                    Param("min_severity", choices=sev_choices,
                          help="Only events at this severity or worse."),
                    Param("days", type="int", default=30, help="Look-back window in days."),
                ],
            ),
            Command(
                name="sources",
                help="Show which hazard feeds are live and what they returned.",
                handler=cmd_sources,
            ),
        ]


PLUGIN = HazardWatchPlugin()
