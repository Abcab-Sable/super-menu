"""Tests for the route-avoider plugin: geometry, the engine-swap seam, and the
area-avoidance acceptance criteria — all offline via the stub engine."""
from super_menu.core.registry import default_registry
from super_menu.plugins.route_avoider import geo, plugin
from super_menu.plugins.route_avoider.adapter import (
    GeoPoint, NoRouteError, RoutingError, StubAdapter,
)


# ----- geometry ------------------------------------------------------------ #

def test_circle_ring_is_closed_and_radius_holds():
    lat, lng, r = 53.8, -1.55, 10.0
    ring = geo.circle_ring(lat, lng, r, steps=64)
    assert ring[0] == ring[-1], "ring must be closed"
    assert len(ring) == 65
    # every vertex sits ~r km from the centre (geodesic circle, not a lat/lng box)
    for plng, plat in ring:
        d = geo.haversine_km(lat, lng, plat, plng)
        assert abs(d - r) < 0.2, (d, r)


def test_point_in_ring():
    ring = geo.circle_ring(53.8, -1.55, 10.0)
    assert geo.point_in_ring(-1.55, 53.8, ring) is True           # centre
    assert geo.point_in_ring(-1.55, 54.5, ring) is False          # ~78 km north


def test_parse_point_and_avoid_spec():
    assert geo.parse_point("53.727,-1.858") == (53.727, -1.858)
    assert geo.parse_point("Leeds") is None                       # a name, not a pair
    specs = geo.parse_avoid_spec("53.99,-1.69,12,Menwith Hill; Heathrow@10")
    assert len(specs) == 2
    assert specs[0].resolved and specs[0].label == "Menwith Hill"
    assert not specs[1].resolved and specs[1].query == "Heathrow"


def test_parse_avoid_spec_rejects_bad_input():
    for bad in ["53.99,-1.69", "53.99,-1.69,0", "999,0,5", "53.99,-1.69,nan_km@"]:
        try:
            geo.parse_avoid_spec(bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad!r}")


def test_preset_specs_resolved():
    specs = geo.preset_specs("uk_cities")
    assert specs and all(s.resolved for s in specs)
    try:
        geo.preset_specs("nope")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown preset should raise")


# ----- stub engine --------------------------------------------------------- #

def test_stub_geocode():
    eng = StubAdapter()
    assert isinstance(eng.geocode("Leeds"), GeoPoint)
    try:
        eng.geocode("Atlantis")
    except RoutingError:
        pass
    else:
        raise AssertionError("unknown place should raise")


def test_stub_route_basic():
    eng = StubAdapter()
    o = eng.geocode("Leeds")
    d = eng.geocode("Aberystwyth")
    res = eng.route(o, d, avoid_rings=[], avoid_motorways=False, profile="driving-car")
    assert res.distance_km > 0 and res.duration_min > 0
    # no zones ⇒ a direct 2-point line; the renderer interpolates it into a stroke
    assert res.geometry["type"] == "LineString" and res.waypoints >= 2


def test_avoid_zone_lengthens_route():
    """Acceptance: a zone across the direct line makes the route longer."""
    eng = StubAdapter()
    o = eng.geocode("Leeds")
    d = eng.geocode("Aberystwyth")
    plain = eng.route(o, d, avoid_rings=[], avoid_motorways=False, profile="driving-car")
    # a zone straddling the mid-point of the Leeds→Aberystwyth line
    mid_lat = (o.lat + d.lat) / 2
    mid_lng = (o.lng + d.lng) / 2
    ring = geo.circle_ring(mid_lat, mid_lng, 25.0)
    avoided = eng.route(o, d, avoid_rings=[ring], avoid_motorways=False,
                        profile="driving-car")
    assert avoided.distance_km > plain.distance_km
    assert avoided.waypoints > 2, "a detour waypoint should be inserted (route bends)"


def test_enclosed_destination_raises_no_route():
    eng = StubAdapter()
    o = eng.geocode("Leeds")
    d = eng.geocode("Aberystwyth")
    ring = geo.circle_ring(d.lat, d.lng, 15.0)  # zone sitting on the destination
    try:
        eng.route(o, d, avoid_rings=[ring], avoid_motorways=False, profile="driving-car")
    except NoRouteError as exc:
        assert exc.reason == "destination_enclosed"
    else:
        raise AssertionError("expected NoRouteError")


