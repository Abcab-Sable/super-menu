"""Unit tests for the Claude Code chat harness (web/chat.py).

These exercise the pure pieces — argv assembly, MCP config shape, and the
stream-json → front-end-event translation — so they run offline with no
``claude`` CLI and no network. ``translate_event`` is fed a real route-avoider
``kind="geojson"`` result wrapped the way the MCP server delivers it.
"""
import json
import os

os.environ.setdefault("SUPER_MENU_OFFLINE", "1")
os.environ.pop("VALHALLA_URL", None)
os.environ.pop("ORS_API_KEY", None)

from super_menu.web import chat
from super_menu.plugins.route_avoider import plugin as ra


def test_mcp_config_launches_super_menu_mcp():
    # Two valid launch shapes: this interpreter directly, or `uv run --extra mcp`
    # when the current env lacks the optional dep. Both must end up running the
    # super-menu MCP server.
    server = chat.mcp_config()["mcpServers"]["super-menu"]
    assert server["command"]
    assert server["args"][-1] == "mcp"
    assert "super_menu.cli" in server["args"]


def test_build_command_flags():
    cmd = chat.build_command("hi", "sess-1", "/tmp/mcp.json")
    assert cmd[:3] == ["claude", "-p", "hi"]
    # the essential headless + streaming flags are present, in pairs
    for flag, val in [("--output-format", "stream-json"), ("--mcp-config", "/tmp/mcp.json"),
                      ("--allowedTools", "mcp__super-menu")]:
        assert cmd[cmd.index(flag) + 1] == val
    assert "--verbose" in cmd
    assert "--strict-mcp-config" in cmd


def test_translate_assistant_text():
    obj = {"type": "assistant", "message": {"content": [{"type": "text", "text": "hello"}]}}
    assert chat.translate_event(obj) == [{"type": "text", "text": "hello"}]


def test_translate_tool_use():
    obj = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "mcp__super-menu__route_avoider__route", "input": {}}]}}
    assert chat.translate_event(obj) == [
        {"type": "tool", "name": "mcp__super-menu__route_avoider__route"}]


def test_translate_result_done_and_error():
    assert chat.translate_event({"type": "result", "subtype": "success"}) == [{"type": "done"}]
    err = chat.translate_event({"type": "result", "is_error": True, "result": "boom"})
    assert {"type": "error", "message": "boom"} in err
    assert {"type": "done"} in err


def test_translate_tool_result_geojson():
    # A genuine offline route result, wrapped exactly as the MCP server delivers a
    # tool result (json.dumps of CommandResult.to_dict() as the block's text).
    res = ra.cmd_route(origin="Leeds", destination="Aberystwyth", avoid="53.4,-2.9,25,Zone")
    assert res.kind == "geojson"
    payload_text = json.dumps(res.to_dict())
    obj = {"type": "user", "message": {"content": [
        {"type": "tool_result", "content": [{"type": "text", "text": payload_text}]}]}}
    events = chat.translate_event(obj)
    assert len(events) == 1 and events[0]["type"] == "route"
    assert events[0]["geojson"]["type"] == "FeatureCollection"


def test_translate_tool_result_string_content():
    # tool_result content can also be a bare string rather than a list of blocks.
    res = ra.cmd_route(origin="Leeds", destination="York")
    obj = {"type": "user", "message": {"content": [
        {"type": "tool_result", "content": json.dumps(res.to_dict())}]}}
    events = chat.translate_event(obj)
    assert events and events[0]["type"] == "route"


def test_translate_non_geojson_tool_result_ignored():
    obj = {"type": "user", "message": {"content": [
        {"type": "tool_result", "content": json.dumps({"kind": "table", "data": []})}]}}
    assert chat.translate_event(obj) == []


def test_translate_unknown_event_is_empty():
    assert chat.translate_event({"type": "system", "subtype": "init"}) == []


def test_child_env_strips_api_credentials():
    env = chat._child_env()
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        assert k not in env


def _without_oauth_token(fn):
    """Run ``fn`` with CLAUDE_CODE_OAUTH_TOKEN removed, restoring it after."""
    saved = os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    try:
        fn()
    finally:
        if saved is not None:
            os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = saved


def test_auth_error_becomes_setup_hint():
    def check():
        ev = chat._maybe_auth_hint({"type": "error", "message": "API Error: 401 Invalid auth"})
        assert "setup-token" in ev["message"]
    _without_oauth_token(check)


def test_auth_error_kept_when_token_configured():
    saved = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = "sk-ant-oat01-xxx"
    try:
        original = {"type": "error", "message": "API Error: 401 Invalid auth"}
        assert chat._maybe_auth_hint(original) == original
    finally:
        if saved is None:
            os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
        else:
            os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = saved


def test_non_auth_error_passes_through():
    def check():
        original = {"type": "error", "message": "no route found"}
        assert chat._maybe_auth_hint(original) == original
    _without_oauth_token(check)


if __name__ == "__main__":
    test_mcp_config_launches_super_menu_mcp()
    test_build_command_flags()
    test_translate_assistant_text()
    test_translate_tool_use()
    test_translate_result_done_and_error()
    test_translate_tool_result_geojson()
    test_translate_tool_result_string_content()
    test_translate_non_geojson_tool_result_ignored()
    test_translate_unknown_event_is_empty()
    test_child_env_strips_api_credentials()
    test_auth_error_becomes_setup_hint()
    test_auth_error_kept_when_token_configured()
    test_non_auth_error_passes_through()
    print("all chat tests passed")
