# Plan 03 — freshness signals (check-links, flags, confidence)

## Goal

Detect stale free-for-dev data with objective signals. `check-links` ships first as a
standalone command (useful day one); the flag store builds on it. Everything here is
**local-only** — upstream contribution is plans 04/05 and this plan must not depend on
it landing.

## File layout

```
src/super_menu/plugins/free_for_dev/
├── linkcheck.py     # NEW: concurrent URL checker
├── flags.py         # NEW: flag store (load/save/validate)
└── plugin.py        # add commands: check-links, flag-entry, flags
```

Runtime data (all under `plugin_data_dir("free-for-dev")`):

```
link_checks.json     # last check result per URL, with checked_at
flags.json           # list of flag records
```

## Step 1 — `check-links`

```
Param("category",  help="Limit to a category (substring match)")
Param("limit", type="int", default=100, help="Max URLs to check this run")
Param("timeout", type="float", default=10.0)
Param("all_results", type="bool", default=False, help="Include OK rows, not just problems")
```

Implementation (`linkcheck.py`):

- `concurrent.futures.ThreadPoolExecutor(max_workers=8)` over entry URLs — stdlib only,
  matching fetch.py's urllib approach. HEAD first; on 405/403 retry once with GET
  (many CDNs reject HEAD). `User-Agent: super-menu/0.1` like fetch.py.
- Politeness: group by hostname, max 2 in-flight per host (a semaphore dict). 1.5k
  URLs across hundreds of hosts finishes in well under a minute at 8 workers.
- Classify: `ok` (2xx), `redirect` (3xx final — record target), `client_error` (4xx),
  `server_error` (5xx), `unreachable` (DNS/timeout/conn refused).
- Persist every outcome to `link_checks.json` keyed by URL:
  `{status, code, final_url, checked_at}` — merge, don't overwrite, so partial runs
  (limit=100) accumulate coverage across invocations.
- Result: kind="table", `name | url | status | code | note`, problems first. Summary:
  "checked 100 URLs: 3 broken, 5 redirected, 92 ok".

Edge cases: URLs with fragments/anchors (strip before request, keep for display);
sites that 200 on everything including garbage paths (out of scope — only entry URLs
are checked); rate-limit 429 → classify as `ok_throttled`, never as broken.

## Step 2 — flag store (`flags.py`)

Record shape (matches FEATURE_IDEAS §3):

```python
{
  "entry": "Auth0",                 # must exist in the index at flag time
  "reason_type": "free_tier_removed",  # closed vocabulary, see below
  "reason": "free tier removed Q3 2023",
  "severity": "critical",           # critical | warning | info
  "evidence_url": "https://...",    # required for every reason_type except url_404
  "flagged_by": "claude-code",
  "created_at": "2026-07-02T...",
  "status": "pending_review",       # pending_review | dismissed | actioned
  "confidence": 0.8,                # derived, not caller-supplied — see below
}
```

`reason_type` is the enforcement point for the CAN/CANNOT-flag rules — a **closed
vocabulary** validated in `flags.py`, not free text:

| reason_type | evidence required | confidence |
|---|---|---|
| `url_404` | no (auto-verified via linkcheck) | 0.95 |
| `service_discontinued` | yes | 0.80 |
| `free_tier_removed` | yes | 0.75 |
| `pricing_changed` | yes | 0.70 |
| `category_mismatch` | no | 0.60 |

Anything else → `err("unknown reason_type; allowed: …")`. This is how "no subjective
flags" is made structural instead of aspirational: there is no reason_type for
"service is bad".

**Confidence is computed from the table, not accepted as a parameter** — a caller
(including Claude) cannot claim 0.95 confidence on an unverified pricing complaint.
`url_404` flags additionally require a matching broken record in `link_checks.json`
(the command re-checks the URL if absent), so the highest-confidence tier is
machine-verified by construction.

## Step 3 — commands

- `flag-entry` — Params: `entry` (required), `reason_type` (required,
  choices=[…vocabulary…]), `reason` (required, free text ≤ 200 chars),
  `severity` (choices), `evidence_url`. Validates entry exists in index, evidence rule,
  dedupes (same entry + reason_type + pending → err pointing at existing flag).
- `flags` — list flags, `Param("status", choices=[...])` filter, kind="table":
  `entry | reason_type | severity | confidence | status | created_at`.
- `dismiss-flag` — `Param("entry", required=True)`, `Param("reason_type", required=True)`;
  sets status=dismissed (audit trail: never delete records).
- Wire-up: `check-links` offers no auto-flagging by default; a
  `Param("flag_broken", type="bool", default=False)` opt-in converts `client_error`/
  `unreachable` results into `url_404` flags in one pass.

Surfaces get all of this free: the TUI renders flag tables, Claude files flags via MCP
tool `free-for-dev__flag-entry`, and the annotations column from plan 02 can show a ⚑
for entries with pending flags (one-line join in `cmd_search`).

## Testing (`tests/test_freshness.py`)

- `linkcheck` classify logic against a fake opener (monkeypatch `urllib.request` —
  no live network in tests): 200, 301→200, 404, timeout, 429.
- Merge semantics: two partial runs accumulate in `link_checks.json` (point
  `SUPER_MENU_HOME` env var at a temp dir — config.py already supports it).
- Flag validation: unknown reason_type rejected; missing evidence rejected;
  duplicate pending flag rejected; confidence comes from the table even if the
  caller passes something.
- `url_404` flag without a broken linkcheck record triggers a re-check.

## Exit criteria

- `super-menu free-for-dev check-links --limit 50` produces a problems-first table on
  the real index; re-running continues past the first 50.
- A flag filed via MCP shows up in the TUI flags table with derived confidence.
- No flag can enter the store that violates the CAN/CANNOT rules.

## Out of scope

Auto-proposing PRs from flags (plan 05), scheduled/background checking (a user can
`/loop` or cron the CLI themselves — no daemon code here), pricing-page crawling
(needs per-site scraping; revisit only if flags prove valuable).
