"""free-for-dev plugin: a searchable local index of free developer resources.

Doubles as a knowledge source for Claude Code when designing app architectures —
query it via ``super-menu free-for-dev search "postgres"`` (add ``--json``) or the
matching MCP tool.
"""
from __future__ import annotations

from collections import Counter

from super_menu.core.plugin import Plugin, Command, Param, CommandResult
from . import fetch


def _entries() -> list[dict]:
    idx = fetch.load_index()
    return idx["entries"] if idx else []


def _matches(entry: dict, query: str) -> bool:
    q = query.lower()
    return (
        q in entry["name"].lower()
        or q in entry["description"].lower()
        or q in entry["category"].lower()
    )


def cmd_search(query: str, category: str | None = None, limit: int = 20) -> CommandResult:
    entries = _entries()
    if not entries:
        return CommandResult.err("index is empty — run the 'update' command first")
    results = [e for e in entries if _matches(e, query)]
    if category:
        cl = category.lower()
        results = [e for e in results if cl in e["category"].lower()]
    total = len(results)
    results = results[: max(1, limit)]
    return CommandResult.ok_(
        data=results,
        summary=f"{total} match(es) for '{query}'"
        + (f" in '{category}'" if category else "")
        + (f" (showing {len(results)})" if total > len(results) else ""),
        kind="table",
        columns=["name", "category", "url", "description"],
    )


def cmd_categories() -> CommandResult:
    entries = _entries()
    if not entries:
        return CommandResult.err("index is empty — run the 'update' command first")
    counts = Counter(e["category"] for e in entries)
    rows = [{"category": c, "count": n} for c, n in counts.most_common()]
    return CommandResult.ok_(
        data=rows,
        summary=f"{len(rows)} categories, {len(entries)} entries total",
        kind="table",
        columns=["category", "count"],
    )


def cmd_category(name: str, limit: int = 50) -> CommandResult:
    entries = _entries()
    if not entries:
        return CommandResult.err("index is empty — run the 'update' command first")
    nl = name.lower()
    rows = [e for e in entries if nl in e["category"].lower()][: max(1, limit)]
    if not rows:
        return CommandResult.err(f"no category matching '{name}'")
    return CommandResult.ok_(
        data=rows,
        summary=f"{len(rows)} entries in categories matching '{name}'",
        kind="table",
        columns=["name", "category", "url", "description"],
    )


def cmd_update() -> CommandResult:
    try:
        meta = fetch.update_index()
    except Exception as exc:  # network/parse failure
        return CommandResult.err(f"update failed: {type(exc).__name__}: {exc}")
    return CommandResult.ok_(
        data=meta,
        summary=f"indexed {meta['count']} entries from free-for-dev",
        kind="json",
    )


class FreeForDevPlugin(Plugin):
    id = "free-for-dev"
    name = "Free for Dev"
    description = "Searchable index of free-tier services & resources for developers."
    icon = "🎁"

    def commands(self) -> list[Command]:
        return [
            Command(
                name="search",
                help="Search entries by name, description, or category.",
                handler=cmd_search,
                params=[
                    Param("query", required=True, help="Text to search for."),
                    Param("category", help="Restrict to a category (substring match)."),
                    Param("limit", type="int", default=20, help="Max results."),
                ],
            ),
            Command(
                name="categories",
                help="List all categories with entry counts.",
                handler=cmd_categories,
            ),
            Command(
                name="category",
                help="List entries within a category.",
                handler=cmd_category,
                params=[
                    Param("name", required=True, help="Category name (substring match)."),
                    Param("limit", type="int", default=50, help="Max results."),
                ],
            ),
            Command(
                name="update",
                help="Re-fetch the free-for-dev README and rebuild the local index.",
                handler=cmd_update,
            ),
        ]


PLUGIN = FreeForDevPlugin()
