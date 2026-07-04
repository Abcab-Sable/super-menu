"""Tests for the generic GeoJSON→braille renderer (core.braille)."""
from super_menu.core import braille


def _is_braille(ch: str) -> bool:
    return 0x2800 <= ord(ch) <= 0x28FF


def _plain(text) -> list[str]:
    return text.plain.rstrip("\n").split("\n")


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
        {"type": "Feature", "properties": {"kind": "origin", "marker": "A", "label": "Leeds"},
         "geometry": {"type": "Point", "coordinates": [-1.5, 53.8]}},
        {"type": "Feature", "properties": {"kind": "destination", "marker": "B",
                                           "label": "Aberystwyth"},
         "geometry": {"type": "Point", "coordinates": [-4.0, 52.4]}},
    ],
}


def test_render_dimensions_and_content():
    lines = _plain(braille.render_geojson(LINE_FC, width=40, height=14, basemap=False))
    assert len(lines) == 14
    joined = "".join(lines)
    assert any(_is_braille(c) for c in joined), "expected braille strokes"
    assert "A" in joined and "B" in joined            # endpoint markers


def test_scale_bar_and_legend_rows():
    text = braille.render_geojson(LINE_FC, width=48, height=16, basemap=False)
    lines = _plain(text)
    assert "km" in lines[-2]                           # scale bar row
    assert "Leeds" in lines[-1] and "Aberystwyth" in lines[-1]   # legend row


def test_colour_is_applied():
    # the route/markers carry Rich styles, so the Text has more than one span
    text = braille.render_geojson(LINE_FC, width=40, height=14, basemap=False)
    styles = {str(span.style) for span in text.spans if span.style}
    assert any("cyan" in s or "green" in s or "red" in s for s in styles)


def test_basemap_adds_coastline():
    # a frame over the UK should pull in bundled coastline strokes
    plain_off = "".join(_plain(braille.render_geojson(LINE_FC, 60, 20, basemap=False)))
    plain_on = "".join(_plain(braille.render_geojson(LINE_FC, 60, 20, basemap=True)))
    n_off = sum(_is_braille(c) for c in plain_off)
    n_on = sum(_is_braille(c) for c in plain_on)
    assert n_on > n_off, "basemap should add coastline dots"


def test_min_size_is_clamped():
    lines = _plain(braille.render_geojson(LINE_FC, width=1, height=1, basemap=False))
    assert len(lines) == 3                             # height floored to 3
    assert all(len(ln) <= 8 for ln in lines)           # width floored to 8


def test_empty_and_degenerate_inputs():
    empty = braille.render_geojson({"type": "FeatureCollection", "features": []}, 40, 10)
    assert len(_plain(empty)) == 10
    single = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"kind": "origin", "marker": "S", "label": "Solo"},
         "geometry": {"type": "Point", "coordinates": [0.0, 0.0]}}]}
    lines = _plain(braille.render_geojson(single, 20, 8, basemap=False))  # zero-span bbox
    assert len(lines) == 8
    assert "S" in "".join(lines)


def test_legend_helper():
    leg = braille.legend(LINE_FC)
    assert leg and "Leeds" in leg and "Aberystwyth" in leg
    assert braille.legend({"type": "FeatureCollection", "features": []}) is None


if __name__ == "__main__":
    test_render_dimensions_and_content()
    test_scale_bar_and_legend_rows()
    test_colour_is_applied()
    test_basemap_adds_coastline()
    test_min_size_is_clamped()
    test_empty_and_degenerate_inputs()
    test_legend_helper()
    print("all braille tests passed")
