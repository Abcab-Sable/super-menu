"""Tests for the hazard-watch plugin: feed parsing, the source-swap seam, the
frontend GeoJSON contract, and the command surface — all offline.

Live feeds are exercised by feeding canned payloads through the same
``_get_json`` the adapters call (no network), mirroring how ``test_roads`` injects
a fake fetcher and ``test_route_avoider`` swaps the routing engine behind its
seam. Standalone runners don't load conftest, so we force offline at import time.
"""
import os

os.environ.setdefault("SUPER_MENU_OFFLINE", "1")

from contextlib import contextmanager

from super_menu.core.registry import default_registry
from super_menu.plugins.hazard_watch import feeds, plugin
from super_menu.plugins.hazard_watch.feeds import (
    EONETFeed, Hazard, SeedFeed, USGSFeed,
)


@contextmanager
def _patched(obj, name, value):
    """Swap an attribute for the duration of a block (no pytest fixture needed,
    so every test here also runs under the standalone runner)."""
    old = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old)


# ----- Hazard → GeoJSON contract ------------------------------------------- #

def test_to_feature_matches_frontend_contract():
    h = Hazard(title="Test Fire", category="wildfire", severity=3, source="EONET",
               geometry={"type": "Point", "coordinates": [-120.0, 38.0]},
               date="2026-07-01T00:00:00Z", radius_km=30, extra={"event_url": "http://x"})
    f = h.to_feature()
    assert f["type"] == "Feature"
    assert f["geometry"]["type"] == "Point"
    p = f["properties"]
    # severity is emitted as the GDACS-style word the deck colours by
    assert p["severity"] == "red"
    assert p["category"] == "wildfire" and p["title"] == "Test Fire"
    assert p["radius_km"] == 30 and p["source"] == "EONET"
    assert p["event_url"] == "http://x"           # extra props ride along


def test_feature_roundtrip_preserves_core_fields():
    h = Hazard(title="Quake", category="earthquake", severity=2, source="USGS",
               geometry={"type": "Point", "coordinates": [139.0, 35.0]},
               radius_km=97, extra={"magnitude": 5.4})
    back = feeds._hazard_from_feature(h.to_feature(), "x")
    assert (back.category, back.severity, back.radius_km) == ("earthquake", 2, 97)
    assert back.extra.get("magnitude") == 5.4


def test_polygon_centroid():
    poly = {"type": "Polygon",
            "coordinates": [[[-2.4, 53.5], [-2.1, 53.5], [-2.1, 53.7], [-2.4, 53.7], [-2.4, 53.5]]]}
    lat, lng = feeds._geometry_centroid(poly)
    assert abs(lat - 53.6) < 0.01 and abs(lng - -2.25) < 0.01


# ----- live-feed parsing (canned payloads, no network) --------------------- #

def test_eonet_parse_and_category_map():
    payload = {"events": [
        {"id": "E1", "title": "Test Fire",
         "categories": [{"id": "wildfires", "title": "Wildfires"}],
         "sources": [{"id": "s", "url": "http://fire"}],
         "geometry": [{"date": "2026-07-01T00:00:00Z", "type": "Point",
                       "coordinates": [-120.0, 38.0]}]},
        {"id": "E2", "title": "Test Volcano",
         "categories": [{"id": "volcanoes", "title": "Volcanoes"}],
         "geometry": [{"date": "2026-07-02T00:00:00Z", "type": "Point",
                       "coordinates": [15.0, 40.0]}]},
    ]}
    with _patched(feeds, "_get_json", lambda url, timeout: payload):
        out = EONETFeed().fetch(days=7, timeout=1)
    assert {h.category for h in out} == {"wildfire", "volcano"}
    volcano = next(h for h in out if h.category == "volcano")
    assert volcano.severity == 3                    # volcanoes default red
    fire = next(h for h in out if h.category == "wildfire")
    assert fire.severity == 2 and fire.radius_km == 30
    assert fire.extra.get("event_url") == "http://fire"


def test_eonet_unknown_category_is_other():
    payload = {"events": [{"id": "E", "title": "Odd",
        "categories": [{"id": "manmade", "title": "Manmade"}],
        "geometry": [{"date": "2026-07-01T00:00:00Z", "type": "Point",
                      "coordinates": [0.0, 0.0]}]}]}
    with _patched(feeds, "_get_json", lambda url, timeout: payload):
        out = EONETFeed().fetch(days=7, timeout=1)
    assert out and out[0].category == "other"


def test_usgs_magnitude_to_severity_and_radius():
    payload = {"features": [
        {"geometry": {"type": "Point", "coordinates": [139.0, 35.0, 10]},
         "properties": {"mag": 6.2, "place": "Off Japan", "time": 1751000000000,
                        "url": "http://q"}},
        {"geometry": {"type": "Point", "coordinates": [-120.0, 36.0, 5]},
         "properties": {"mag": 4.6, "place": "California", "time": 1751000000000}},
    ]}
    with _patched(feeds, "_get_json", lambda url, timeout: payload):
        out = USGSFeed().fetch(days=7, timeout=1)
    big = next(h for h in out if h.extra["magnitude"] == 6.2)
    small = next(h for h in out if h.extra["magnitude"] == 4.6)
    assert big.severity == 3 and small.severity == 1
    assert big.radius_km >= 100 and big.date and big.date.startswith("20")
    assert all(h.category == "earthquake" for h in out)


