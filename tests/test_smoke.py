"""End-to-end smoke tests: discovery, parsing, CLI dispatch, and TUI boot."""
import asyncio

from super_menu.core.registry import Registry, default_registry
from super_menu.plugins.free_for_dev import fetch


def test_plugin_discovered():
    reg = default_registry()
    assert reg.get("free-for-dev") is not None


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
    test_command_run_and_result_shape()
    test_search_missing_required_param()
    test_tui_boots()
    print("all smoke tests passed")
