# Super-menu Feature Ideas

## Overview

This document captured the roadmap for making super-menu — and specifically the
free-for-dev plugin — more useful to Claude Code. The core vision: **make Claude Code a
first-class citizen** of super-menu, so it can intelligently consume (and, conditionally,
help curate) resource data.

The vision unfolded in two arcs:

1. **Claude as consumer** — give Claude structured, queryable access to repo state and
   resource data so its advice gets better (git-tools, smart search, architecture analysis).
2. **Claude as curator** — let Claude detect stale data and propose evidence-backed fixes
   upstream, with a human gatekeeper at every step (flags, proposals, PRs).

Guiding principle throughout: **build the cheapest version that proves the value, then
invest.** Fancy tech (embeddings, extraction pipelines) is earned by demonstrated need, not
assumed up front.

Most of this roadmap is now executed. The sections below are the **status ledger** (what
shipped / was decided, so nobody re-proposes it) and the **still-live ideas** (deferred work
that has not yet cleared the evidence bar). Detailed build specs for shipped work lived in
`plans/` and have been removed now that the code is the source of truth — see `git log` and
the closed PRs. The one design still parked is plan 05, which lives in
[plans/05-pr-proposals.md](plans/05-pr-proposals.md).

---

## Status ledger (resolved — do not re-propose)

- **git-tools plugin — SHIPPED (tranche 1) & CLOSED.** Core commands (`status`, `log`,
  `branch`, `diff-stats`) merged in [PR #5](https://github.com/Abcab-Sable/super-menu/pull/5).
  Tranche 2 (`tag`, `stash`, `blame`) was **cancelled**: Claude Code runs `git` directly,
  other MCP clients have the official `mcp-server-git`, and humans have the git CLI / lazygit
  / IDE integrations — no user segment prefers this plugin over their current tool. It stays
  in the tree only as the **reference plugin for subprocess-backed sources**. Feature-frozen;
  do not add commands.

- **Smart discovery — SHIPPED** ([PR #7](https://github.com/Abcab-Sable/super-menu/pull/7)):
  keyword/category search, `suggest-alternatives`, `analyze-architecture`, and the
  annotations overlay. Claude itself is the semantic layer over the keyword index.

- **Freshness signals — SHIPPED**: `check-links`, `flag-entry`, `flags`, `dismiss-flag`.
  Flags are valuable *locally* — "this entry is stale" context improves Claude's
  recommendations immediately, independent of any upstream contribution.

- **Upstream contribution (PR proposals) — SHELVED (NO-GO).** The plan-04 gate found that
  free-for-dev explicitly bans AI-authored edits (`CONTRIBUTING.md`, plus agent-directed
  `AGENTS.md`/`CLAUDE.md` that warn of account blocks). Since the whole value of the
  curator arc was *tooling that drafts PRs*, it cannot proceed against this upstream. The
  local flags already deliver the personal value, so nothing is lost by stopping here. The
  parked design (review panel, `propose-pr`, GitHub integration) is preserved in
  [plans/05-pr-proposals.md](plans/05-pr-proposals.md) in case a *different*,
  automation-friendly upstream ever justifies reviving it.

---

## Still-live ideas (deferred — earn with evidence)

These have **no plan** until real usage demonstrates the need. The bar each must clear is
listed so "we should add X" is answered with a benchmark, not optimism.

### Semantic search / embeddings

Only if, after keyword search is in real use, queries **demonstrably miss** relevant
entries. When that happens:

- Prefer a small local model (e.g. `sentence-transformers`) as an **optional extra**
  (`pip install super-menu[semantic]`) — a paid API key as a hard dependency of a
  *free-tools* plugin is off-brand and a setup barrier.
- Cache embeddings under `plugin_data_dir("free-for-dev")`; recompute only on index refresh.
- Ship with a benchmark: ~20 real queries with expected hits, so "embeddings beat keywords"
  is **measured**, not assumed.

### Structured extraction (highest risk)

Extracting structured facts (free-tier limits, data residency, pricing) from free-for-dev's
freeform prose is lossy and goes stale the moment a vendor changes pricing. Treat as an
experiment, not a foundation:

- Start with 2–3 fields that are usually stated explicitly ("open-source", "credit card
  required").
- Every extracted fact carries `extracted_at` + source text, surfaced as "as described in
  free-for-dev on <date>" — never as ground truth.
- Expand the schema only for fields where extraction accuracy holds up.

---

## Principles that outlast the roadmap

- **Rank by who stops using their current tool for this**, not by how cheap it is to build.
  Low effort × low value is still low value (the lesson that cancelled git-tools tranche 2).
- **Build the cheapest version that proves the value; upgrade with evidence, not optimism.**
- **Objective criteria only** for any curation signal (free tier removed, pricing changed,
  URL broken) — never subjective quality judgments.
- **The user stays in control.** Claude proposes; the human approves. No auto-submissions.
- **Local value first.** Every curation feature must be worth building even if nothing is
  ever sent upstream.
