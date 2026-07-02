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

## Outcome (fill in when done)

- Norms summary: _pending_
- Acceptance stats: _pending_
- Probe PR: _pending (link)_
- Decision: _pending_
