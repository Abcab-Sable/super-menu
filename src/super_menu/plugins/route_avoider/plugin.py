"""route-avoider plugin: driving routes that steer around user-defined areas.

Fills a real gap — consumer map apps (Google, Apple, Waze) expose only
*categorical* avoids (tolls, highways, ferries); none support "route me A→B but
stay N km away from *here*". This plugin does, by leaning on the one primitive a
few routing engines have and Google does not: **avoid-polygons**. A pin + radius
becomes a circle-polygon (see ``geo.py``), the circles are handed to a routing
engine behind an adapter (see ``adapter.py``), and the route that threads between
them comes back as GeoJSON — flowing unchanged to the CLI's ``--json``, the MCP
tool, and the TUI, exactly like every other command's result.

Live routing needs ``ORS_API_KEY`` (free tier at openrouteservice.org); without
it the plugin falls back to a clearly-labelled offline straight-line estimator so
it still installs, demos, and tests with zero setup. Run ``route-avoider config``
to see which engine is active.
"""
from __future__ import annotations

import os

from super_menu.core.plugin import Plugin, Command, Param, CommandResult
from . import geo
from .adapter import (
    DEFAULT_ORS_BASE, PROFILES, GeoPoint, NoRouteError, ORSAdapter,
    RoutingAdapter, RoutingError, StubAdapter,
)

# A single request handing the router dozens of circles is both slow and a sign
# of misuse; cap it (the brief's "cap polygon count" mitigation).
MAX_ZONES = 40


def active_adapter() -> RoutingAdapter:
    """The routing engine for this run: live ORS if a key is set, else the offline
    estimator. A module-level seam so tests can swap in a stub engine."""
    key = os.environ.get("ORS_API_KEY")
    if key:
        return ORSAdapter(api_key=key,
                          base_url=os.environ.get("ORS_BASE_URL", DEFAULT_ORS_BASE))
    return StubAdapter()


def _resolve_point(engine: RoutingAdapter, text: str) -> GeoPoint:
    """A ``"lat,lng"`` pair, or a place name resolved through the engine's geocoder."""
    pair = geo.parse_point(text)
    if pair is not None:
        return GeoPoint(lat=pair[0], lng=pair[1])
    return engine.geocode(text)  # may raise RoutingError


def cmd_route(origin: str, destination: str, avoid: str | None = None,
              preset: str | None = None, avoid_motorways: bool = False,
              profile: str = "driving-car") -> CommandResult:
    engine = active_adapter()

    # 1. endpoints
    try:
        o = _resolve_point(engine, origin)
        d = _resolve_point(engine, destination)
    except RoutingError as exc:
        return CommandResult.err(str(exc))

    # 2. avoid zones (user string + optional seeded preset layer)
    try:
        specs = geo.parse_avoid_spec(avoid) if avoid else []
        if preset:
            specs += geo.preset_specs(preset)
    except ValueError as exc:
        return CommandResult.err(str(exc))

    # 3. geocode any named zones ("Heathrow@10") through the same engine
    for s in specs:
        if not s.resolved:
            try:
                pt = engine.geocode(s.query)  # type: ignore[arg-type]
            except RoutingError as exc:
                return CommandResult.err(f"avoid zone '{s.query}': {exc}")
            s.lat, s.lng = pt.lat, pt.lng

    if len(specs) > MAX_ZONES:
        return CommandResult.err(
            f"{len(specs)} avoid zones exceeds the {MAX_ZONES}-zone cap — "
            "merge or drop some"
        )

    rings = geo.specs_to_rings(specs)

    # Motorway avoidance is a driving concept; pedestrians/cyclists never use them,
    # and ORS rejects the highways flag on those profiles. Honour intent, don't error.
    apply_motorways = avoid_motorways and profile == "driving-car"

    # 4. route
    try:
        result = engine.route(o, d, avoid_rings=rings,
                              avoid_motorways=apply_motorways, profile=profile)
    except NoRouteError as exc:
        return CommandResult.err(f"no route ({exc.reason}) — {exc.suggestion}")
    except RoutingError as exc:
        return CommandResult.err(str(exc))

    # The result IS a GeoJSON FeatureCollection (route line + avoid circles +
    # endpoints), so the TUI/CLI render it as a braille map and MCP/--json get a
    # payload that drops straight into geojson.io. Route metrics ride along as
    # GeoJSON foreign members (RFC 7946 §6.1), so callers still read them as fields.
    fc = geo.feature_collection(result.geometry, specs, (o.lat, o.lng), (d.lat, d.lng))
    fc.update({
        "bbox": result.bbox,
        "engine": engine.name,
        "live": engine.live,
        "profile": profile,
        "distance_km": result.distance_km,
        "duration_min": result.duration_min,
        "avoid_zones_applied": len(rings),
        "avoid_motorways": apply_motorways,
        "waypoints": result.waypoints,
    })

    zones = f", {len(rings)} zone(s) avoided" if rings else ""
    motor = ", motorway-free" if apply_motorways else ""
    estimate = "  [offline estimate — set ORS_API_KEY for real routing]" if not engine.live else ""
    summary = (f"{result.distance_km} km, {result.duration_min} min "
               f"via {profile}{zones}{motor}{estimate}")
    return CommandResult.ok_(data=fc, summary=summary, kind="geojson")


