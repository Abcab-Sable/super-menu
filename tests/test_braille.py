"""Tests for the generic GeoJSON→braille renderer (core.braille)."""
from super_menu.core import braille


def _is_braille(ch: str) -> bool:
    return 0x2800 <= ord(ch) <= 0x28FF


LINE_FC = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "properties": {"kind": "route"},
         "geometry": {"type": "LineString",
                      "coordinates": [[-1.5, 53.8], [-3.0, 53.0], [-4.0, 52.4]]}},
        {"type": "Feature", "properties": {"kind": "avoid", "label": "Zone"},
         "geometry": {"type": "Polygon",
                      "coordinates": [[[-2.8, 53.2], [-2.6, 53.2],
                                       [-2.6, 53.0], [-2.8, 53.0], [-2.8, 53.2]]]}},
        {"type": "Feature", "properties": {"kind": "origin", "label": "Leeds"},
         "geometry": {"type": "Point", "coordinates": [-1.5, 53.8]}},
        {"type": "Feature", "properties": {"kind": "destination", "label": "Aberystwyth"},
         "geometry": {"type": "Point", "coordinates": [-4.0, 52.4]}},
    ],
}


def test_render_dimensions_and_content():
    lines = braille.render_geojson(LINE_FC, width=40, height=12)
    assert len(lines) == 12
    joined = "".join(lines)
    assert any(_is_braille(c) for c in joined), "expected braille strokes"
    # point markers use the first letter of the label
    assert "L" in joined and "A" in joined


def test_min_size_is_clamped():
    lines = braille.render_geojson(LINE_FC, width=1, height=1)
    assert len(lines) == 3               # height floored to 3
    assert all(len(ln) <= 8 for ln in lines)  # width floored to 8


def test_empty_and_degenerate_inputs():
    assert braille.render_geojson({"type": "FeatureCollection", "features": []},
                                  40, 10) == [" "] * 10
    single = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"kind": "x", "label": "Solo"},
         "geometry": {"type": "Point", "coordinates": [0.0, 0.0]}}]}
    lines = braille.render_geojson(single, 20, 6)   # zero-span bbox must not crash
    assert len(lines) == 6
    assert "S" in "".join(lines)


def test_legend():
    leg = braille.legend(LINE_FC)
    assert leg and "Leeds" in leg and "Aberystwyth" in leg
    assert braille.legend({"type": "FeatureCollection", "features": []}) is None


if __name__ == "__main__":
    test_render_dimensions_and_content()
    test_min_size_is_clamped()
    test_empty_and_degenerate_inputs()
    test_legend()
    print("all braille tests passed")
