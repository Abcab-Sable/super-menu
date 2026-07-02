# Plan 05 — PR proposals: review panel, propose-pr, GitHub integration

**Gated on plan 04 returning GO or PARTIAL.** This is the largest and riskiest plan;
it contains the project's first deliberate core-contract change. Build it in the four
stages below — each stage is independently shippable, and PARTIAL stops after stage 3.

## Goal

Flags (plan 03) become structured proposals; the user reviews them in a rich TUI panel
(plain English, evidence, action options); an approved proposal becomes a free-for-dev
PR — drafted by tooling, submitted under the user's control.

## Stage 1 — core contract extension (design first, smallest possible)

The review panel can't be an auto-rendered table, but the plugin must stay UI-free.
Two additions to `core/plugin.py` make it expressible:

**1. Generic follow-up actions on any result** — the TUI renders buttons, CLI prints
hint lines, MCP passes them through as data:

```python
@dataclass
class ResultAction:
    label: str            # "Dismiss", "Approve & Preview"
    command: str          # command name on the same plugin, e.g. "decide-proposal"
    params: dict          # pre-filled params, e.g. {"id": "auth0-1", "choice": "remove"}

# CommandResult gains: actions: list[ResultAction] = field(default_factory=list)
```

This keeps the interaction loop declarative: a button is just "invoke this command
with these params", dispatched through the existing `Command.run` path. Nothing about
it is proposal-specific — any plugin can use it (e.g. git-tools `status` could offer a
"show log" action).

**2. A `review` result kind** — `ResultKind` gains `"review"`; `data` must follow the
Proposal schema below. The TUI gets one new renderer for it; CLI `--json` and MCP
treat it exactly like `json` (already-structured data), so **non-TUI surfaces degrade
with zero code**. `to_dict()` serializes actions too.

Compatibility rules: `actions` defaults empty (existing plugins untouched); surfaces
that don't understand `actions` ignore them; unknown kinds render as json. Update
`.claude/super-menu.md` + CLAUDE.md contract description in the same PR.

## Stage 2 — proposal store + commands (plugin-side, no UI yet)

```
src/super_menu/plugins/free_for_dev/
└── proposals.py     # NEW: Proposal/ActionOption/Evidence dataclasses, store, transitions
```

Dataclasses exactly as sketched in FEATURE_IDEAS §5 (Proposal, ActionOption, Evidence)
with `to_dict()`/`from_dict()`. Storage under `plugin_data_dir("free-for-dev")`:

```
proposals.json       # all proposals with status: draft|decided|dismissed|submitted
drafts/<id>.md       # generated PR body per approved proposal
submitted.log        # append-only audit: timestamp, proposal id, decision, PR url
```

Commands (all ordinary contract commands):

| Command | Params | Result |
|---|---|---|
| `propose` | `flag_entry` req, `flag_reason_type` req + summary/options/evidence fields | builds a Proposal from an existing pending flag; err if flag confidence < 0.7 |
| `proposals` | `status` filter | table: id, entry, title, confidence, status |
| `show-proposal` | `id` req | **kind="review"**, actions = [Dismiss / Request changes / Approve:<option> per ActionOption] |
| `decide-proposal` | `id` req, `choice` req, `note` | transitions state, logs to audit; choice="dismiss" allowed |
| `draft-pr` | `id` req | renders `drafts/<id>.md` from the decided option + evidence; returns the markdown as kind="text" |

Evidence-input reality check: `Param` has no list type, so `propose` accepts
evidence as a JSON string param (`evidence_json`) validated against the Evidence
schema — clunky for humans, natural for the MCP caller (Claude), which is who files
proposals. Humans review; Claude proposes. Confidence is inherited from the flag
(plan 03's derived value), never caller-supplied.

PR body template per action option (remove / deprecate / note), following whatever
house style plan 04 observed. Every draft embeds the evidence list with dates + URLs.

## Stage 3 — TUI review panel

New screen in `src/super_menu/tui/` (e.g. `review.py`, a Textual `Screen`):

- Triggered whenever a command result has `kind == "review"` — the app currently
  routes results to form/table widgets; add one branch.
- Sections top-to-bottom (FEATURE_IDEAS §5): plain-English summary → radio group built
  from `action_options` → evidence list (dated, URLs opened via `webbrowser.open`) →
  why-it-matters → buttons built from `result.actions`.
- Button press = dispatch the pre-filled command through the same code path the
  form screen uses; re-render on the returned result (decide → confirmation text,
  approve → the `draft-pr` preview text).
- Textual note: give every widget an explicit unique `id` — the DuplicateIds crash
  (f98d7b0) came from generated ids; don't repeat it.

**PARTIAL stops here**: draft in hand, user submits manually with `gh` or the browser.

## Stage 4 — GitHub submission (GO only)

Deviation from FEATURE_IDEAS §4, deliberate: **shell out to `gh` instead of managing a
`GITHUB_TOKEN`**. `gh` is already authenticated on this machine, handles fork
workflows, and keeps credentials out of our config surface entirely.

`submit-pr` command — `Param("id", required=True)`, `Param("dry_run", type="bool", default=True)`:

1. Preflight: `gh auth status`; err with setup instructions if absent.
2. `gh repo fork ripienaar/free-for-dev --clone` into a temp workdir (or reuse the
   user's existing fork), branch `super-menu/<proposal-id>`.
3. Apply the edit to README.md programmatically: locate the entry's bullet line with
   the same `_ENTRY_RE` logic fetch.py uses, apply the decided option (delete line /
   move under Deprecated / append note). Err loudly if the line can't be found
   unambiguously — never fuzzy-edit someone else's README.
4. Commit, push, `gh pr create --title ... --body-file drafts/<id>.md`.
5. `dry_run=True` (the default) does 1–3 and shows the diff; only an explicit
   `--dry_run false` pushes. Append to `submitted.log`, set proposal status=submitted.

## Testing (`tests/test_proposals.py`)

- Contract: `to_dict()` round-trips actions; result with unknown-kind renders as json
  in CLI (assert no crash); existing smoke tests still pass untouched.
- Store: full lifecycle draft→decided→submitted transitions; illegal transitions err;
  audit log appends.
- Drafting: golden-file tests — decided proposal fixture in, expected markdown out,
  one per action option.
- README edit: fixture README, apply each option, assert exact diff; ambiguous-match
  fixture errs.
- TUI: extend the `run_test()` pattern from test_smoke.py — open a review result,
  assert sections mounted, press a button, assert the decide command ran (fake plugin
  with a recording handler; no real store needed).
- Stage 4 is exercised end-to-end manually against the user's own fork with
  `dry_run` — no network in automated tests.

## Exit criteria

- A plan-03 flag flows: `propose` (via MCP) → panel review in TUI → decide → draft →
  (GO) submitted PR visible on GitHub with evidence-cited body → audit log entry.
- CLI/MCP degrade cleanly: `show-proposal --json` returns the full structure; actions
  appear as data, nothing crashes.
- No auto-submission path exists: every route to `gh pr create` passes through an
  explicit human decision recorded in the audit log.

## Risks

- **Contract creep** — `actions` could become a general app-framework temptation.
  Guard: actions may only reference commands on the same plugin, params must be
  JSON-scalar, no nesting.
- **README edit fragility** — free-for-dev reformats occasionally; the unambiguous-
  match-or-err rule converts silent corruption into a loud failure.
- **Upstream drift** — if maintainers change norms after the gate, revisit plan 04's
  decision rather than pushing harder.