def cmd_presets() -> CommandResult:
    rows = geo.preset_rows()
    return CommandResult.ok_(
        data=rows,
        summary=f"{len(rows)} preset avoid layer(s)",
        kind="table",
        columns=["key", "label", "zones", "note"],
    )


def cmd_config() -> CommandResult:
    engine = active_adapter()
    data = {
        "engine": engine.name,
        "live": engine.live,
        "ors_api_key_set": bool(os.environ.get("ORS_API_KEY")),
        "ors_base_url": os.environ.get("ORS_BASE_URL", DEFAULT_ORS_BASE),
        "profiles": list(PROFILES),
        "presets": list(geo.PRESETS),
        "note": (
            "Live routing via OpenRouteService is active."
            if engine.live else
            "Offline estimator active. Set ORS_API_KEY (free at openrouteservice.org) "
            "for real road routing; optionally ORS_BASE_URL to point at a self-hosted "
            "engine."
        ),
    }
    return CommandResult.ok_(data=data, summary=f"routing engine: {engine.name}",
                             kind="json")


class RouteAvoiderPlugin(Plugin):
    id = "route-avoider"
    name = "Route Avoider"
    description = "Plan driving routes that steer around user-defined areas and motorways."
    icon = "🧭"

    def commands(self) -> list[Command]:
        return [
            Command(
                name="route",
                help="Route A→B avoiding circular zones, presets, and/or motorways.",
                handler=cmd_route,
                params=[
                    Param("origin", required=True,
                          help="'lat,lng' or a place name, e.g. 'Leeds'."),
                    Param("destination", required=True,
                          help="'lat,lng' or a place name, e.g. 'Aberystwyth'."),
                    Param("avoid",
                          help="Zones ';'-separated: 'lat,lng,radius_km[,label]' or "
                               "'name@radius_km'. e.g. '53.99,-1.69,12,Menwith Hill'."),
                    Param("preset", choices=list(geo.PRESETS),
                          help="Add a seeded avoid layer (see the 'presets' command)."),
                    Param("avoid_motorways", type="bool", default=False,
                          help="Keep the route off motorways (driving only)."),
                    Param("profile", default="driving-car", choices=list(PROFILES),
                          help="Travel mode."),
                ],
            ),
            Command(
                name="presets",
                help="List the bundled avoid layers you can pass to 'route --preset'.",
                handler=cmd_presets,
            ),
            Command(
                name="config",
                help="Show the active routing engine and how to enable live routing.",
                handler=cmd_config,
            ),
        ]


PLUGIN = RouteAvoiderPlugin()
