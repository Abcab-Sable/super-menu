"""End-to-end smoke tests: discovery, parsing, CLI dispatch, and TUI boot."""
import asyncio

from super_menu.core.registry import Registry, default_registry
from super_menu.plugins.free_for_dev import fetch


def test_plugin_discovered():
    reg = default_registry()
    assert reg.get("free-for-dev") is not None
    assert reg.get("git") is not None


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
    test_tui_boots()
    print("all smoke tests passed")
