"""Unit tests for the Claude Code chat harness (web/chat.py).

These exercise the pure pieces — argv assembly, MCP config shape, stream-json →
front-end-event translation, and drawing the map from the route tool's *input*
(re-run in-process, since the full-geometry result is too large to survive
Claude's context) — so they run offline with no ``claude`` CLI and no network.
"""
import os

os.environ.setdefault("SUPER_MENU_OFFLINE", "1")
os.environ.pop("VALHALLA_URL", None)
os.environ.pop("ORS_API_KEY", None)

from super_menu.web import chat


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


def test_route_tool_inputs_extracts_params():
    # The map is drawn from the tool INPUT, not the (oversized, offloaded) result.
    obj = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "mcp__super-menu__route_avoider__route",
         "input": {"origin": "53.48,-2.24", "destination": "53.80,-1.55"}}]}}
    inputs = chat.route_tool_inputs(obj)
    assert inputs == [{"origin": "53.48,-2.24", "destination": "53.80,-1.55"}]
    # non-route tool calls and non-assistant events yield nothing
    assert chat.route_tool_inputs({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]}}) == []
    assert chat.route_tool_inputs({"type": "user", "message": {"content": []}}) == []


def test_run_route_executes_in_process():
    # Offline estimator (VALHALLA_URL/ORS cleared above) returns a GeoJSON route.
    ran = chat.run_route({"origin": "53.48,-2.24", "destination": "53.80,-1.55"})
    assert ran is not None
    geojson, summary = ran
    assert geojson["type"] == "FeatureCollection"
    assert isinstance(summary, str)


def test_run_route_bad_params_returns_none():
    # A malformed avoid spec must be swallowed, not raised, so the stream survives.
    assert chat.run_route({"origin": "x", "destination": "y"}) is None


def test_translate_unknown_event_is_empty():
    assert chat.translate_event({"type": "system", "subtype": "init"}) == []
    # tool results are no longer parsed for geometry — that path is gone
    assert chat.translate_event({"type": "user", "message": {"content": [
        {"type": "tool_result", "content": "anything"}]}}) == []


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
    test_route_tool_inputs_extracts_params()
    test_run_route_executes_in_process()
    test_run_route_bad_params_returns_none()
    test_translate_unknown_event_is_empty()
    test_child_env_strips_api_credentials()
    test_auth_error_becomes_setup_hint()
    test_auth_error_kept_when_token_configured()
    test_non_auth_error_passes_through()
    print("all chat tests passed")
