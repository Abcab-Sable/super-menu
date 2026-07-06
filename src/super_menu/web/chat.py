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
    "You are embedded in super-menu's route planner. The user is looking at a live "
    "map. To plan or change a route, call the route tool (mcp__super-menu"
    "__route_avoider__route) — the map renders whatever it returns. Do the routing "
    "through the tool, never invent coordinates or distances yourself. Keep replies "
    "short: confirm what you did and note any trade-off (extra time/distance) in a "
    "sentence or two."
)


def _child_env() -> dict:
    """The subprocess environment with API/endpoint overrides removed, so
    ``claude`` falls back to the user's subscription credentials."""
    return {k: v for k, v in os.environ.items() if k not in _STRIP_ENV}


def claude_available() -> bool:
    """True when the ``claude`` CLI is on PATH — the pane is gated on this so the
    keyless default surface is untouched when Claude Code isn't installed."""
    return shutil.which("claude") is not None


def mcp_config() -> dict:
    """The ``--mcp-config`` payload: run *this* interpreter's ``super-menu mcp``.

    Referencing ``sys.executable -m super_menu.cli`` avoids depending on the
    ``super-menu`` console script being on the spawned process's PATH."""
    return {
        "mcpServers": {
            "super-menu": {
                "command": sys.executable,
                "args": ["-m", "super_menu.cli", "mcp"],
            }
        }
    }


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


def _route_from_tool_result(block: dict) -> dict | None:
    """If a ``tool_result`` block carries a ``kind="geojson"`` CommandResult, pull
    out its FeatureCollection; otherwise ``None``.

    The block's content is exactly the ``json.dumps(payload)`` text the MCP server
    returns (see ``mcp_server.call_tool``), so we parse it and read ``kind``/``data``.
    """
    content = block.get("content")
    # tool_result content may be a raw string or a list of {type:text,text:...}.
    if isinstance(content, list):
        content = "".join(
            c.get("text", "") for c in content if isinstance(c, dict))
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        payload = json.loads(content)
    except (ValueError, TypeError):
        return None
    if isinstance(payload, dict) and payload.get("kind") == "geojson":
        return payload
    return None


def translate_event(obj: dict) -> list[dict]:
    """Map one decoded ``stream-json`` object to zero+ front-end events.

    Pure and total: unknown/irrelevant events yield ``[]``. Front-end event types:
    ``text`` (assistant prose), ``tool`` (a tool call started), ``route`` (a
    geojson result to draw), ``done`` (turn finished), ``error``.
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

    elif etype == "user":
        for block in _blocks(obj):
            if isinstance(block, dict) and block.get("type") == "tool_result":
                payload = _route_from_tool_result(block)
                if payload is not None:
                    events.append({
                        "type": "route",
                        "geojson": payload.get("data"),
                        "summary": payload.get("summary", ""),
                    })

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
            cmd, stdin=subprocess.DEVNULL,  # headless: no stdin, don't stall waiting for it
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_child_env(),
            text=True, encoding="utf-8", errors="replace",
        )
        assert proc.stdout is not None
        emitted_error = False
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue  # non-JSON chatter (shouldn't happen with stream-json)
            for event in translate_event(obj):
                event = _maybe_auth_hint(event)
                emitted_error = emitted_error or event.get("type") == "error"
                yield event
        code = proc.wait()
        # Only surface a generic non-zero-exit error if the stream didn't already
        # report a specific one (e.g. the auth failure), to avoid overwriting it.
        if code != 0 and not emitted_error:
            err = (proc.stderr.read() if proc.stderr else "").strip()
            yield {"type": "error",
                   "message": err.splitlines()[-1] if err else f"claude exited {code}"}
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
