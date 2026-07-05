"""Tests for the self-hosted Valhalla adapter — no running engine needed.

The HTTP call is the only untestable part offline; everything around it (body
building, polyline decoding, response + error parsing, engine selection) is
exercised against fixtures built with a reference polyline encoder."""
import io
import json
from urllib.error import HTTPError

from super_menu.plugins.route_avoider.adapter import (
    GeoPoint, NoRouteError, RoutingError, ValhallaAdapter, _decode_polyline,
)
from super_menu.plugins.route_avoider import geo


def _encode_polyline(coords, precision=6):
    """Reference Google-polyline encoder (lat,lng deltas) for building fixtures."""
    factor = 10 ** precision

    def enc(value):
        value = ~(value << 1) if value < 0 else (value << 1)
        out = ""
        while value >= 0x20:
            out += chr((0x20 | (value & 0x1F)) + 63)
            value >>= 5
        return out + chr(value + 63)

    prev_lat = prev_lng = 0
    parts = []
    for lng, lat in coords:
        ilat, ilng = round(lat * factor), round(lng * factor)
        parts.append(enc(ilat - prev_lat))
        parts.append(enc(ilng - prev_lng))
        prev_lat, prev_lng = ilat, ilng
    return "".join(parts)


def test_decode_polyline_roundtrip():
    coords = [[-1.5491, 53.8008], [-2.8000, 53.1000], [-4.0810, 52.4140]]
    decoded = _decode_polyline(_encode_polyline(coords))
    assert len(decoded) == len(coords)
    for (lng, lat), (elng, elat) in zip(decoded, coords):
        assert abs(lng - elng) < 1e-5 and abs(lat - elat) < 1e-5


def test_build_body_maps_costing_and_polygons():
    o, d = GeoPoint(53.8, -1.55), GeoPoint(52.41, -4.08)
    ring = geo.circle_ring(53.2, -2.7, 20)
    body = ValhallaAdapter._build_body(o, d, [ring], avoid_motorways=True,
                                       profile="driving-car")
    assert body["costing"] == "auto"
    assert body["units"] == "kilometers"
    assert body["locations"][0] == {"lat": 53.8, "lon": -1.55}
    assert body["exclude_polygons"] == [ring]           # our rings pass straight through
    assert body["costing_options"]["auto"]["use_highways"] == 0


def test_build_body_walking_ignores_motorways():
    o, d = GeoPoint(53.8, -1.55), GeoPoint(52.41, -4.08)
    body = ValhallaAdapter._build_body(o, d, [], avoid_motorways=True,
                                       profile="foot-walking")
    assert body["costing"] == "pedestrian"
    assert "costing_options" not in body                # highways flag is auto-only
    assert "exclude_polygons" not in body


def test_parse_route_success():
    coords = [[-1.5491, 53.8008], [-2.5, 53.2], [-4.0810, 52.4140]]
    payload = {"trip": {"status": 0, "summary": {"length": 214.6, "time": 9000},
                        "legs": [{"shape": _encode_polyline(coords)}]}}
    res = ValhallaAdapter._parse_route(payload)
    assert res.distance_km == 214.6
    assert res.duration_min == 150.0                    # 9000 s / 60
    assert res.geometry["type"] == "LineString"
    assert res.waypoints == len(coords) and res.bbox is not None


def test_parse_route_no_path_status():
    try:
        ValhallaAdapter._parse_route({"trip": {"status": 442,
                                               "status_message": "No path could be found"}})
    except NoRouteError as exc:
        assert exc.reason == "no_route"
    else:
        raise AssertionError("expected NoRouteError")


def _http_error(code, body):
    return HTTPError("http://x/route", 400, "Bad Request", {},
                     io.BytesIO(json.dumps(body).encode()))


def test_translate_http_error():
    no_path = ValhallaAdapter._translate_http_error(
        _http_error(400, {"error_code": 442, "error": "No path could be found for input"}))
    assert isinstance(no_path, NoRouteError)
    other = ValhallaAdapter._translate_http_error(
        _http_error(400, {"error_code": 171, "error": "Insufficient locations"}))
    assert isinstance(other, RoutingError) and not isinstance(other, NoRouteError)
    assert "Insufficient" in str(other)


def test_geocode_is_unsupported():
    try:
        ValhallaAdapter("http://localhost:8002").geocode("Leeds")
    except RoutingError as exc:
        assert "routing-only" in str(exc)
    else:
        raise AssertionError("Valhalla geocode should raise")


def test_active_adapter_prefers_valhalla(monkeypatch):
    from super_menu.plugins.route_avoider import plugin
    monkeypatch.setenv("VALHALLA_URL", "http://localhost:8002")
    monkeypatch.setenv("ORS_API_KEY", "should-be-ignored")
    engine = plugin.active_adapter()
    assert isinstance(engine, ValhallaAdapter)
    assert engine.base_url == "http://localhost:8002" and engine.live is True


if __name__ == "__main__":
    test_decode_polyline_roundtrip()
    test_build_body_maps_costing_and_polygons()
    test_build_body_walking_ignores_motorways()
    test_parse_route_success()
    test_parse_route_no_path_status()
    test_translate_http_error()
    test_geocode_is_unsupported()
    print("all valhalla tests passed (run via pytest for the fixture-based selection test)")
