"""Claude Code as a fourth driver of super-menu commands.

The dashboard's chat pane (on ``/route``) posts a message to ``/api/chat``; this
module spawns the **``claude`` CLI in headless mode** (``claude -p``) and streams
its ``stream-json`` output back as front-end events. Claude authenticates with the
user's existing Claude subscription — no ``ANTHROPIC_API_KEY``, no per-token
billing — and reaches super-menu's own plugins through the MCP server we hand it
(``super-menu mcp``). So a natural-language request becomes a real
``route-avoider__route`` call whose ``kind="geojson"`` result the ``/route`` map
already knows how to draw.

The pieces are deliberately split so the fiddly bit — turning one decoded
``stream-json`` object into front-end events (:func:`translate_event`) — is pure
and unit-testable without a subprocess or a live Claude. Only :func:`stream_chat`
touches the process boundary.

This is a single-user, local harness: one person driving their own subscription
on their own machine. It is not a multi-user hosted service.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Iterator

# Strip ambient API-key / endpoint overrides from the child so ``claude`` uses the
# user's Claude subscription (OAuth) rather than per-token API billing or a parent
# process's proxy. ``ANTHROPIC_BASE_URL`` is included because an inherited proxy URL
# (e.g. from a Claude Code SDK/desktop host) sends the request somewhere the child's
# subscription token isn't valid → 401. Subscription auth comes from a long-lived
# token in ``CLAUDE_CODE_OAUTH_TOKEN`` (run ``claude setup-token`` once), which is
# NOT stripped and so passes through to the child.
_STRIP_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")

# Shown when Claude fails to authenticate and no headless token is configured.
_AUTH_HINT = (
    "Claude couldn't authenticate. To drive Claude Code on your subscription, run "
    "`claude setup-token` once, then set CLAUDE_CODE_OAUTH_TOKEN (in your shell or "
    "super-menu's .env) before starting `super-menu web`."
)


def _debug_log(text: str) -> None:
    """Append a line to a debug dump when SUPER_MENU_CHAT_DEBUG is set — used to
    inspect the exact stream-json shapes Claude Code emits. Truncated so a full
    road-trace tool result stays readable while still showing its structure."""
    if not os.environ.get("SUPER_MENU_CHAT_DEBUG"):
        return
    try:
        path = Path(tempfile.gettempdir()) / "super-menu-chat-debug.jsonl"
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text[:4000] + "\n")
    except OSError:
        pass


def _looks_like_auth_error(message: str) -> bool:
    m = (message or "").lower()
    return "401" in m or "authenticate" in m or "authentication" in m


def _maybe_auth_hint(event: dict) -> dict:
    """Rewrite a raw auth failure into an actionable setup hint (unless a headless
    token is already configured, in which case surface the real error)."""
    if (event.get("type") == "error"
            and _looks_like_auth_error(event.get("message", ""))
            and not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")):
        return {"type": "error", "message": _AUTH_HINT}
    return event

# Allowlist the whole super-menu MCP server so a headless run never blocks on an
# interactive permission prompt (there is no UI to answer one). Safe: every
# plugin command exposed today is read-only.
_ALLOWED_TOOLS = "mcp__super-menu"

_SYSTEM_PROMPT = (
    "You are embedded in super-menu's route planner, looking at a live map with the "
    "user. Plan routes by calling the route tool (mcp__super-menu__route_avoider__route). "
    "Pass `origin` and `destination` as 'lat,lng' coordinate strings — use your own "
    "geographic knowledge of the places, because the routing engine does NOT geocode "
    "names. Put each area to avoid in the `avoid` parameter as 'lat,lng,radius_km', "
    "joining several with ';'. Call the tool once with your best coordinates. The map "
    "renders the result automatically, so do NOT read files, run shell commands, or "
    "otherwise inspect the tool's raw output. Then reply in 1-2 sentences: confirm the "
    "route and note any trade-off (extra time/distance)."
)


def _child_env() -> dict:
    """The subprocess environment with API/endpoint overrides removed, so
    ``claude`` falls back to the user's subscription credentials."""
    return {k: v for k, v in os.environ.items() if k not in _STRIP_ENV}


