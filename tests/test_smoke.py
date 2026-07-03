"""End-to-end smoke tests: discovery, parsing, CLI dispatch, and TUI boot."""
import asyncio
import os
import tempfile

from super_menu.core.registry import Registry, default_registry
from super_menu.plugins.free_for_dev import fetch
from super_menu.plugins.pentest import recon


def test_plugin_discovered():
    reg = default_registry()
    assert reg.get("free-for-dev") is not None
    assert reg.get("git") is not None
    assert reg.get("pentest") is not None


def test_parse_markdown():
    md = (
        "# Free for dev\n"
        "## Major Cloud Providers\n"
        "- [Acme](https://acme.example) - Free tier with 1 vCPU.\n"
        "### Sub\n"
        "- [Beta](https://beta.example) — does things\n"
    )
    entries = fetch.parse_markdown(md)
    assert len(entries) == 2
    assert entries[0].name == "Acme"
    assert entries[0].category == "Major Cloud Providers"
    assert entries[0].description == "Free tier with 1 vCPU."
    assert entries[1].category == "Sub"
    assert entries[1].description == "does things"


def test_parse_markdown_skips_toc_and_relative_links():
    # The real README opens with a table of contents whose bullets link to
    # in-page ``#anchor`` headings, plus the occasional relative path. Those have
    # no URL scheme and are not real catalog entries. Absolute URLs are kept —
    # including non-http schemes like the WebRTC section's ``stun:`` servers, so
    # the filter must not be "http(s) only".
    md = (
        "# Free for dev\n"
        "## Table of Contents\n"
        "- [Major Cloud Providers](#major-cloud-providers)\n"
        "- [Analytics, Events and Statistics](#analytics-events-and-statistics)\n"
        "## Major Cloud Providers\n"
        "- [Acme](https://acme.example) - Real service.\n"
        "- [Local](/relative/path) - not a service\n"
        "## Tunneling, WebRTC and Other Routers\n"
        "- [Example STUN](stun:stun.example.com:3478) - WebRTC STUN server.\n"
    )
    entries = fetch.parse_markdown(md)
    assert [e.name for e in entries] == ["Acme", "Example STUN"], entries
    # In-page anchors and relative links are excluded...
    assert all(not e.url.startswith(("#", "/")) for e in entries)
    # ...but a legitimate non-http scheme (stun:) is preserved.
    assert any(e.url.startswith("stun:") for e in entries)


def test_command_run_and_result_shape():
    plugin = default_registry().get("free-for-dev")
    cmd = plugin.command("categories")
    result = cmd.run({})
    # Either populated (seed/index present) or a clean error — never a crash.
    assert result.ok or "empty" in result.summary
    assert isinstance(result.to_dict(), dict)


def test_search_missing_required_param():
    plugin = default_registry().get("free-for-dev")
    result = plugin.command("search").run({})
    assert not result.ok
    assert "query" in result.summary


def test_pentest_parse_jsonl():
    # Blank lines, a non-JSON notice line, and a valid object interleaved.
    raw = (
        '{"host": "a.example.com", "source": "crtsh"}\n'
        "\n"
        "[INF] running with 1 source\n"
        '{"host": "b.example.com", "source": "wayback"}\n'
    )
    objs = recon.parse_jsonl(raw)
    rows = recon.rows_subdomains(objs)
    assert [r["host"] for r in rows] == ["a.example.com", "b.example.com"]
    assert rows[0]["source"] == "crtsh"


def test_pentest_scope_matching():
    patterns = ["example.com", "*.corp.example", "10.0.0.0/24", "192.168.1.5"]
    # Apex, subdomain, and wildcard-domain matches.
    assert recon.in_scope("example.com", patterns)
    assert recon.in_scope("https://api.example.com/login", patterns)
    assert recon.in_scope("host.corp.example:8443", patterns)
    # CIDR membership and exact IP.
    assert recon.in_scope("10.0.0.42", patterns)
    assert recon.in_scope("192.168.1.5", patterns)
    # Out of scope: unrelated domain, look-alike suffix, IP outside the CIDR.
    assert not recon.in_scope("evil.com", patterns)
    assert not recon.in_scope("notexample.com", patterns)
    assert not recon.in_scope("10.0.1.1", patterns)


def test_pentest_scope_multi_target():
    # A multi-target string must be split and every host validated; an
    # out-of-scope host riding behind an in-scope suffix must be refused.
    assert recon.split_targets("evil.com, api.example.com") == [
        "evil.com", "api.example.com",
    ]
    prev = os.environ.get("SUPER_MENU_HOME")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["SUPER_MENU_HOME"] = tmp
        try:
            recon.add_scope("example.com")
            # Out-of-scope host smuggled in behind an in-scope suffix: refused.
            try:
                recon.require_scope("evil.com,api.example.com")
            except recon.ToolError as exc:
                assert "evil.com" in str(exc)
            else:  # pragma: no cover
                raise AssertionError("multi-target scope gate is fail-open")
            # All-in-scope multi-target passes without raising.
            recon.require_scope("api.example.com www.example.com")
        finally:
            if prev is None:
                os.environ.pop("SUPER_MENU_HOME", None)
            else:
                os.environ["SUPER_MENU_HOME"] = prev


def test_pentest_rows_ports_nested():
    # naabu builds that nest the port must surface the scalar, not the dict.
    flat = recon.rows_ports([{"host": "h", "ip": "1.2.3.4", "port": 80}])
    nested = recon.rows_ports([{"host": "h", "ip": "1.2.3.4", "port": {"Port": 443}}])
    assert flat[0]["port"] == 80
    assert nested[0]["port"] == 443


def test_pentest_scope_fail_closed():
    # An empty scope must refuse every scan — checked before any binary runs, so
    # this passes whether or not the recon tools are installed. Point the data
    # dir at an isolated temp home so we never touch the user's real scope file.
    plugin = default_registry().get("pentest")
    prev = os.environ.get("SUPER_MENU_HOME")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["SUPER_MENU_HOME"] = tmp
        try:
            result = plugin.command("subdomains").run({"domain": "example.com"})
        finally:
            if prev is None:
                os.environ.pop("SUPER_MENU_HOME", None)
            else:
                os.environ["SUPER_MENU_HOME"] = prev
    assert not result.ok
    assert "scope" in result.summary


def test_tui_boots():
    from super_menu.tui.app import SuperMenuApp

    async def _run():
        app = SuperMenuApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            # the sidebar tree mounted with at least the root + one plugin
            from textual.widgets import Tree
            tree = app.query_one(Tree)
            assert len(tree.root.children) >= 1

    asyncio.run(_run())


if __name__ == "__main__":
    test_plugin_discovered()
    test_parse_markdown()
    test_parse_markdown_skips_toc_and_relative_links()
    test_command_run_and_result_shape()
    test_search_missing_required_param()
    test_pentest_parse_jsonl()
    test_pentest_scope_matching()
    test_pentest_scope_multi_target()
    test_pentest_rows_ports_nested()
    test_pentest_scope_fail_closed()
    test_tui_boots()
    print("all smoke tests passed")