# ----- plugin commands (through the contract) ------------------------------ #

def test_plugin_discovered():
    assert default_registry().get("route-avoider") is not None


def test_route_command_ok():
    res = plugin.cmd_route(origin="Leeds", destination="Aberystwyth")
    # result is a GeoJSON FeatureCollection with metrics as foreign members
    assert res.ok and res.kind == "geojson"
    assert res.data["type"] == "FeatureCollection"
    assert res.data["distance_km"] > 0
    assert res.data["engine"] == "offline-estimate"


def test_route_command_with_coords_and_zone():
    res = plugin.cmd_route(
        origin="53.8008,-1.5491", destination="52.4140,-4.0810",
        avoid="53.1,-2.8,20,MidZone",
    )
    assert res.ok
    assert res.data["avoid_zones_applied"] == 1
    # the FeatureCollection carries route + avoid + endpoints
    kinds = {f["properties"]["kind"] for f in res.data["features"]}
    assert {"route", "avoid", "origin", "destination"} <= kinds


def test_route_command_no_route_is_clean_error():
    # destination boxed in by its own avoid circle
    res = plugin.cmd_route(origin="Leeds", destination="Aberystwyth",
                           avoid="52.4140,-4.0810,15,Box")
    assert not res.ok
    assert "no route" in res.summary and "destination_enclosed" in res.summary


def test_route_command_bad_avoid_string():
    res = plugin.cmd_route(origin="Leeds", destination="York", avoid="garbage")
    assert not res.ok and "avoid zone" in res.summary


def test_route_command_unknown_place_offline():
    res = plugin.cmd_route(origin="Atlantis", destination="Leeds")
    assert not res.ok and "Atlantis" in res.summary


def test_engine_swap_via_seam(monkeypatch):
    """Acceptance: the engine is reached only through the adapter — swapping in a
    different stub changes the result with no command-code change."""
    class FixedEngine(StubAdapter):
        name = "fixed-test-engine"
        def route(self, *a, **k):
            r = super().route(*a, **k)
            r.distance_km = 42.0
            return r
    monkeypatch.setattr(plugin, "active_adapter", lambda: FixedEngine())
    res = plugin.cmd_route(origin="Leeds", destination="York")
    assert res.ok and res.data["distance_km"] == 42.0
    assert res.data["engine"] == "fixed-test-engine"


def test_constrained_route_carries_baseline():
    """Trust pack: a zone-constrained route ships the unconstrained baseline as a
    ghost feature plus detour metrics, so every surface can show the trade-off."""
    res = plugin.cmd_route(origin="53.8008,-1.5491", destination="52.4140,-4.0810",
                           avoid="53.1,-2.8,20,MidZone")
    assert res.ok
    kinds = [f["properties"]["kind"] for f in res.data["features"]]
    assert kinds[0] == "baseline", "ghost line draws first, under everything"
    assert res.data["baseline_km"] > 0
    assert res.data["detour_km"] == round(
        res.data["distance_km"] - res.data["baseline_km"], 2)
    assert "vs direct" in res.summary


def test_unconstrained_route_has_no_baseline():
    res = plugin.cmd_route(origin="53.8008,-1.5491", destination="52.4140,-4.0810")
    assert res.ok
    kinds = {f["properties"]["kind"] for f in res.data["features"]}
    assert "baseline" not in kinds and "baseline_km" not in res.data
    assert "vs direct" not in res.summary


def test_presets_and_config_commands():
    presets = plugin.cmd_presets()
    assert presets.ok and presets.kind == "table" and presets.data
    config = plugin.cmd_config()
    assert config.ok and "engine" in config.data


if __name__ == "__main__":
    import sys
    funcs = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in funcs:
        if "monkeypatch" in fn.__code__.co_varnames[: fn.__code__.co_argcount]:
            continue  # needs pytest fixtures; run via pytest for these
        fn()
        passed += 1
    print(f"{passed} route-avoider tests passed (run via pytest for fixture-based ones)")
    sys.exit(0)