def claude_available() -> bool:
    """True when the ``claude`` CLI is on PATH — the pane is gated on this so the
    keyless default surface is untouched when Claude Code isn't installed."""
    return shutil.which("claude") is not None


def _mcp_server_cmd() -> tuple[str, list[str]]:
    """How to launch ``super-menu mcp`` such that it has the optional ``mcp`` dep.

    The stdio MCP server needs the ``mcp`` extra. If *this* interpreter already has
    it, run it directly (fast, no PATH/uv assumptions). Otherwise the web server was
    likely started with a plain ``uv run super-menu web`` whose env omits the extra —
    so self-provision it with ``uv run --extra mcp`` rather than crashing on startup
    (which is what leaves Claude reporting "MCP server still connecting")."""
    if importlib.util.find_spec("mcp") is not None:
        return sys.executable, ["-m", "super_menu.cli", "mcp"]
    uv = shutil.which("uv")
    if uv is not None:
        return uv, ["run", "--extra", "mcp", "python", "-m", "super_menu.cli", "mcp"]
    return sys.executable, ["-m", "super_menu.cli", "mcp"]  # will emit a clear install error


def mcp_config() -> dict:
    """The ``--mcp-config`` payload pointing Claude at super-menu's own MCP server."""
    command, args = _mcp_server_cmd()
    return {"mcpServers": {"super-menu": {"command": command, "args": args}}}


def build_command(message: str, session_id: str, config_path: str) -> list[str]:
    """Assemble the headless ``claude`` argv. Pure — unit-tested for flags.

    ``session_id`` is always a concrete id: a brand-new conversation passes it via
    ``--session-id`` (mints the session); a continuation passes ``--resume`` (the
    customise loop). The caller decides which by tracking whether it just minted
    the id.
    """
    return [
        "claude", "-p", message,
        "--output-format", "stream-json",
        "--verbose",                       # required with -p + stream-json
        "--include-partial-messages",
        "--mcp-config", config_path,
        "--strict-mcp-config",             # ignore any ambient MCP config
        "--allowedTools", _ALLOWED_TOOLS,
        "--permission-mode", "default",
        "--append-system-prompt", _SYSTEM_PROMPT,
    ]


def _blocks(obj: dict) -> list[dict]:
    """The content blocks of an assistant/user message event, robust to shape."""
    msg = obj.get("message")
    if isinstance(msg, dict):
        content = msg.get("content")
    else:
        content = obj.get("content")
    return content if isinstance(content, list) else []


def route_tool_inputs(obj: dict) -> list[dict]:
    """The ``input`` params of every ``route_avoider__route`` tool call in an
    assistant message.

    We render the map from the tool *input* (which is small and always present in
    the stream), not the tool *result*: a full road-trace GeoJSON is tens of
    kilobytes and Claude Code offloads results over its token cap to a file, so the
    geometry never comes back inline. Re-running the route from these params in the
    web server is what actually draws the map. See :func:`run_route`.
    """
    if obj.get("type") != "assistant":
        return []
    inputs = []
    for block in _blocks(obj):
        if (isinstance(block, dict) and block.get("type") == "tool_use"
                and str(block.get("name", "")).endswith("route_avoider__route")):
            inp = block.get("input")
            if isinstance(inp, dict):
                inputs.append(inp)
    return inputs


def run_route(params: dict) -> tuple[dict, str] | None:
    """Execute the route-avoider ``route`` command in-process and return its
    ``(geojson, summary)`` — or ``None`` if it isn't available or the call fails
    (e.g. Claude passed place names instead of coordinates, which we simply skip)."""
    try:
        from super_menu.core.registry import default_registry
        plugin = default_registry().get("route-avoider")
        command = plugin.command("route") if plugin else None
        if command is None:
            return None
        result = command.run(params)
        if result.ok and result.kind == "geojson" and result.data:
            return result.data, result.summary
    except Exception:  # a bad tool input must never break the chat stream
        return None
    return None