def test_usgs_window_follows_days():
    seen = {}
    def fake(url, timeout):
        seen["url"] = url
        return {"features": []}
    with _patched(feeds, "_get_json", fake):
        USGSFeed().fetch(days=1, timeout=1)
        assert "4.5_day" in seen["url"]
        USGSFeed().fetch(days=30, timeout=1)
        assert "4.5_month" in seen["url"]


# ----- collection seam + offline fallback ---------------------------------- #

def test_collect_offline_uses_seed():
    bundle = feeds.collect(days=7)
    assert bundle["hazards"] and bundle["live"] is False
    assert bundle["sources"] == ["seed"]


def test_collect_tolerates_one_feed_failing():
    class BoomFeed(feeds.HazardFeed):
        name = "boom"
        def fetch(self, days, timeout):
            raise feeds.HazardFeedError("down")

    good = SeedFeed()
    with _patched(feeds, "active_feeds", lambda: [BoomFeed(), good]):
        bundle = feeds.collect(days=7)
    assert bundle["hazards"]                          # seed feed still delivered
    assert "boom" in bundle["errors"]


def test_collect_all_fail_falls_back_to_seed():
    class BoomFeed(feeds.HazardFeed):
        name = "boom"
        def fetch(self, days, timeout):
            raise feeds.HazardFeedError("down")

    # Isolate the no-cache path (a prior live run may have written one) so this
    # deterministically exercises the final seed fallback.
    with _patched(feeds, "active_feeds", lambda: [BoomFeed()]), \
            _patched(feeds, "_read_cache", lambda *a, **k: None):
        bundle = feeds.collect(days=7)
    assert bundle["hazards"] and bundle["sources"] == ["seed"]


# ----- plugin commands (through the contract) ------------------------------ #

def test_plugin_discovered():
    p = default_registry().get("hazard-watch")
    assert p is not None and [c.name for c in p.commands()] == ["active", "near", "sources"]


def test_active_returns_geojson_contract():
    res = plugin.cmd_active()
    assert res.ok and res.kind == "geojson"
    fc = res.data
    assert fc["type"] == "FeatureCollection" and fc["features"]
    assert fc["live"] is False and fc["window_days"] == 30
    for f in fc["features"]:
        p = f["properties"]
        assert p["category"] in feeds.CATEGORIES
        assert p["severity"] in ("green", "orange", "red")
        assert f["geometry"]["type"] in ("Point", "Polygon")


def test_active_category_and_severity_filters():
    quakes = plugin.cmd_active(category="earthquake")
    assert quakes.ok
    assert all(f["properties"]["category"] == "earthquake" for f in quakes.data["features"])

    red = plugin.cmd_active(min_severity="red")
    assert red.ok
    assert all(f["properties"]["severity"] == "red" for f in red.data["features"])

    assert not plugin.cmd_active(category="nonsense").ok


def test_active_limit_keeps_most_severe():
    res = plugin.cmd_active(limit=3)
    feats = res.data["features"]
    assert len(feats) == 3
    sev_rank = {"red": 3, "orange": 2, "green": 1}
    ranks = [sev_rank[f["properties"]["severity"]] for f in feats]
    assert ranks == sorted(ranks, reverse=True), "cap must keep the worst first"
    assert "showing 3 of" in res.summary


def test_near_filters_by_distance_and_marks_origin():
    res = plugin.cmd_near(location="Manchester", radius_km=300)
    assert res.ok and res.kind == "geojson"
    feats = res.data["features"]
    assert feats[0]["properties"]["kind"] == "origin"       # query point first
    assert res.data["center"] == [53.4808, -2.2426]
    hazards = feats[1:]
    # every returned hazard is tagged with its distance and within the radius
    assert all(h["properties"]["distance_km"] <= 300 for h in hazards)
    dists = [h["properties"]["distance_km"] for h in hazards]
    assert dists == sorted(dists), "nearest first"


def test_near_accepts_coordinates():
    res = plugin.cmd_near(location="53.48,-2.24", radius_km=50)
    assert res.ok and res.data["center"][0] == 53.48


def test_near_unknown_place_errors():
    res = plugin.cmd_near(location="Atlantis")
    assert not res.ok and "Atlantis" in res.summary


def test_sources_reports_feeds():
    res = plugin.cmd_sources()
    assert res.ok and res.kind == "json"
    assert res.data["live"] is False
    assert "seed" in res.data["reached"]
    assert set(res.data["categories"]) == set(feeds.CATEGORIES)


if __name__ == "__main__":
    import sys
    funcs = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in funcs:
        fn()
    print(f"{len(funcs)} hazard-watch tests passed")
    sys.exit(0)
