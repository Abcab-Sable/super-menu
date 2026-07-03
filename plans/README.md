# Implementation plans

The roadmap in [FEATURE_IDEAS.md](../FEATURE_IDEAS.md) is now fully resolved. The plans
that shipped (git-tools, smart discovery, freshness signals) have been removed — the code
is the source of truth for those, and their history lives in git. Only the two plans that
are *not* settled by code remain here:

| Plan | Feature | Status |
|------|---------|--------|
| [04-upstream-gate](04-upstream-gate.md) | Validate free-for-dev accepts contributions | **Closed — NO-GO** (2026-07-03): upstream bans AI-authored edits (`CONTRIBUTING.md` + agent-directed `AGENTS.md`/`CLAUDE.md`); probe PR intentionally not sent |
| [05-pr-proposals](05-pr-proposals.md) | Proposal review panel, propose-pr, GitHub integration | **Shelved** — gate 04 returned NO-GO; the design is kept for possible revival against a *different* upstream that permits tool-assisted human-reviewed PRs |

Plan 04 is the decision record for why 05 is shelved; plan 05 is the parked design.
Shipped-plan history (01 git-tools closed, 02 smart-discovery, 03 freshness-signals):
see `git log` and the closed PRs (#5, #7).

Lesson recorded from plan 01: rank plans by *who stops using their current tool for
this*, not by how cheap they are to build. Low effort × low value is still low value.

## Constraints (apply to plan 05 if revived)

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
