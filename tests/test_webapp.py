"""Tests for the generic web dashboard surface (web/server.py) — no socket."""
import os

os.environ.setdefault("SUPER_MENU_OFFLINE", "1")

from super_menu.web.server import handle_run, menu_payload


def test_menu_payload_lists_every_plugin_and_param_metadata():
    menu = menu_payload()
    ids = {p["id"] for p in menu["plugins"]}
    assert {"free-for-dev", "git", "route-avoider"} <= ids

    ra = next(p for p in menu["plugins"] if p["id"] == "route-avoider")
    assert ra["icon"] and ra["description"]
    route = next(c for c in ra["commands"] if c["name"] == "route")
    origin = next(p for p in route["params"] if p["name"] == "origin")
    assert origin["required"] is True
    profile = next(p for p in route["params"] if p["name"] == "profile")
    assert profile["choices"] and profile["default"] == "driving-car"


def test_handle_run_executes_a_command():
    res = handle_run({"plugin": "route-avoider", "command": "config", "params": {}})
    assert res["ok"] is True
    assert res["kind"] == "json"
    assert "engine" in res["data"]


def test_handle_run_rejects_unknowns_and_missing_params():
    assert handle_run({"plugin": "nope", "command": "x"})["ok"] is False
    assert handle_run({"plugin": "route-avoider", "command": "nope"})["ok"] is False
    missing = handle_run({"plugin": "route-avoider", "command": "route", "params": {}})
    assert missing["ok"] is False and "origin" in missing["summary"]
    # a non-dict params payload must not crash the handler
    assert handle_run({"plugin": "route-avoider", "command": "presets",
                       "params": "garbage"})["ok"] is True


if __name__ == "__main__":
    test_menu_payload_lists_every_plugin_and_param_metadata()
    test_handle_run_executes_a_command()
    test_handle_run_rejects_unknowns_and_missing_params()
    print("all webapp tests passed")
