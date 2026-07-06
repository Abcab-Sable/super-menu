"""A tiny local web UI for the route planner — a 4th surface over the same plugin.

``super-menu web`` starts this. It serves a single Leaflet page (real OSM road
tiles) and one JSON endpoint, ``POST /api/route``, which calls the very same
``cmd_route`` the TUI/CLI/MCP use and returns the identical GeoJSON
FeatureCollection. So the browser renders the exact ``kind="geojson"`` payload
the braille map does — just on a real road basemap.

Stdlib only (``http.server``); no framework dependency. The request-shaping logic
lives in :func:`handle_route` so it is unit-testable without binding a socket.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .plugin import active_adapter, cmd_route, set_api_key

_INDEX = Path(__file__).parent / "web" / "index.html"
_NOMINATIM = "https://nominatim.openstreetmap.org/search"


def handle_route(payload: dict) -> dict:
    """Turn a web request body into a ``cmd_route`` call and a JSON-able reply.

    Reuses the plugin end to end: the reply's ``geojson`` is exactly the
    FeatureCollection (route + avoid circles + endpoints, metrics as foreign
    members) that every other surface renders."""
    origin = payload.get("origin") or {}
    dest = payload.get("destination") or {}
    if "lat" not in origin or "lat" not in dest:
        return {"ok": False, "error": "origin and destination are both required"}

    def _pt(p: dict) -> str:
        return f"{p['lat']},{p['lng']}"

    zones = payload.get("avoid_zones") or []

    def _spec(z: dict) -> str:
        base = f"{z['lat']},{z['lng']},{z.get('radius_km', 5)}"
        # A label rides in the last, ';'-delimited field of the avoid grammar, so
        # strip ';' from it or a crafted label would inject an extra avoid zone.
        label = str(z.get("label", "")).replace(";", " ").strip()
        return f"{base},{label}" if label else base

    avoid = ";".join(_spec(z) for z in zones)
    result = cmd_route(
        origin=_pt(origin),
        destination=_pt(dest),
        avoid=avoid or None,
        avoid_motorways=bool(payload.get("avoid_motorways")),
        profile=payload.get("profile") or "driving-car",
    )
    if not result.ok:
        return {"ok": False, "error": result.summary}
    return {"ok": True, "summary": result.summary, "geojson": result.data}


def handle_geocode(query: str) -> dict:
    """Resolve a place name to a point. Uses ORS when a live key is set, else the
    keyless OpenStreetMap Nominatim geocoder (so search works without a key)."""
    query = (query or "").strip()
    if not query:
        return {"ok": False, "error": "empty query"}
    engine = active_adapter()
    if engine.live:
        try:
            pt = engine.geocode(query)
            return {"ok": True, "lat": pt.lat, "lng": pt.lng, "label": query}
        except Exception:
            pass  # fall back to Nominatim
    try:
        lat, lng, label = _nominatim(query)
        return {"ok": True, "lat": lat, "lng": lng, "label": label}
    except Exception as exc:
        return {"ok": False, "error": f"could not find '{query}': {exc}"}


def _nominatim(query: str) -> tuple[float, float, str]:
    qs = urllib.parse.urlencode({"q": query, "format": "json", "limit": 1})
    req = urllib.request.Request(
        f"{_NOMINATIM}?{qs}", headers={"User-Agent": "super-menu-route-avoider/0.1"})
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
        rows = json.loads(resp.read().decode("utf-8"))
    if not rows:
        raise ValueError("no match")
    row = rows[0]
    return float(row["lat"]), float(row["lon"]), row.get("display_name", query)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # keep the console quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict) -> None:
        self._send(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json")

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            try:
                self._send(200, _INDEX.read_bytes(), "text/html; charset=utf-8")
            except OSError:
                self._send(500, b"index.html missing", "text/plain")
        elif parsed.path == "/api/status":
            self._send_json(_status_payload())
        elif parsed.path == "/api/geocode":
            q = urllib.parse.parse_qs(parsed.query).get("q", [""])[0]
            self._send_json(handle_geocode(q))
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/api/route":
                reply = handle_route(payload)
            elif self.path == "/api/key":
                set_api_key(payload.get("key"))
                reply = {"ok": True, **_status_payload()}
            else:
                self._send(404, b"not found", "text/plain")
                return
        except Exception as exc:  # never 500 with a stack trace to the browser
            reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        self._send_json(reply)


def _status_payload() -> dict:
    from .adapter import PROFILES
    from .plugin import active_adapter
    engine = active_adapter()
    return {"engine": engine.name, "live": engine.live, "profiles": list(PROFILES)}


def run(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    server = ThreadingHTTPServer((host, port), _Handler)
    url = f"http://{host}:{port}/"
    print(f"Route planner web UI → {url}  (Ctrl+C to stop)")
    if not _status_payload()["live"]:
        print("  engine: offline estimate — set ORS_API_KEY for real road routing")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
