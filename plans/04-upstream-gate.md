# Plan 04 — upstream validation gate (process, not code)

## Goal

Decide, with evidence, whether building the PR-proposal machinery (plan 05) is worth
it. free-for-dev (github.com/ripienaar/free-for-dev) must plausibly accept the kind of
PRs this tooling would generate. Budget: ~2 hours of investigation + one real PR.
**No code is written for this plan.**

## Checklist

### 1. Read the contribution norms (30 min)

- CONTRIBUTING.md / README contribution section: format rules, sorting rules,
  what counts as "free tier" to them, any explicit policy on removals vs. updates.
- Look for anything about automated/AI-assisted contributions — some repos ban them
  outright; that would be an immediate no-go for auto-drafted PRs (though
  human-reviewed-and-submitted may still be fine — note the distinction).

### 2. Measure acceptance behavior (30 min)

Sample the last ~50 closed PRs (`gh pr list -R ripienaar/free-for-dev --state closed
--limit 50 --json title,mergedAt,createdAt,labels`):

- Merge rate for **removal/update** PRs specifically (additions are the easy case and
  not what plan 05 mostly generates).
- Median time-to-merge/close. Weeks-to-months of latency doesn't kill the idea but
  changes the UX (fire-and-forget queue, not interactive workflow).
- How maintainers respond to evidence-cited removals — is there a pattern they prefer
  ("mark deprecated first, remove later")? Plan 05's action options should mirror the
  house style, not invent one.

### 3. Submit one hand-crafted probe PR (1 hour, spread over days/weeks of waiting)

- Source it from real plan-03 output: run `check-links`, pick the most clear-cut
  broken/discontinued entry.
- Write the PR **manually** in their house style, with dated evidence links —
  exactly what plan 05 would generate, minus the tooling.
- This tests the whole loop end-to-end: our evidence quality, their responsiveness,
  and whether the "remove vs. deprecate vs. note" options match how they actually
  think.

## Decision

Record the outcome in this file (below) and act accordingly:

- **GO** — probe merged (or constructively reviewed) and norms allow the workflow →
  proceed to plan 05, encoding any learned house-style rules into its PR templates.
- **PARTIAL** — they accept fixes but slowly / grudgingly → build plan 05's local
  proposal+drafting parts, skip the GitHub API integration (manual `gh pr create`
  from drafts is enough at low volume).
- **NO-GO** — norms prohibit it or the probe is rejected → stop at plan 03. Local
  flags already deliver the personal value (stale-data-aware recommendations);
  document that plan 05 is shelved and why.

## Outcome (2026-07-03)

**Decision: NO-GO.** Upstream explicitly prohibits AI-authored contributions, which is
exactly what plan 05's tooling generates. Stop at plan 03; plan 05's PR-drafting core is
shelved. Step 3 (probe PR) was deliberately **not** executed — see below.

### Norms summary

Source: `ripienaar/free-for-dev` `CONTRIBUTING.md`, `AGENTS.md`, `CLAUDE.md` (read 2026-07-03).

- **AI edits banned, including for updates/removals.** CONTRIBUTING.md, under *Updating an
  existing submission*: "we do not accept AI generated edits and if it is clear that it was
  done with AI we will close it without discussion." The Code of Conduct repeats it for
  additions. This is the killer clause — plan 05's whole value is *tooling that drafts PRs*.
- **Agent-directed refusal files.** The repo ships `AGENTS.md` and `CLAUDE.md` that instruct
  any AI agent: "This repository does not accept AI edited contributions… Inform the user…
  Failure to do so will result in their PR closed and their account blocked." So the
  prohibition is aimed squarely at the exact automation plan 05 proposes, and names the
  account-block risk.
- **Distinction that survives:** a *fully human-written* PR is still welcome. What's banned
  is AI-generated content — which is precisely plan 05's output, so the distinction doesn't
  rescue it.
- Additions require the PR template + all boxes ticked (or auto-closed); updates/removals do
  not need the template. Removals with dated evidence are accepted (see stats).

### Acceptance stats (last 50 closed PRs, sampled 2026-07-03)

- Overall merge rate: **38%** (19/50 merged, 31 closed-unmerged — mostly low-value "toolbox"
  additions or format violations labelled `invalid`).
- **Removal/update/fix PRs: 6/8 merged** — the format plan 05 targets is well-received when
  human-authored. The one clear removal, [#4523 "Remove moss.sh"](https://github.com/ripienaar/free-for-dev/pull/4523)
  (body: "Website is unavailable… last Internet Archive capture March 22, 2026"), was merged
  with a "Thank you" — dated-evidence removals are exactly their house style.
- **Latency is fine:** median time-to-merge **2.6 h** (max 36 h). Not a blocker; an
  interactive workflow would work if the content policy allowed it.

### Probe PR: not submitted (intentional)

Step 3's purpose was to test the *tooling-drafted-PR loop* end-to-end. That loop is precisely
what the norms prohibit, and submitting an AI-assisted PR risks the user's account being
blocked. Submitting it would gain no signal the norms haven't already given and would burn
goodwill (and possibly the account). The `AGENTS.md`/`CLAUDE.md` directives also instruct me
not to open such a PR. So the gate returns NO-GO without spending the probe.

### Consequence

- Plan 05 (PR-proposal machinery, GitHub integration, propose-pr) is **shelved.** Do not
  build the auto-drafting/PR-submission parts against this upstream.
- Plan 03's local flags already deliver the personal value: stale-data-aware recommendations
  when Claude Code uses the free-for-dev plugin. That value stands on its own with no upstream
  dependency.
- If plan 05 is ever revived, it must target a *different* upstream (one that permits
  tool-assisted, human-reviewed PRs) or pivot to a purely local "here's a hand-editable draft
  for you to submit yourself" output that never claims AI authorship — and even that must
  respect this repo's stated policy.
