"""free-for-dev plugin: a searchable local index of free developer resources.

Doubles as a knowledge source for Claude Code when designing app architectures —
query it via ``super-menu free-for-dev search "postgres"`` (add ``--json``) or the
matching MCP tool.

Search is ranked and synonym-aware (see ``search.py``); a per-user annotation
overlay (see ``annotations.py``) lets tags reshape ranking. ``suggest-alternatives``
and ``analyze-architecture`` are deliberately-dumb lexical helpers — Claude does
the reasoning on top of their output.

Freshness signals (see ``linkcheck.py`` / ``flags.py``) detect stale data:
``check-links`` probes entry URLs, and ``flag-entry`` records objective staleness
against a closed reason-type vocabulary with confidence derived from that table.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from super_menu.core.plugin import Plugin, Command, Param, CommandResult
from . import fetch, search, annotations, linkcheck, flags

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


def _row(entry: dict, anns: dict[str, dict], flagged: set[str] = frozenset()) -> dict:
    """Search/suggest row: the entry fields plus its annotation label.

    ``flagged`` is the set of entry names carrying a pending freshness flag; a
    matching entry gets a ``⚑`` prefixed onto its annotation cell so a stale
    entry is visible at a glance in the same column.
    """
    label = annotations.label(entry["name"], anns)
    if entry["name"] in flagged:
        label = f"⚑ {label}".rstrip()
    return {
        "name": entry["name"],
        "category": entry["category"],
        "url": entry["url"],
        "description": entry["description"],
        "annotation": label,
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
    flagged = flags.pending_entries()
    ranked = _rank(entries, query, category, anns)
    total = len(ranked)
    shown = ranked[: max(1, limit)]
    rows = [_row(e, anns, flagged) for _, e in shown]
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

    flagged = flags.pending_entries()
    ranked = _rank_candidates(entries, in_category, technology, criteria, anns, limit,
                              exclude_substring=exclude_substring)
    rows = [{**_row(e, anns, flagged), "score": round(s, 2)} for s, e in ranked]
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


# --- freshness signals (plan 03): link checking + flags --------------------


def _entry_url(entries: list[dict], name: str) -> str | None:
    return next((e["url"] for e in entries if e["name"] == name), None)


def cmd_check_links(category: str | None = None, limit: int = 100,
                    timeout: float = 10.0, all_results: bool = False,
                    flag_broken: bool = False) -> CommandResult:
    entries = _entries()
    if not entries:
        return CommandResult.err("index is empty — run the 'update' command first")

    pool = entries
    if category:
        cl = category.lower()
        pool = [e for e in entries if cl in e["category"].lower()]
        if not pool:
            return CommandResult.err(f"no category matching '{category}'")

    # Only probe real external links. The index also carries table-of-contents
    # pseudo-entries whose "url" is a '#anchor'; those are not links to check and
    # must never be reported broken or auto-flagged.
    pool = [e for e in pool
            if e.get("url", "").lower().startswith(("http://", "https://"))]
    if not pool:
        return CommandResult.err("no checkable http(s) URLs in scope")

    # Never-checked URLs first (accumulate coverage); then, once everything has
    # been probed at least once, re-check oldest-first so successive runs cycle
    # through the whole catalog by staleness rather than re-probing the same head
    # in index order forever. checked_at is ISO-8601 UTC, so lexical == oldest.
    store = linkcheck.load_checks()
    unchecked = [e for e in pool if e["url"] not in store]
    checked = sorted((e for e in pool if e["url"] in store),
                     key=lambda e: store[e["url"]].get("checked_at", ""))
    batch = (unchecked + checked)[: max(1, limit)]

    results = linkcheck.check_many(
        [(e["name"], e["url"]) for e in batch], timeout=timeout
    )
    linkcheck.merge_checks(results)

    broken = sum(1 for r in results if r["status"] in linkcheck.PROBLEM_STATUSES)
    redirected = sum(1 for r in results if r["status"] == "redirect")
    # A 429 is "throttled/unknown", not confirmed healthy — count it separately
    # so it is neither hidden from the table nor tallied as ok.
    throttled = sum(1 for r in results if r["status"] == "ok_throttled")
    ok = len(results) - broken - redirected - throttled

    flagged_count = 0
    if flag_broken:
        flagged_count = _autoflag_broken(results)

    if all_results:
        shown = results
    else:
        shown = [r for r in results if r["status"] != "ok"]  # everything unhealthy/unknown
    shown.sort(key=lambda r: (linkcheck.status_rank(r["status"]), r["name"].lower()))

    rows = [{"name": r["name"], "url": r["url"], "status": r["status"],
             "code": r["code"] if r["code"] is not None else "",
             "note": r["final_url"] or r["note"]}
            for r in shown]

    summary = (f"checked {len(results)} URL(s): {broken} broken, "
               f"{redirected} redirected, {throttled} throttled, {ok} ok")
    if flag_broken:
        summary += f"; filed {flagged_count} url_404 flag(s)"
    return CommandResult.ok_(
        data=rows, summary=summary, kind="table",
        columns=["name", "url", "status", "code", "note"],
    )


def _autoflag_broken(results: list[dict]) -> int:
    """Auto-file url_404 flags for confirmed-dead links, skipping dupes.

    Only ``not_found`` (HTTP 404/410) qualifies — see
    :data:`linkcheck.DEAD_LINK_STATUSES`. Transient ``unreachable`` and
    live-but-blocked ``access_denied`` (401/403) are excluded, so
    "machine-verified by construction" holds: every url_404 flag is backed by a
    server explicitly reporting the resource gone, not a single flaky or gated
    probe.
    """
    # Load/dedup/save once for the whole batch rather than per broken URL.
    existing = flags.load()
    pending = {(f.get("entry"), f.get("reason_type")) for f in existing
               if f.get("status") == "pending_review"}
    new: list[dict] = []
    for r in results:
        if r["status"] not in linkcheck.DEAD_LINK_STATUSES:
            continue
        key = (r["name"], "url_404")
        if key in pending:
            continue
        reason = f"link check returned HTTP {r['code']}"
        new.append(flags.build(r["name"], "url_404",
                               reason[:flags.MAX_REASON_LEN], "critical", None))
        pending.add(key)  # dedupe within this batch too
    if new:
        flags.save(existing + new)
    return len(new)


def cmd_flag_entry(entry: str, reason_type: str, reason: str,
                   severity: str = "warning",
                   evidence_url: str | None = None) -> CommandResult:
    if reason_type not in flags.REASON_TYPES:
        return CommandResult.err(
            f"unknown reason_type '{reason_type}' — allowed: "
            f"{', '.join(flags.reason_types())}"
        )
    if severity not in flags.SEVERITIES:
        return CommandResult.err(
            f"unknown severity '{severity}' — choose one of {', '.join(flags.SEVERITIES)}"
        )
    reason = (reason or "").strip()
    if not reason:
        return CommandResult.err("reason is required")
    if len(reason) > flags.MAX_REASON_LEN:
        return CommandResult.err(f"reason too long (max {flags.MAX_REASON_LEN} chars)")

    entries = _entries()
    if not entries:
        return CommandResult.err("index is empty — run the 'update' command first")
    canonical, close = _resolve_name(entries, entry)
    if canonical is None:
        hint = f" — did you mean: {', '.join(close)}?" if close else ""
        return CommandResult.err(f"no entry named '{entry}'{hint}")
    entry = canonical

    if flags.evidence_required(reason_type) and not (evidence_url or "").strip():
        return CommandResult.err(
            f"reason_type '{reason_type}' requires an evidence_url"
        )

    # url_404 is the machine-verified tier: it must be backed by a broken
    # linkcheck record. Re-check on demand if we have no record yet, so the flag
    # cannot be filed against a URL that is actually live.
    if reason_type == "url_404":
        verified = _verify_url_404(entries, entry)
        if verified is not None:
            return verified

    if flags.find_pending(entry, reason_type):
        return CommandResult.err(
            f"a pending '{reason_type}' flag already exists for '{entry}' — "
            "dismiss it first or use a different reason_type"
        )

    record = flags.add(entry, reason_type, reason, severity, evidence_url)
    return CommandResult.ok_(
        data=record,
        summary=f"flagged '{entry}' as {reason_type} "
                f"(confidence {record['confidence']}, {record['status']})",
        kind="json",
    )


def _verify_url_404(entries: list[dict], entry: str) -> CommandResult | None:
    """Confirm ``entry``'s URL returns an HTTP 4xx. Returns an err result to
    abort, or ``None`` to let the flag proceed.

    A transient ``unreachable`` does not qualify (see
    :data:`linkcheck.DEAD_LINK_STATUSES`) — url_404 means the server actually
    answered with a client error, not that one probe failed to connect."""
    url = _entry_url(entries, entry)
    if not url:
        return CommandResult.err(f"cannot resolve a URL for '{entry}'")
    store = linkcheck.load_checks()
    record = store.get(url)
    if not record or record.get("status") not in linkcheck.DEAD_LINK_STATUSES:
        # No confirmed-dead record on file — re-check this one URL right now.
        result = linkcheck.check_one(url)
        linkcheck.merge_checks([{"url": url, **result}])
        if result["status"] not in linkcheck.DEAD_LINK_STATUSES:
            return CommandResult.err(
                f"url_404 rejected: '{entry}' is not a confirmed dead link "
                f"(status={result['status']}, code={result['code']}; needs an "
                "HTTP 404/410) — a transient, throttled, or access-denied probe "
                "does not prove the link is gone. Use service_discontinued with "
                "evidence if you know the service is gone"
            )
    return None


def cmd_flags(status: str | None = None) -> CommandResult:
    if status and status not in flags.STATUSES:
        return CommandResult.err(
            f"unknown status '{status}' — choose one of {', '.join(flags.STATUSES)}"
        )
    records = flags.load()
    if status:
        records = [f for f in records if f.get("status") == status]
    records.sort(key=lambda f: f.get("created_at", ""), reverse=True)
    rows = [
        {
            "entry": f.get("entry", ""),
            "reason_type": f.get("reason_type", ""),
            "severity": f.get("severity", ""),
            "confidence": f.get("confidence", ""),
            "status": f.get("status", ""),
            "created_at": f.get("created_at", ""),
        }
        for f in records
    ]
    summary = (f"{len(rows)} flag(s)" + (f" with status '{status}'" if status else "")
               if rows else "no flags" + (f" with status '{status}'" if status else ""))
    return CommandResult.ok_(
        data=rows, summary=summary, kind="table",
        columns=["entry", "reason_type", "severity", "confidence", "status", "created_at"],
    )


def cmd_dismiss_flag(entry: str, reason_type: str) -> CommandResult:
    updated = flags.dismiss(entry, reason_type)
    if updated is None:
        return CommandResult.err(
            f"no pending '{reason_type}' flag for '{entry}' to dismiss"
        )
    return CommandResult.ok_(
        data=updated,
        summary=f"dismissed '{reason_type}' flag for '{entry}'",
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
            Command(
                name="check-links",
                help="Probe entry URLs and report broken/redirected links.",
                handler=cmd_check_links,
                params=[
                    Param("category", help="Limit to a category (substring match)."),
                    Param("limit", type="int", default=100,
                          help="Max URLs to check this run (unchecked first)."),
                    Param("timeout", type="float", default=10.0,
                          help="Per-request timeout in seconds."),
                    Param("all_results", type="bool", default=False,
                          help="Include OK rows, not just problems."),
                    Param("flag_broken", type="bool", default=False,
                          help="Auto-file url_404 flags for broken/unreachable links."),
                ],
            ),
            Command(
                name="flag-entry",
                help="File an objective freshness flag against an entry.",
                handler=cmd_flag_entry,
                params=[
                    Param("entry", required=True, help="Exact entry name to flag."),
                    Param("reason_type", required=True, choices=flags.reason_types(),
                          help="Closed vocabulary: " + ", ".join(flags.reason_types()) + "."),
                    Param("reason", required=True,
                          help="Short factual reason (≤ 200 chars)."),
                    Param("severity", choices=flags.SEVERITIES, default="warning",
                          help="critical | warning | info."),
                    Param("evidence_url",
                          help="Required for all reason_types except url_404 / category_mismatch."),
                ],
            ),
            Command(
                name="flags",
                help="List freshness flags (optionally filter by status).",
                handler=cmd_flags,
                params=[
                    Param("status", choices=flags.STATUSES,
                          help="pending_review | dismissed | actioned."),
                ],
            ),
            Command(
                name="dismiss-flag",
                help="Dismiss a pending flag (kept as an audit record, never deleted).",
                handler=cmd_dismiss_flag,
                params=[
                    Param("entry", required=True, help="Flagged entry name."),
                    Param("reason_type", required=True, choices=flags.reason_types(),
                          help="Which reason_type flag to dismiss."),
                ],
            ),
        ]


PLUGIN = FreeForDevPlugin()
