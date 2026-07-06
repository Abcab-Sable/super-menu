"""The super-menu web dashboard — every plugin, one HUD.

``super-menu web`` serves this. Like the TUI, the page is built entirely from
plugin metadata: ``GET /api/menu`` returns the registry (plugins → commands →
typed params) and the front end auto-generates forms from it; ``POST /api/run``
executes any command through the same ``Command.run`` path every other surface
uses and returns ``CommandResult.to_dict()`` unchanged. A new plugin dropped
into ``plugins/`` appears in the dashboard with zero web code.

The route-avoider's dedicated map planner remains mounted at ``/route`` (with
its ``/api/route`` / ``/api/geocode`` / ``/api/key`` endpoints delegated to the
plugin's own handlers), so the deep-dive surface survives the generic one.

Stdlib only (``http.server``) — no framework. The payload builders
(:func:`menu_payload`, :func:`handle_run`) are pure so they unit-test without a
socket.
"""
from __future__ import annotations

import json
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from super_menu.core.registry import default_registry
from super_menu.web import chat

_STATIC = Path(__file__).parent / "static"


def menu_payload() -> dict:
    """The full registry as JSON: what the dashboard builds itself from."""
    plugins = []
    for p in default_registry().plugins:
        plugins.append({
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "icon": p.icon,
            "commands": [{
                "name": c.name,
                "help": c.help,
                "params": [{
                    "name": prm.name,
                    "type": prm.type,
                    "required": prm.required,
                    "default": prm.default,
                    "help": prm.help,
                    "choices": prm.choices,
                } for prm in c.params],
            } for c in p.commands()],
        })
    return {"plugins": plugins}


def handle_run(payload: dict) -> dict:
    """Execute ``{plugin, command, params}`` and return the result dict.

    Delegates coercion, missing-required checks, and error wrapping to
    ``Command.run`` — identical semantics to the CLI and MCP surfaces."""
    plugin = default_registry().get(str(payload.get("plugin") or ""))
    if plugin is None:
        return {"ok": False, "summary": f"unknown plugin: {payload.get('plugin')}",
                "kind": "text", "columns": None, "data": None}
    command = plugin.command(str(payload.get("command") or ""))
    if command is None:
        return {"ok": False, "summary": f"unknown command: {payload.get('command')}",
                "kind": "text", "columns": None, "data": None}
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        params = {}
    return command.run(params).to_dict()


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

    def _send_file(self, path: Path) -> None:
        try:
            self._send(200, path.read_bytes(), "text/html; charset=utf-8")
        except OSError:
            self._send(500, b"page missing", "text/plain")

    def _send_sse(self, events) -> None:
        """Stream front-end events as Server-Sent Events. ``ThreadingHTTPServer``
        gives each request its own thread, so a long-lived stream just occupies
        that thread until Claude's turn ends or the browser disconnects."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")  # disable proxy buffering
        self.end_headers()
        try:
            for ev in events:
                self.wfile.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                                 .encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # browser navigated away mid-stream

    def do_GET(self) -> None:
        from super_menu.plugins.route_avoider import webserver as ra_web

        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            self._send_file(_STATIC / "index.html")
        elif parsed.path == "/route":
            self._send_file(ra_web._INDEX)          # the dedicated map planner
        elif parsed.path == "/api/menu":
            self._send_json(menu_payload())
        elif parsed.path == "/api/status":
            self._send_json({**ra_web._status_payload(), "chat": chat.claude_available()})
        elif parsed.path == "/api/geocode":
            q = urllib.parse.parse_qs(parsed.query).get("q", [""])[0]
            self._send_json(ra_web.handle_geocode(q))
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        from super_menu.plugins.route_avoider import webserver as ra_web
        from super_menu.plugins.route_avoider.plugin import set_api_key

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) or b"{}"
        if self.path == "/api/chat":
            try:
                body = json.loads(raw)
            except ValueError:
                body = {}
            self._send_sse(chat.stream_chat(body.get("message"), body.get("session_id")))
            return
        try:
            payload = json.loads(raw)
            if self.path == "/api/run":
                reply = handle_run(payload)
            elif self.path == "/api/route":
                reply = ra_web.handle_route(payload)
            elif self.path == "/api/key":
                set_api_key(payload.get("key"))
                reply = {"ok": True, **ra_web._status_payload()}
            else:
                self._send(404, b"not found", "text/plain")
                return
        except Exception as exc:  # never 500 with a stack trace to the browser
            reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        self._send_json(reply)


def run(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    server = ThreadingHTTPServer((host, port), _Handler)
    url = f"http://{host}:{port}/"
    n = len(default_registry().plugins)
    print(f"super-menu dashboard → {url}  ({n} plugins · route planner at {url}route · Ctrl+C to stop)")
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