def translate_event(obj: dict) -> list[dict]:
    """Map one decoded ``stream-json`` object to zero+ front-end events.

    Pure and total: unknown/irrelevant events yield ``[]``. Front-end event types
    here: ``text`` (assistant prose), ``tool`` (a tool call started), ``done``,
    ``error``. ``route`` events are emitted separately by :func:`stream_chat` from
    the tool input (see :func:`route_tool_inputs`), not from the model output.
    """
    etype = obj.get("type")
    events: list[dict] = []

    if etype == "assistant":
        for block in _blocks(obj):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and block.get("text"):
                events.append({"type": "text", "text": block["text"]})
            elif block.get("type") == "tool_use":
                events.append({"type": "tool", "name": block.get("name", "")})

    elif etype == "result":
        if obj.get("is_error") or obj.get("subtype") not in (None, "success"):
            events.append({"type": "error",
                           "message": obj.get("result") or "Claude reported an error"})
        events.append({"type": "done"})

    return events


def stream_chat(message: str, session_id: str | None) -> Iterator[dict]:
    """Drive a headless ``claude`` turn, yielding front-end events.

    Yields a ``session`` event first carrying the id the browser must echo back on
    the next turn (minted here when absent = the start of a conversation). Any
    failure — missing CLI, non-zero exit, undecodable line — surfaces as an
    ``error`` event rather than raising, so the SSE stream always closes cleanly.
    """
    message = (message or "").strip()
    if not message:
        yield {"type": "error", "message": "empty message"}
        yield {"type": "done"}
        return
    if not claude_available():
        yield {"type": "error", "message": "the 'claude' CLI is not installed"}
        yield {"type": "done"}
        return

    resuming = bool(session_id)
    session_id = session_id or str(uuid.uuid4())
    yield {"type": "session", "session_id": session_id}

    cfg = Path(tempfile.mkdtemp(prefix="super-menu-mcp-")) / "mcp.json"
    cfg.write_text(json.dumps(mcp_config()), encoding="utf-8")
    cmd = build_command(message, session_id, str(cfg))
    cmd += ["--resume", session_id] if resuming else ["--session-id", session_id]

    proc: subprocess.Popen | None = None
    try:
        proc = subprocess.Popen(
            # Fold stderr into stdout and drain the single pipe in the loop below.
            # A separate stderr=PIPE read only *after* the loop can deadlock: if the
            # child fills the ~64 KB stderr buffer before exiting, it blocks writing
            # stderr while we block reading stdout that never comes.
            cmd, stdin=subprocess.DEVNULL,  # headless: no stdin, don't stall waiting for it
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=_child_env(),
            text=True, encoding="utf-8", errors="replace",
        )
        assert proc.stdout is not None
        emitted_error = False
        last_noise = ""  # tail of non-JSON output (now incl. stderr) for exit errors
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            _debug_log("RAW " + line)
            try:
                obj = json.loads(line)
            except ValueError:
                last_noise = line  # non-JSON chatter, e.g. an error line on stderr
                continue
            for event in translate_event(obj):
                event = _maybe_auth_hint(event)
                emitted_error = emitted_error or event.get("type") == "error"
                _debug_log("EVT " + event.get("type", "?"))
                yield event
            # Draw the map from the tool *input* by re-running the route here — the
            # tool result is too large to survive Claude's context (see run_route).
            for params in route_tool_inputs(obj):
                ran = run_route(params)
                if ran is not None:
                    geojson, summary = ran
                    _debug_log("ROUTE re-run ok: " + summary)
                    yield {"type": "route", "geojson": geojson, "summary": summary}
        code = proc.wait()
        # Only surface a generic non-zero-exit error if the stream didn't already
        # report a specific one (e.g. the auth failure), to avoid overwriting it.
        if code != 0 and not emitted_error:
            yield {"type": "error",
                   "message": last_noise or f"claude exited {code}"}
            yield {"type": "done"}
    except Exception as exc:  # never leak a stack trace into the SSE stream
        yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
        yield {"type": "done"}
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
        try:
            cfg.unlink(missing_ok=True)
            cfg.parent.rmdir()
        except OSError:
            pass
