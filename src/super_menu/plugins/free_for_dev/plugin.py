"""free-for-dev plugin: a searchable local index of free developer resources.

Doubles as a knowledge source for Claude Code when designing app architectures —
query it via ``super-menu free-for-dev search "postgres"`` (add ``--json``) or the
matching MCP tool.

Search is ranked and synonym-aware (see ``search.py``); a per-user annotation
overlay (see ``annotations.py``) lets tags reshape ranking. ``suggest-alternatives``
and ``analyze-architecture`` are deliberately-dumb lexical helpers — Claude does
the reasoning on top of their output.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from super_menu.core.plugin import Plugin, Command, Param, CommandResult
from . import fetch, search, annotations

# Entry names that are also common English words or too generic to be a reliable
# technology mention in a free-text document. Whole-word matches against these
# are ignored by ``analyze-architecture``. Expect to grow this from real docs.
_IGNORE_NAMES = {
    "files", "notes", "free", "tools", "docs", "apps", "api", "apis", "cloud",
    "data", "email", "mail", "host", "hosting", "text", "images", "web", "site",
    "app", "code", "test", "tests", "build", "deploy", "static", "search",
    # Entry names that are also everyday English verbs/nouns — a bare mention in
    # a doc ("expose the port", "monitor usage") is prose, not a tech choice.
    "expose", "monitor", "deliver", "connect", "sync", "share", "forward",
}

# Abbreviations that unambiguously stand for a technology when they appear in a
# document, so ``analyze-architecture`` may resolve them to their best entry.
# Deliberately excludes the generic query-expansion synonym keys (auth, email,
# queue, metrics, logs, sso, cdn, …): those are ordinary prose and resolving
# them would fabricate a "detected technology" from any architecture doc.
_ABBREV_SYNONYMS = {"k8s", "postgres", "postgresql", "psql", "cicd"}

_SEARCH_COLUMNS = ["name", "category", "url", "description", "annotation"]


def _entries() -> list[dict]:
    idx = fetch.load_index()
    return idx["entries"] if idx else []


def _row(entry: dict, anns: dict[str, dict]) -> dict:
    """Search/suggest row: the entry fields plus its annotation label."""
    return {
        "name": entry["name"],
        "category": entry["category"],
        "url": entry["url"],
        "description": entry["description"],
        "annotation": annotations.label(entry["name"], anns),
    }


def _rank(entries: list[dict], query: str, category: str | None,
          anns: dict[str, dict]) -> list[tuple[float, dict]]:
    """Search then apply annotation score nudges and re-sort."""
    scored = search.search(entries, query, category, limit=0)
    adjusted = [
        (s + annotations.score_adjust(e["name"], anns), e) for s, e in scored
    ]
    adjusted.sort(key=lambda pair: (-pair[0], pair[1]["name"].lower()))
    return adjusted


def cmd_search(query: str, category: str | None = None, limit: int = 20) -> CommandResult:
    entries = _entries()
    if not entries:
        return CommandResult.err("index is empty — run the 'update' command first")
    anns = annotations.load()
    ranked = _rank(entries, query, category, anns)
    total = len(ranked)
    shown = ranked[: max(1, limit)]
    rows = [_row(e, anns) for _, e in shown]
    return CommandResult.ok_(
        data=rows,
        summary=f"{total} match(es) for '{query}'"
        + (f" in '{category}'" if category else "")
        + (f" (showing {len(rows)})" if total > len(rows) else ""),
        kind="table",
        columns=_SEARCH_COLUMNS,
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


def _is_excluded(name: str, exclude: str, exclude_tokens: set[str],
                 substring: bool) -> bool:
    """Whether ``name`` is the named technology itself and should be dropped.

    On the name-anchored path substring exclusion is right ("PostgreSQL" drops
    "PostgreSQL Hosting Co"). On an explicit ``category`` override it over-
    excludes — a generic anchor like "auth" would wipe out FusionAuth, Auth0,
    Authentik — so there we require exact equality or a whole-token match.
    """
    if name == exclude:
        return True
    if substring:
        return bool(exclude) and exclude in name
    return bool(exclude_tokens & set(search.tokenize(name)))


def _rank_candidates(entries: list[dict], in_category, exclude: str,
                     criteria: str | None, anns: dict[str, dict],
                     limit: int, exclude_substring: bool = True) -> list[tuple[float, dict]]:
    """Rank entries within an anchor category by optional ``criteria``.

    ``in_category`` is a predicate selecting the candidate pool; ``exclude`` is
    the named technology itself (dropped — see :func:`_is_excluded`). Criteria
    tokens are scored against each candidate (name/category/description);
    annotation tags nudge the result. With no criteria, ordering is
    annotation-then-name.
    """
    crit_tokens = search.tokenize(criteria) if criteria else []
    crit_expanded = search.expand(crit_tokens) if crit_tokens else set()
    ex = exclude.lower().strip()
    ex_tokens = set(search.tokenize(ex))

    cands: list[tuple[float, dict]] = []
    for e in entries:
        if not in_category(e):
            continue
        nm = e["name"].lower()
        if ex and _is_excluded(nm, ex, ex_tokens, exclude_substring):
            continue
        base = search.score(e, crit_tokens, crit_expanded) if crit_tokens else 0.0
        base += annotations.score_adjust(e["name"], anns)
        cands.append((base, e))

    cands.sort(key=lambda pair: (-pair[0], pair[1]["name"].lower()))
    return cands[:limit] if limit and limit > 0 else cands


def _anchor_by_name(entries: list[dict], technology: str) -> dict | None:
    """Best entry whose *name* actually matches ``technology`` (else None).

    Ranks name matches with the search scorer but keeps only candidates that
    share a token with, or contain, the query in their name — so "Oracle
    Exadata" (indexed nowhere by name) returns None even though its tokens hit
    some descriptions.
    """
    tokens = search.tokenize(technology)
    if not tokens:
        return None
    for _, e in search.search(entries, technology, limit=10):
        name = e["name"].lower()
        name_tokens = set(search.tokenize(name))
        if any(t in name_tokens or t in name for t in tokens):
            return e
    return None


def cmd_suggest_alternatives(technology: str, criteria: str | None = None,
                             category: str | None = None,
                             limit: int = 10) -> CommandResult:
    entries = _entries()
    if not entries:
        return CommandResult.err("index is empty — run the 'update' command first")
    anns = annotations.load()

    if category:
        cl = category.lower()
        in_category = lambda e: cl in e["category"].lower()  # noqa: E731
        anchor = category
        # A generic override anchor ("auth") must not substring-exclude every
        # provider containing it — require exact/token exclusion instead.
        exclude_substring = False
    else:
        # Anchor only on a genuine *name* match — a mere description hit (of
        # which the 1.5k-entry index always has some) is not enough to trust the
        # category guess. No name match ⇒ tell the user to pass an override.
        anchored = _anchor_by_name(entries, technology)
        if anchored is None:
            return CommandResult.err(
                f"'{technology}' not found by name in the index — pass "
                "category=<name> to anchor the search manually"
            )
        anchor = anchored["category"]
        in_category = lambda e: e["category"] == anchor  # noqa: E731
        exclude_substring = True

    ranked = _rank_candidates(entries, in_category, technology, criteria, anns, limit,
                              exclude_substring=exclude_substring)
    rows = [{**_row(e, anns), "score": round(s, 2)} for s, e in ranked]
    return CommandResult.ok_(
        data=rows,
        summary=f"{len(rows)} alternative(s) to '{technology}' in category '{anchor}'"
        + (f" ranked by '{criteria}'" if criteria else ""),
        kind="table",
        columns=["name", "category", "score", "url", "description", "annotation"],
    )


def _detect_mentions(text: str, entries: list[dict]) -> list[dict]:
    """Entries whose name (or a synonym key) appears as a whole word in ``text``.

    Case-insensitive; names shorter than 3 chars or on ``_IGNORE_NAMES`` are
    skipped. Deduped by entry name, first occurrence wins.
    """
    low = text.lower()
    found: dict[str, dict] = {}

    for e in entries:
        key = e["name"].lower()
        if len(key) < 3 or key in _IGNORE_NAMES:
            continue
        if re.search(rf"\b{re.escape(key)}\b", low):
            found.setdefault(e["name"], e)

    # Curated abbreviations (k8s, postgres, …): if one appears, surface its best
    # entry so abbreviations in a doc still resolve to a real catalog technology.
    # Restricted to _ABBREV_SYNONYMS so generic prose ("email", "metrics") never
    # fabricates a detected technology.
    for syn in _ABBREV_SYNONYMS:
        if len(syn) < 3 or syn in _IGNORE_NAMES:
            continue
        if re.search(rf"\b{re.escape(syn)}\b", low):
            hits = search.search(entries, syn, limit=1)
            if hits:
                e = hits[0][1]
                found.setdefault(e["name"], e)

    return list(found.values())


def cmd_analyze_architecture(path: str, limit: int = 5) -> CommandResult:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        return CommandResult.err(f"cannot read '{path}': {exc}")

    entries = _entries()
    if not entries:
        return CommandResult.err("index is empty — run the 'update' command first")
    anns = annotations.load()

    mentions = _detect_mentions(text, entries)
    rows: list[dict] = []
    for entry in mentions:
        anchor = entry["category"]
        ranked = _rank_candidates(
            entries, lambda e, a=anchor: e["category"] == a,
            entry["name"], None, anns, limit,
        )
        alts = [{**_row(e, anns), "score": round(s, 2)} for s, e in ranked]
        rows.append({
            "technology": entry["name"],
            "category": anchor,
            "alternatives": ", ".join(a["name"] for a in alts) or "(none in category)",
            # Full structure for --json / MCP consumers; the table shows the join.
            "alternatives_detail": alts,
        })

    rows.sort(key=lambda r: r["technology"].lower())
    return CommandResult.ok_(
        data=rows,
        summary=f"{len(rows)} known technolog(y/ies) found in '{path}'",
        kind="table",
        columns=["technology", "category", "alternatives"],
    )


def _resolve_name(entries: list[dict], name: str) -> tuple[str | None, list[str]]:
    """Map a user-typed name to the canonical index name.

    Returns ``(canonical, [])`` on a unique case-insensitive match, else
    ``(None, close_matches)`` where close matches are substring suggestions.
    Guards against typo/case mismatches that would otherwise create dead
    annotations the search side never looks up.
    """
    nl = name.strip().lower()
    exact = [e["name"] for e in entries if e["name"].lower() == nl]
    if exact:
        return exact[0], []
    close = [e["name"] for e in entries if nl and nl in e["name"].lower()][:5]
    return None, close


def cmd_annotate(name: str, tag: str | None = None,
                 note: str | None = None) -> CommandResult:
    if tag and tag not in annotations.TAGS:
        return CommandResult.err(
            f"unknown tag '{tag}' — choose one of {', '.join(annotations.TAGS)}"
        )
    clearing = not (tag or "").strip() and not (note or "").strip()

    # Setting an annotation must land on a real entry name, or the tag/nudge is
    # invisible to search. Clearing bypasses resolution so stale annotations on
    # renamed/orphaned entries stay deletable.
    if not clearing:
        canonical, close = _resolve_name(_entries(), name)
        if canonical is None:
            hint = f" — did you mean: {', '.join(close)}?" if close else ""
            return CommandResult.err(f"no entry named '{name}'{hint}")
        name = canonical

    result = annotations.upsert(name, tag, note)
    action = result["action"]
    if action == "deleted":
        summary = f"cleared annotation for '{name}'"
    elif action == "noop":
        summary = f"no annotation to clear for '{name}'"
    else:
        summary = f"annotated '{name}'" + (f" as {result['tag']}" if result.get("tag") else "")
    return CommandResult.ok_(data=result, summary=summary, kind="json")


def cmd_annotations() -> CommandResult:
    anns = annotations.load()
    rows = [
        {
            "name": n,
            "tag": a.get("tag", ""),
            "note": a.get("note", ""),
            "updated_at": a.get("updated_at", ""),
        }
        for n, a in sorted(anns.items())
    ]
    return CommandResult.ok_(
        data=rows,
        summary=f"{len(rows)} annotation(s)" if rows else "no annotations yet",
        kind="table",
        columns=["name", "tag", "note", "updated_at"],
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
                help="Ranked search by name, description, or category (synonym-aware).",
                handler=cmd_search,
                params=[
                    Param("query", required=True, help="Text to search for."),
                    Param("category", help="Restrict to a category (substring match)."),
                    Param("limit", type="int", default=20, help="Max results."),
                ],
            ),
            Command(
                name="suggest-alternatives",
                help="Alternatives to a named service, from its catalog category.",
                handler=cmd_suggest_alternatives,
                params=[
                    Param("technology", required=True,
                          help="Named service, e.g. 'Auth0'."),
                    Param("criteria",
                          help="Optional constraints, e.g. 'open-source, EU data'."),
                    Param("category",
                          help="Anchor category override if the service isn't indexed."),
                    Param("limit", type="int", default=10, help="Max alternatives."),
                ],
            ),
            Command(
                name="analyze-architecture",
                help="Scan a doc for known technologies and suggest alternatives.",
                handler=cmd_analyze_architecture,
                params=[
                    Param("path", required=True,
                          help="Markdown/YAML/text doc to scan."),
                    Param("limit", type="int", default=5,
                          help="Max alternatives per technology."),
                ],
            ),
            Command(
                name="annotate",
                help="Tag an entry (star/avoid/using/note); empty tag+note clears it.",
                handler=cmd_annotate,
                params=[
                    Param("name", required=True, help="Exact entry name to annotate."),
                    Param("tag", choices=annotations.TAGS,
                          help="star | avoid | using | note."),
                    Param("note", help="Free-text note."),
                ],
            ),
            Command(
                name="annotations",
                help="List all your entry annotations.",
                handler=cmd_annotations,
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
