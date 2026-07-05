"""Tests for the zoom-aware OSM road underlay (core.roads) — all offline."""
import contextlib
import os
import tempfile

from super_menu.core import braille, roads

VIEW = (-1.65, 53.75, -1.45, 53.85)  # a town-scale window over Leeds

OVERPASS_FIXTURE = {
    "elements": [
        {"type": "way", "id": 1, "tags": {"highway": "motorway"},
         "geometry": [{"lat": 53.80, "lon": -1.60}, {"lat": 53.81, "lon": -1.55},
                      {"lat": 53.82, "lon": -1.50}]},
        {"type": "way", "id": 2, "tags": {"highway": "residential"},
         "geometry": [{"lat": 53.79, "lon": -1.56}, {"lat": 53.79, "lon": -1.55}]},
        {"type": "way", "id": 3, "tags": {"highway": "primary"},
         "geometry": [{"lat": 53.78, "lon": -1.52}]},          # 1 point: dropped
        {"type": "node", "id": 4},                              # not a way: dropped
    ],
}


@contextlib.contextmanager
def _fresh():
    """A temp cache dir + cleared memo, restoring env for later test modules."""
    prev_home = os.environ.get("SUPER_MENU_HOME")
    prev_offline = os.environ.get("SUPER_MENU_OFFLINE")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["SUPER_MENU_HOME"] = tmp
        os.environ.pop("SUPER_MENU_OFFLINE", None)
        roads._memo.clear()
        roads._failures.clear()
        try:
            yield tmp
        finally:
            roads._memo.clear()
            roads._failures.clear()
            for k, v in (("SUPER_MENU_HOME", prev_home),
                         ("SUPER_MENU_OFFLINE", prev_offline)):
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def test_classes_for_span_tiers():
    assert "residential" in roads.classes_for_span(0.1)
    assert "residential" not in roads.classes_for_span(0.4)
    assert roads.classes_for_span(2.0) == ("motorway", "trunk", "primary")
    assert roads.classes_for_span(5.0) == ("motorway", "trunk")
    assert roads.classes_for_span(90.0) is None                # world scale: no fetch


def test_fetch_bbox_pads_and_quantizes():
    got = roads.fetch_bbox(VIEW)
    assert got[0] <= VIEW[0] and got[1] <= VIEW[1]             # contains the view…
    assert got[2] >= VIEW[2] and got[3] >= VIEW[3]
    assert all(abs(v / 0.05 - round(v / 0.05)) < 1e-9 for v in got)  # …on the lattice…
    assert got == roads.fetch_bbox((VIEW[0] + 0.002, VIEW[1] + 0.002,
                                    VIEW[2] + 0.002, VIEW[3] + 0.002))  # …so pans reuse it


def test_parse_overpass_shapes():
    lines = roads.parse_overpass(OVERPASS_FIXTURE)
    assert [cls for cls, _ in lines] == ["motorway", "residential"]
    assert lines[0][1][0] == [-1.60, 53.80]                    # [lng, lat] order


def test_decimate_keeps_endpoints():
    dense = [["primary", [[0.0, 0.0], [0.0001, 0.0], [0.0002, 0.0], [1.0, 1.0]]]]
    out = roads.decimate(dense, tol=0.01)
    assert out == [["primary", [[0.0, 0.0], [1.0, 1.0]]]]


def test_roads_for_view_caches_fetches():
    calls = []

    def fake_fetch(bbox, classes):
        calls.append(classes)
        return roads.parse_overpass(OVERPASS_FIXTURE)

    with _fresh():
        first = roads.roads_for_view(VIEW, fetch=fake_fetch)
        assert [cls for cls, _ in first] == ["motorway", "residential"]
        roads._memo.clear()                                    # force the disk path
        second = roads.roads_for_view(VIEW, fetch=fake_fetch)
        assert second == first
        assert len(calls) == 1, "second call must come from the disk cache"


def test_roads_for_view_failure_is_soft_and_backed_off():
    calls = []

    def bad_fetch(bbox, classes):
        calls.append(1)
        raise OSError("no network")

    with _fresh():
        assert roads.roads_for_view(VIEW, fetch=bad_fetch) == []
        assert roads.roads_for_view(VIEW, fetch=bad_fetch) == []
        assert len(calls) == 1, "failures are negative-cached, not retried per render"


def test_empty_fetch_is_not_persisted():
    with _fresh():
        assert roads.roads_for_view(VIEW, fetch=lambda b, c: []) == []
        cache_files = list((roads._cache_dir()).glob("roads-*.json"))
        assert cache_files == [], "an empty reply must never poison the disk cache"


def test_country_scale_needs_allow_slow():
    wide = (-5.0, 51.0, 0.0, 55.0)             # ~a-minute fetch on public Overpass
    calls = []

    def fake_fetch(bbox, classes):
        calls.append(1)
        return [["motorway", [[-2.0, 53.0], [-2.1, 53.1]]]]

    with _fresh():
        assert roads.roads_for_view(wide, fetch=fake_fetch) == []
        assert calls == [], "sync callers must not start a slow fetch"
        assert roads.roads_for_view(wide, fetch=fake_fetch, allow_slow=True)
        assert len(calls) == 1
        roads._memo.clear()
        assert roads.roads_for_view(wide, fetch=fake_fetch)  # cached ⇒ sync gets it
        assert len(calls) == 1


def test_offline_switch_blocks_fetching():
    with _fresh():
        os.environ["SUPER_MENU_OFFLINE"] = "1"
        boom = lambda bbox, classes: (_ for _ in ()).throw(AssertionError("fetched"))
        assert roads.roads_for_view(VIEW, fetch=boom) == []


def test_braille_draws_road_underlay():
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"kind": "route"},
         "geometry": {"type": "LineString",
                      "coordinates": [[-1.60, 53.78], [-1.48, 53.84]]}}]}
    road_lines = roads.parse_overpass(OVERPASS_FIXTURE)
    off = braille.render_geojson(fc, 50, 16, basemap=False)
    on = braille.render_geojson(fc, 50, 16, basemap=False, roads=road_lines)
    n = lambda t: sum(0x2800 <= ord(c) <= 0x28FF for c in t.plain)
    assert n(on) > n(off), "roads should add strokes"
    assert "© OSM" in on.plain and "© OSM" not in off.plain    # attribution rides the scale bar
    styles = {str(span.style) for span in on.spans if span.style}
    assert any("orange" in s for s in styles), "motorways draw warm"


if __name__ == "__main__":
    test_classes_for_span_tiers()
    test_fetch_bbox_pads_and_quantizes()
    test_parse_overpass_shapes()
    test_decimate_keeps_endpoints()
    test_roads_for_view_caches_fetches()
    test_roads_for_view_failure_is_soft_and_backed_off()
    test_empty_fetch_is_not_persisted()
    test_country_scale_needs_allow_slow()
    test_offline_switch_blocks_fetching()
    test_braille_draws_road_underlay()
    print("all roads tests passed")
