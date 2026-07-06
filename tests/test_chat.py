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


def test_mcp_config_runs_this_interpreter():
    cfg = chat.mcp_config()
    server = cfg["mcpServers"]["super-menu"]
    assert server["args"] == ["-m", "super_menu.cli", "mcp"]
    assert server["command"]  # sys.executable


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


if __name__ == "__main__":
    test_mcp_config_runs_this_interpreter()
    test_build_command_flags()
    test_translate_assistant_text()
    test_translate_tool_use()
    test_translate_result_done_and_error()
    test_translate_tool_result_geojson()
    test_translate_tool_result_string_content()
    test_translate_non_geojson_tool_result_ignored()
    test_translate_unknown_event_is_empty()
    print("all chat tests passed")
