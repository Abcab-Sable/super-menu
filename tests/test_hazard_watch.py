"""Tests for the hazard-watch plugin: feed parsing, the source-swap seam, the
frontend GeoJSON contract, and the command surface — all offline.

Live feeds are exercised by feeding canned payloads through the same
``_get_json`` the adapters call (no network), mirroring how ``test_roads`` injects
a fake fetcher and ``test_route_avoider`` swaps the routing engine behind its
seam. Standalone runners don't load conftest, so we force offline at import time.
"""
import os
import tempfile

os.environ.setdefault("SUPER_MENU_OFFLINE", "1")
# Redirect runtime caches to a throwaway dir so the disk-cache short-circuit is
# deterministic and never reads the developer's real last_scan.json.
os.environ.setdefault("SUPER_MENU_HOME", tempfile.mkdtemp(prefix="hazwatch-test-"))

import time
from contextlib import contextmanager

from super_menu.core.registry import default_registry
from super_menu.plugins.hazard_watch import feeds, plugin
from super_menu.plugins.hazard_watch.feeds import (
    EONETFeed, GDACSFeed, Hazard, IMGWFeed, MetOfficeFeed, SeedFeed, UKFloodFeed,
    USGSFeed, _normalize, _pl_category, _teryt_voivodeship, _uk_category,
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


# ----- UK Environment Agency flood feed ------------------------------------ #

_EA_FLOODS = {"items": [
    {"@id": "http://ea/1", "description": "River Aire at Leeds",
     "severity": "Severe Flood Warning", "severityLevel": 1, "floodAreaID": "A1",
     "timeRaised": "2026-07-08T06:00:00", "isTidal": False,
     "floodArea": {"county": "West Yorkshire", "notation": "A1", "riverOrSea": "River Aire"}},
    {"@id": "http://ea/2", "description": "Tidal Trent", "severity": "Flood Alert",
     "severityLevel": 3, "floodAreaID": "A2", "timeRaised": "2026-07-08T05:00:00",
     "floodArea": {"county": "Lincs", "notation": "A2", "riverOrSea": "Trent"}},
    {"@id": "x", "description": "expired", "severity": "Warning no Longer in Force",
     "severityLevel": 4, "floodAreaID": "A3", "floodArea": {}},
]}
_EA_AREAS = {"items": [{"notation": "A1", "lat": 53.79, "long": -1.54},
                       {"notation": "A2", "lat": 53.2, "long": -0.8}]}


def test_uk_flood_parse_severity_and_area_join():
    def fake(url, timeout):
        return _EA_AREAS if "floodAreas" in url else _EA_FLOODS
    with _patched(feeds, "_get_json", fake), \
            _patched(feeds, "_load_area_cache", lambda: {}), \
            _patched(feeds, "_save_area_cache", lambda c: None):
        out = UKFloodFeed().fetch(days=30, timeout=5)
    assert len(out) == 2                                  # severityLevel 4 dropped
    severe = next(h for h in out if h.title.startswith("River Aire"))
    alert = next(h for h in out if h.title == "Tidal Trent")
    assert severe.severity == 3 and alert.severity == 1  # Severe→red, Alert→green
    assert severe.category == "flood" and severe.radius_km == 10.0
    assert severe.geometry["coordinates"] == [-1.54, 53.79]   # [long, lat] from area join
    assert severe.extra["county"] == "West Yorkshire"


def test_uk_flood_no_active_skips_area_fetch():
    """A quiet day (no active warnings) must not fetch the large area list."""
    def fake(url, timeout):
        if "floodAreas" in url:
            raise AssertionError("area list must not be fetched when nothing is active")
        return {"items": []}
    with _patched(feeds, "_get_json", fake):
        assert UKFloodFeed().fetch(days=30, timeout=5) == []


def test_uk_flood_unresolved_area_is_skipped():
    def fake(url, timeout):
        return {"items": []} if "floodAreas" in url else _EA_FLOODS
    with _patched(feeds, "_get_json", fake), \
            _patched(feeds, "_load_area_cache", lambda: {}), \
            _patched(feeds, "_save_area_cache", lambda c: None):
        out = UKFloodFeed().fetch(days=30, timeout=5)
    assert out == []                                     # no centroid ⇒ not invented


# ----- Poland IMGW meteo + hydro feed -------------------------------------- #

_PL_HYDRO = [
    {"stopień": "-1", "zdarzenie": "Susza hydrologiczna", "data_od": "2026-05-17 08:45:56",
     "data_do": "9999-12-31 23:59:59", "obszary": [{"wojewodztwo": "wielkopolskie"}]},
    {"stopień": "3", "zdarzenie": "Gwałtowny wzrost stanów wody", "data_od": "2026-07-08 10:00:00",
     "obszary": [{"wojewodztwo": "małopolskie"}, {"wojewodztwo": "podkarpackie"}]},
]
_PL_METEO = [
    {"stopien": "2", "nazwa_zdarzenia": "Burze z gradem", "obowiazuje_od": "2026-07-08 14:00:00",
     "teryt": ["1201", "1210", "1465"]},                 # voiv 12 (małopolskie) + 14 (mazowieckie)
    {"stopien": "1", "zdarzenie": "Oblodzenie", "obszary": [{"teryt": ["2261", "2262"]}]},
]


def _imgw(meteo, hydro):
    def fake(url, timeout):
        if "warningsmeteo" in url:
            return meteo
        if "warningshydro" in url:
            return hydro
        return {"status": False, "message": "No products were found"}
    return fake


def test_imgw_hydro_nested_wojewodztwo_and_drought_level():
    with _patched(feeds, "_get_json", _imgw({"status": False}, _PL_HYDRO)):
        out = IMGWFeed().fetch(days=30, timeout=5)
    droughts = [h for h in out if h.category == "drought"]
    floods = [h for h in out if h.category == "flood"]
    assert len(droughts) == 1 and droughts[0].severity == 1      # stopień -1 → green
    assert droughts[0].date == "2026-05-17T08:45:56"             # 9999 data_do ignored
    assert {h.extra["voivodeship"] for h in floods} == {"małopolskie", "podkarpackie"}
    assert all(h.severity == 3 for h in floods)                  # stopień 3 → red


def test_imgw_meteo_teryt_prefix_to_voivodeship():
    with _patched(feeds, "_get_json", _imgw(_PL_METEO, [])):
        out = IMGWFeed().fetch(days=30, timeout=5)
    storms = {h.extra["voivodeship"] for h in out if h.category == "storm"}
    assert storms == {"małopolskie", "mazowieckie"}   # 1465 → prefix 14 = mazowieckie
    ice = [h for h in out if h.category == "ice"]
    assert ice and ice[0].severity == 1 and ice[0].extra["voivodeship"] == "pomorskie"
    assert all(h.radius_km == 70.0 for h in out)      # regional footprint


def test_imgw_empty_endpoints_yield_nothing():
    with _patched(feeds, "_get_json", _imgw({"status": False}, {"status": False})):
        assert IMGWFeed().fetch(days=30, timeout=5) == []


def test_imgw_one_endpoint_down_is_tolerated():
    def fake(url, timeout):
        if "warningsmeteo" in url:
            raise feeds.HazardFeedError("meteo down")
        return _PL_HYDRO
    with _patched(feeds, "_get_json", fake):
        out = IMGWFeed().fetch(days=30, timeout=5)
    assert out and all(h.source == "IMGW" for h in out)   # hydro still delivered


def test_pl_helpers():
    assert _normalize("Dolnośląskie") == "dolnoslaskie"
    assert _teryt_voivodeship("1465") == {"14"}
    assert _teryt_voivodeship("99xx") == set()
    assert _pl_category("Susza hydrologiczna", hydro=True) == "drought"
    assert _pl_category("Opady marznące", hydro=False) == "ice"      # freezing → ice, not rain
    assert _pl_category("Silny wiatr", hydro=False) == "storm"
    assert _pl_category("Upał", hydro=False) == "other"


def test_regional_feeds_registered_when_online():
    was = os.environ.pop("SUPER_MENU_OFFLINE", None)
    try:
        names = {f.name for f in feeds.active_feeds()}
    finally:
        if was is not None:
            os.environ["SUPER_MENU_OFFLINE"] = was
    assert {"EONET", "USGS", "GDACS", "EA-Floods", "MetOffice", "IMGW"} <= names


# ----- GDACS global RSS feed ----------------------------------------------- #

_GDACS_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:gdacs="http://www.gdacs.org"
     xmlns:georss="http://www.georss.org/georss">
  <channel>
    <item>
      <title>Green forest fire notification in Australia</title>
      <link>https://www.gdacs.org/report.aspx?eventtype=WF&amp;eventid=1</link>
      <pubDate>Thu, 09 Jul 2026 15:00:36 GMT</pubDate>
      <georss:point>-14.88 142.23</georss:point>
      <gdacs:eventtype>WF</gdacs:eventtype>
      <gdacs:alertlevel>Green</gdacs:alertlevel>
      <gdacs:country>Australia</gdacs:country>
    </item>
    <item>
      <title>Red earthquake alert (M7.1)</title>
      <link>https://www.gdacs.org/report.aspx?eventtype=EQ&amp;eventid=2</link>
      <pubDate>Wed, 08 Jul 2026 10:00:00 GMT</pubDate>
      <georss:point>38.2 -122.0</georss:point>
      <gdacs:eventtype>EQ</gdacs:eventtype>
      <gdacs:alertlevel>Red</gdacs:alertlevel>
    </item>
    <item>
      <title>No coordinates here</title>
      <gdacs:eventtype>TC</gdacs:eventtype>
      <gdacs:alertlevel>Orange</gdacs:alertlevel>
    </item>
  </channel>
</rss>"""


def test_gdacs_parse_alert_levels_and_types():
    import xml.etree.ElementTree as ET
    root = ET.fromstring(_GDACS_RSS)
    # Wide window so the look-back filter is a no-op regardless of wall-clock.
    with _patched(feeds, "_get_xml", lambda url, timeout: root):
        out = GDACSFeed().fetch(days=36500, timeout=1)
    assert len(out) == 2                                  # the point-less item is skipped
    fire = next(h for h in out if h.category == "wildfire")
    quake = next(h for h in out if h.category == "earthquake")
    assert fire.severity == 1 and quake.severity == 3    # Green→green(1), Red→red(3)
    assert fire.geometry["coordinates"] == [142.23, -14.88]   # [lng, lat]
    assert fire.extra["country"] == "Australia"
    assert quake.extra["event_url"].endswith("eventid=2")
    assert quake.date and quake.date.startswith("2026-07-08T10:00:00")
    assert all(h.source == "GDACS" for h in out)


def test_gdacs_unknown_type_is_other():
    rss = _GDACS_RSS.replace("<gdacs:eventtype>WF</gdacs:eventtype>",
                             "<gdacs:eventtype>ZZ</gdacs:eventtype>")
    import xml.etree.ElementTree as ET
    root = ET.fromstring(rss)
    with _patched(feeds, "_get_xml", lambda url, timeout: root):
        out = GDACSFeed().fetch(days=36500, timeout=1)
    assert any(h.category == "other" for h in out)


def test_gdacs_respects_days_window():
    import xml.etree.ElementTree as ET
    # One item stamped a decade ago, one with no date at all. A narrow window must
    # drop the old dated item but keep the undated one (could be a current event).
    rss = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:gdacs="http://www.gdacs.org"
     xmlns:georss="http://www.georss.org/georss">
  <channel>
    <item><title>Ancient flood</title><georss:point>10 10</georss:point>
      <gdacs:eventtype>FL</gdacs:eventtype><gdacs:alertlevel>Orange</gdacs:alertlevel>
      <pubDate>Mon, 01 Jan 2001 00:00:00 GMT</pubDate></item>
    <item><title>Undated storm</title><georss:point>20 20</georss:point>
      <gdacs:eventtype>TC</gdacs:eventtype><gdacs:alertlevel>Red</gdacs:alertlevel></item>
  </channel>
</rss>"""
    root = ET.fromstring(rss)
    with _patched(feeds, "_get_xml", lambda url, timeout: root):
        out = GDACSFeed().fetch(days=7, timeout=1)
    titles = {h.title for h in out}
    assert titles == {"Undated storm"}                   # old one trimmed, undated kept


# ----- UK Met Office severe-weather feed ----------------------------------- #

def _metoffice_region_rss(title):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item><title>{title}</title>
    <link>https://www.metoffice.gov.uk/weather/warnings</link>
    <pubDate>Wed, 08 Jul 2026 09:00:00 GMT</pubDate></item>
</channel></rss>"""


def test_metoffice_places_by_region_and_reads_colour():
    import xml.etree.ElementTree as ET
    # Only Wales ("wl") carries a warning; every other region is empty.
    def fake(url, timeout):
        if url.endswith("/wl"):
            return ET.fromstring(_metoffice_region_rss("Amber warning of rain"))
        return ET.fromstring("<rss version='2.0'><channel/></rss>")
    with _patched(feeds, "_get_xml", fake):
        out = MetOfficeFeed().fetch(days=7, timeout=1)
    assert len(out) == 1
    h = out[0]
    assert h.severity == 2 and h.extra["colour"] == "amber"   # amber → 2
    assert h.category == "flood"                              # rain → flood
    assert h.extra["region"] == "Wales"
    assert h.geometry["coordinates"] == [-3.80, 52.40]        # Wales centroid, [lng, lat]
    assert h.radius_km == feeds.UK_WARNING_RADIUS_KM
    assert h.title.startswith("Amber warning of rain — Wales")


def test_metoffice_all_regions_down_raises():
    def boom(url, timeout):
        raise feeds.HazardFeedError("region unreachable")
    with _patched(feeds, "_get_xml", boom):
        try:
            MetOfficeFeed().fetch(days=7, timeout=1)
        except feeds.HazardFeedError:
            return
    raise AssertionError("a total regional outage must raise, not look like a quiet day")


def test_metoffice_bails_after_consecutive_failures():
    """A total outage must stop early, not wait out all 16 regions sequentially."""
    calls = {"n": 0}
    def boom(url, timeout):
        calls["n"] += 1
        raise feeds.HazardFeedError("region unreachable")
    with _patched(feeds, "_get_xml", boom):
        try:
            MetOfficeFeed().fetch(days=7, timeout=20)
        except feeds.HazardFeedError:
            pass
    assert calls["n"] == MetOfficeFeed._MAX_CONSECUTIVE_FAILS   # bailed, didn't poll all 16


def test_metoffice_quiet_day_is_empty_not_error():
    import xml.etree.ElementTree as ET
    with _patched(feeds, "_get_xml",
                  lambda url, timeout: ET.fromstring("<rss version='2.0'><channel/></rss>")):
        assert MetOfficeFeed().fetch(days=7, timeout=1) == []


def test_uk_category_mapping():
    assert _uk_category("Yellow warning of snow and ice") == "ice"
    assert _uk_category("Amber warning of thunderstorms") == "storm"
    assert _uk_category("Red warning of rain") == "flood"
    assert _uk_category("Yellow warning of extreme heat") == "other"


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


# ----- disk-cache short-circuit + per-feed backfill ------------------------ #

def _clear_hazard_cache():
    for path in (feeds._cache_path(), feeds._feed_cache_path()):
        try:
            path.unlink()
        except OSError:
            pass


class _SpyFeed(feeds.HazardFeed):
    """A live feed that counts fetches and returns one fixed hazard."""
    name = "spy"
    live = True

    def __init__(self):
        self.calls = 0

    def fetch(self, days, timeout):
        self.calls += 1
        return [Hazard(title="spy-event", category="storm", severity=2, source="spy",
                       geometry={"type": "Point", "coordinates": [0.0, 0.0]})]


class _FlakyFeed(feeds.HazardFeed):
    """A live feed whose fetch can be flipped to fail on demand."""
    live = True

    def __init__(self, name, hazards):
        self.name = name
        self._hazards = hazards
        self.fail = False

    def fetch(self, days, timeout):
        if self.fail:
            raise feeds.HazardFeedError("down")
        return list(self._hazards)


def test_fresh_cache_short_circuits_network():
    _clear_hazard_cache()
    spy = _SpyFeed()
    try:
        with _patched(feeds, "active_feeds", lambda: [spy]):
            first = feeds.collect(force=True)          # populates the disk cache
            assert spy.calls == 1 and first["from_cache"] is False
            second = feeds.collect()                   # within 15 min → served from disk
            assert spy.calls == 1, "a fresh cache must not re-poll the feeds"
            assert second["from_cache"] is True and second["live"] is True
            feeds.collect(force=True)                  # force bypasses the cache
            assert spy.calls == 2
    finally:
        _clear_hazard_cache()


def test_stale_cache_is_not_short_circuited():
    _clear_hazard_cache()
    spy = _SpyFeed()
    try:
        with _patched(feeds, "active_feeds", lambda: [spy]):
            feeds.collect(force=True)                  # writes the cache
            # Backdate the cache well past the 15-min freshness window.
            old = time.time() - (feeds._CACHE_TTL_S + 120)
            os.utime(feeds._cache_path(), (old, old))
            feeds.collect()                            # stale → must re-poll
            assert spy.calls == 2
    finally:
        _clear_hazard_cache()


def test_per_feed_backfill_on_single_failure():
    _clear_hazard_cache()
    h_a = Hazard(title="A-event", category="storm", severity=2, source="AA",
                 geometry={"type": "Point", "coordinates": [1.0, 1.0]})
    h_b = Hazard(title="B-event", category="flood", severity=3, source="BB",
                 geometry={"type": "Point", "coordinates": [2.0, 2.0]})
    a, b = _FlakyFeed("AA", [h_a]), _FlakyFeed("BB", [h_b])
    try:
        with _patched(feeds, "active_feeds", lambda: [a, b]):
            first = feeds.collect(force=True)          # both good → per-feed cache seeded
            assert {"A-event", "B-event"} <= {h.title for h in first["hazards"]}
            b.fail = True
            second = feeds.collect(force=True)         # B down → restored from its cache
        titles = {h.title for h in second["hazards"]}
        assert "A-event" in titles and "B-event" in titles   # B survives despite failing
        assert "BB" in second["errors"]                      # failure still recorded
        assert any("BB (cached)" == s for s in second["sources"])
    finally:
        _clear_hazard_cache()


def test_per_feed_backfill_skipped_when_all_live_feeds_fail():
    """If no live feed succeeds we take the whole-scan fallback, not fragments."""
    _clear_hazard_cache()
    h_a = Hazard(title="A-event", category="storm", severity=2, source="AA",
                 geometry={"type": "Point", "coordinates": [1.0, 1.0]})
    a = _FlakyFeed("AA", [h_a])
    try:
        with _patched(feeds, "active_feeds", lambda: [a]):
            feeds.collect(force=True)                  # seed per-feed cache for AA
            a.fail = True
            with _patched(feeds, "_read_cache", lambda *args, **kw: None):
                bundle = feeds.collect(force=True)     # AA down, no whole-scan cache → seed
        assert bundle["sources"] == ["seed"] and "AA" in bundle["errors"]
        assert all(h.title != "A-event" for h in bundle["hazards"])  # no fragment leak
    finally:
        _clear_hazard_cache()


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


def test_active_region_filters_before_cap():
    # The seed spans several countries; region focus keeps only the in-bbox events.
    pl = plugin.cmd_active(region="poland")
    assert pl.ok and pl.data["region"] == "poland"
    hazards = [f for f in pl.data["features"] if f["properties"].get("category") != "origin"]
    assert hazards and all(14.1 <= f["geometry"]["coordinates"][0] <= 24.2
                           for f in hazards)              # all within Poland's longitudes
    assert {f["properties"]["source"] for f in hazards} == {"IMGW"}
    assert not plugin.cmd_active(region="atlantis").ok


def test_active_region_uk_matches_ea_flood():
    uk = plugin.cmd_active(region="uk")
    assert uk.ok
    srcs = {f["properties"]["source"] for f in uk.data["features"]}
    assert "EA-Floods" in srcs                            # the seeded Lincoln warning


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
