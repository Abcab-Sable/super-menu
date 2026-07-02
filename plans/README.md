# Implementation plans

One plan per phase of the [FEATURE_IDEAS.md](../FEATURE_IDEAS.md) roadmap. Each plan is
self-contained: goal, file layout, command specs against the real plugin contract
(`src/super_menu/core/plugin.py`), data shapes, edge cases, and exit criteria.

| Plan | Feature | Depends on | Status |
|------|---------|------------|--------|
| [01-git-tools](01-git-tools.md) | git-tools plugin | — | **Closed** — tranche 1 shipped ([PR #5](https://github.com/Abcab-Sable/super-menu/pull/5)); tranche 2 cancelled; plugin reframed as the subprocess-backed reference example |
| [02-smart-discovery](02-smart-discovery.md) | Keyword search, suggest-alternatives, analyze-architecture, annotations | — | **Next up** (low risk) |
| [03-freshness-signals](03-freshness-signals.md) | check-links, flag-entry, confidence scoring | 02 (search layer, storage) | Planned (medium risk) |
| [04-upstream-gate](04-upstream-gate.md) | Validate free-for-dev accepts contributions | 03 (check-links findings) | Planned (process, not code) |
| [05-pr-proposals](05-pr-proposals.md) | Proposal review panel, propose-pr, GitHub integration | 03 + gate pass in 04 | Gated (high risk) |

Lesson recorded from plan 01: rank plans by *who stops using their current tool for
this*, not by how cheap they are to build. Low effort × low value is still low value.

Deferred (no plan until evidence demands one): semantic search/embeddings, structured
extraction. See "Deferred" in FEATURE_IDEAS.md for the bar they must clear.

## Constraints that apply to every plan

- **The contract is the contract.** Handlers return `CommandResult` (`ok_`/`err`), `data`
  JSON-serializable, `kind` ∈ table/list/text/json. `Param.type` ∈ str/int/float/bool —
  there is no list type, so multi-value inputs are comma-separated strings.
- **No UI code in plugins.** If a feature can't be expressed as commands + results, that's
  a core change and the plan must say so explicitly (only plan 05 does).
- **Runtime data** goes under `core.config.plugin_data_dir(<id>)`, never the repo.
- **Windows first-class:** `pathlib` for paths, no bare emoji in `print()` paths that
  bypass `_force_utf8`, `encoding="utf-8"` on every file/subprocess read.
- **Tests** extend `tests/` following `test_smoke.py`'s pattern: plain asserts, runnable
  standalone via `uv run python tests/<file>.py`, no network in tests (feed fixtures).
