"""Tests for the route-avoider web surface's request-shaping (no socket needed)."""
from super_menu.plugins.route_avoider import webserver


def test_missing_endpoints_errors():
    r = webserver.handle_route({"origin": {"lat": 53.8, "lng": -1.5}})
    assert r["ok"] is False and "required" in r["error"]


def test_route_returns_same_geojson_contract():
    r = webserver.handle_route({
        "origin": {"lat": 53.8008, "lng": -1.5491},
        "destination": {"lat": 52.4140, "lng": -4.0810},
        "avoid_zones": [{"lat": 53.1, "lng": -2.8, "radius_km": 20, "label": "Mid"}],
        "profile": "driving-car",
    })
    assert r["ok"] is True
    fc = r["geojson"]
    # identical payload to every other surface: a FeatureCollection with metrics
    assert fc["type"] == "FeatureCollection"
    assert fc["distance_km"] > 0
    assert fc["avoid_zones_applied"] == 1
    kinds = {f["properties"]["kind"] for f in fc["features"]}
    assert {"route", "avoid", "origin", "destination"} <= kinds


def test_no_route_reports_error():
    r = webserver.handle_route({
        "origin": {"lat": 53.8008, "lng": -1.5491},
        "destination": {"lat": 52.4140, "lng": -4.0810},
        "avoid_zones": [{"lat": 52.4140, "lng": -4.0810, "radius_km": 15}],  # boxes in the end
    })
    assert r["ok"] is False and "enclosed" in r["error"]


def test_status_payload():
    s = webserver._status_payload()
    assert "engine" in s and isinstance(s["live"], bool) and s["profiles"]


if __name__ == "__main__":
    test_missing_endpoints_errors()
    test_route_returns_same_geojson_contract()
    test_no_route_reports_error()
    test_status_payload()
    print("all webserver tests passed")
