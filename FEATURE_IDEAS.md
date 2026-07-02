# Super-menu Feature Ideas

## Overview

This document captures ideas for improving super-menu to unlock new capabilities with the free-for-dev GitHub repository and beyond. The core vision: **make Claude Code a first-class citizen** of super-menu, allowing it to intelligently consume and contribute to resource data.

The vision unfolds in two arcs:

1. **Claude as consumer** — give Claude structured, queryable access to repo state and resource data so its advice gets better (git-tools, smart search, architecture analysis).
2. **Claude as curator** — let Claude detect stale data and propose evidence-backed fixes upstream, with a human gatekeeper at every step (flags, proposals, PRs).

Guiding principle throughout: **build the cheapest version that proves the value, then invest.** Fancy tech (embeddings, extraction pipelines) is earned by demonstrated need, not assumed up front.

---

## 1. git-tools Plugin — SHIPPED (tranche 1) & CLOSED

> **Status (2026-07-02):** tranche 1 merged ([PR #5](https://github.com/Abcab-Sable/super-menu/pull/5)); tranche 2 cancelled. Post-ship review concluded the honest scoping note below was understated: Claude Code runs `git` directly, other MCP clients have the official `mcp-server-git`, and humans have the git CLI/lazygit/IDE integrations — no user segment prefers this plugin over their current tool. Its delivered value is as the **reference plugin for subprocess-backed sources** (contract proof, error-handling conventions), and it stays in the tree in that role only. No further git commands. Details in [plans/01-git-tools.md](plans/01-git-tools.md).

An extensible git plugin that surfaces repository state and history as queryable tables. Enables Claude Code to understand repo context during architecture reviews and coding sessions.

**Honest scoping note (pre-ship):** Claude Code can already run `git` directly via its shell. The MCP value here is mostly for *other* MCP clients and for the TUI/CLI surfaces (a git dashboard, structured JSON for scripting). That's still plenty — just don't oversell the Claude angle.

### Commands

- **`status`** — Current branch, dirty state, ahead/behind tracking
  - Output: single-row table with branch info
  - Use case: Claude checks if you're on main before suggesting a workflow

- **`log`** — Browse commit history with filters
  - Params: `--author` (substring match), `--since` (days ago), `--limit` (default 20), `--oneline`
  - Output: table with `hash | author | date | subject`
  - Use case: "What did Sarah commit yesterday?"

- **`branch`** — List branches with metadata
  - Params: `--remote`, `--sort` (by name/date/commits), `--merged`
  - Output: table with `name | last-commit-date | author | behind main (n commits)`
  - Use case: TUI branch picker; Claude identifies stale branches

- **`tag`** — List tags with commit date and message
  - Params: `--sort`, `--since`, `--pattern` (e.g., `v1.*`)
  - Output: table with `tag | commit | date | message`
  - Use case: Release calendar without opening GitHub

- **`stash`** — View stashed changes, search by content
  - Commands: `list`, `search <pattern>`
  - Output: table with `index | date | branch | description`
  - Use case: Visual stash browser in TUI

- **`diff-stats`** — Summary of what changed in a commit or between branches
  - Params: `--against` (compare to another branch, default: main), `--limit-files`
  - Output: table with `file | additions | deletions | status`
  - Use case: "How much did this PR touch?"

- **`blame`** — Who last touched each line (condensed, searchable)
  - Params: `--file` (required), `--since` (days)
  - Output: table with `line-number | author | date | code-snippet`
  - Use case: Track down mystery changes

### Why It Works

**TUI**: A git dashboard with panes for branches, commits, stash browser.

**CLI**: Structured JSON output for scripting. Example: `super-menu git-tools log --author=bot --json | jq '.data | length'`

**MCP**: Any MCP client checks repo state while coding:
- "Am I on main or a feature branch?"
- "How many commits are in this PR?"
- "Is this branch safe to rebase?"

### Implementation Notes

- **No caching.** Local git commands run in milliseconds; caching adds staleness bugs for no measurable gain. Revisit only if a command is proven slow on a monster repo.
- Use plumbing/stable formats (`git status --porcelain`, `--pretty=format:`) — never parse human-readable output
- Assume `.git/` exists in cwd; error gracefully if not
- On Windows: use `pathlib` for all path handling

---

## 2. Enhanced free-for-dev: Smart Discovery

Transform free-for-dev from substring search into intelligent resource discovery. Claude can find alternatives even when terminology differs.

**Key insight:** when queries arrive via MCP, *Claude itself is the semantic layer.* It can rephrase "find alternatives to Postgres" into good keyword + category queries without any embeddings. So the discovery features below are ordered keyword-first, with embeddings as a deliberate later upgrade — not a prerequisite.

### A. Keyword + Category Search Upgrade (build first)

- Tokenized multi-term matching over name + description + category (not raw substring)
- Category-aware ranking (a "Database" query boosts the DBaaS section)
- Synonym map for common aliases ("postgres" → "PostgreSQL", "auth" → "Authentication")
- Cheap, dependency-free, and — for a corpus of ~1,500 short entries — likely 80% of the value

### B. `suggest-alternatives` — Claude-Native MCP Interface

New command: `suggest-alternatives(technology, criteria)`

```python
def cmd_suggest_alternatives(technology: str, criteria: str | None = None) -> CommandResult:
    """Find free alternatives to a named service.

    Params:
    - technology: "PostgreSQL", "Auth0", etc.
    - criteria: optional constraints ("data EU-only", "open-source required")

    Returns: ranked list of alternatives with reasoning
    """
```

Backed by the keyword layer in (A): map the named technology to its category, return the category's entries ranked by criteria-term matches. Claude does the qualitative tradeoff analysis on top.

**Example flow:**
```
Claude: "I see you're using Auth0. Let me check free alternatives..."
→ MCP: suggest_alternatives("Auth0", criteria="open-source preferred")
→ Returns: Keycloak, Supabase Auth, Firebase (free tier), etc.
→ Claude: "Keycloak is self-hosted and free. Firebase gives you managed + free tier. Here's the tradeoff..."
```

### C. Architecture Document Analyzer

**New command:** `analyze-architecture <path-to-doc>`

- Reads markdown/YAML architecture docs
- Extracts technology mentions: "PostgreSQL", "Auth0", "Stripe", etc.
- Looks up each in free-for-dev (via the keyword layer), suggests alternatives
- Returns structured data: `{ technology: "Auth0", mentioned: true, free_alternatives: [...] }`

**Use case:** Claude reviews your architecture and flags paid services with free alternatives.

### D. Annotations: Personal Overlay (cheap, high value)

- Allow users to tag/annotate resources: "⭐ we use this in prod", "❌ don't recommend"
- Build personal overlay on top of free-for-dev data
- Claude considers that context in recommendations

**Storage:**
```
~/.super-menu/plugins/free-for-dev/
├── index.json
└── annotations.json  # user tags + ratings
```

### E. Semantic Search / Embeddings (deferred — earn it)

If, after (A)–(C) ship, real queries demonstrably miss relevant entries, add an embedding layer:

- Prefer a small local model (e.g., `sentence-transformers`) as an **optional extra** (`pip install super-menu[semantic]`) — a paid OpenAI key as a hard dependency of a free-tools plugin is off-brand and a setup barrier
- Cache embeddings in `~/.super-menu/plugins/free-for-dev/embeddings.json`; recompute only when the index refreshes
- Ship with a benchmark: a file of ~20 real queries with expected hits, so "embeddings beat keywords" is measured, not assumed

### F. Structured Extraction (deferred — highest risk)

Extracting structured facts (free tier limits, data residency, pricing tiers) from free-for-dev's freeform prose descriptions is lossy and goes stale the moment a vendor changes pricing. Treat it as an experiment, not a foundation:

- Start with 2–3 fields that are usually stated explicitly (e.g., "open-source", "credit card required")
- Every extracted fact carries `extracted_at` + source text, and the UI displays it as "as described in free-for-dev on <date>" — never as ground truth
- Expand the schema only for fields where extraction accuracy holds up

---

## 3. Flagging System

Allow Claude to mark entries as problematic and propose fixes, with you as the gatekeeper.

### Quick Win First: `check-links`

Before any flagging machinery exists, ship a standalone `check-links` command: crawl entry URLs, report 404s/redirects as a table. It's the one **objectively verifiable, high-confidence signal** in the whole curation arc, it's useful on day one from all three surfaces, and it later becomes the highest-confidence input to the flag pipeline.

### How Flagging Works

**New command:** `flag-entry(entry_name, reason, severity)`

Claude calls this when it spots issues:
```
Claude: "Auth0's free tier was removed in 2023. Flagging as critical."
→ Flag stored locally
```

**Flag structure:**
```json
{
  "entry": "Auth0",
  "reason": "free tier removed Q3 2023",
  "severity": "critical",
  "flagged_by": "claude-code",
  "timestamp": "2026-07-01",
  "evidence_url": "https://auth0.com/blog/...",
  "status": "pending_review"
}
```

### What Claude CAN Flag ✅

- Free tier explicitly removed (with link to announcement)
- Service acquired/discontinued (verifiable from public sources)
- Pricing changed significantly (crawled from their website)
- Broken URLs (404 response)
- Category mismatch

### What Claude CANNOT Flag ❌

- Subjective quality judgments ("this service is worse")
- Unverified complaints
- Competing service suggestions (no "remove X, add Y")
- Opinion-based criteria

### Confidence Scoring

Only auto-propose if confidence > 0.8:
- `url_404`: 0.95 (high confidence)
- `acquisition` + evidence: 0.8
- `pricing_changed` + evidence: 0.7
- Unverified claim: 0.3

**Note:** flags are valuable *locally* even if nothing is ever sent upstream — "this entry is stale" context improves Claude's recommendations immediately. Upstream contribution (§4–5) is a bonus layer, not the justification.

---

## 4. Draft PR Workflow with Human-in-the-Loop

Claude proposes PRs back to free-for-dev, but you stay in control.

### Gate Before Building: Validate Upstream Fit

This arc's payoff depends on free-for-dev maintainers actually accepting these PRs. **Before Phase 4+, do a cheap reality check:** read their CONTRIBUTING.md, scan recent PR acceptance patterns and review latency, and ideally submit one hand-crafted fix PR to see how it lands. If upstream is slow or hostile to this style of contribution, stop at local flags (§3) — they carry most of the personal value anyway.

### New Command

`propose-pr(action, details)`

**Actions:**
- `remove_entry` — Delete from index
- `update_entry` — Modify existing entry
- `add_entry` — Suggest new resource

### Flow

1. Claude flags an entry → Proposal stored locally
2. You review in TUI → See plain-English summary + evidence + options
3. You choose direction (remove vs. deprecate vs. note)
4. Claude generates markdown PR from your choice
5. You preview, then copy/submit to GitHub

**Local state:**
```
~/.super-menu/plugins/free-for-dev/
├── index.json
├── flags.json          (entries Claude flagged)
├── drafts/             (PR drafts awaiting review)
│   ├── auth0-removal.md
│   └── stripe-pricing-update.md
└── submitted.log       (audit trail)
```

### GitHub Integration

- Plugin needs `GITHUB_TOKEN` (with `repo:free-for-dev-org/free-for-dev` scope)
- Creates PRs under your user account (not anonymous)
- Links PR back to the flag + evidence
- **Fallback that ships first:** generate the PR as markdown + a `gh pr create` command you run yourself. Full API integration is a convenience layer on top, not a prerequisite.

---

## 5. PR Proposal Review Panel

A dedicated TUI interface for reviewing Claude's PR proposals. **Markdown is not a good format for non-technical users**—this panel makes it approachable.

### Architectural Note (read before building)

This is the first feature that breaks the "surfaces auto-render from plugin metadata" rule: a review panel with radio options, evidence links, and multi-step approval can't be expressed as a `Command` + `CommandResult` table. Building it means **extending the core surface contract** (e.g., a new result kind like `CommandResult.review(...)` that the TUI knows how to render richly, while CLI/MCP degrade to structured JSON). Design that contract extension deliberately — it's a core change, not a plugin change, and it should stay generic enough that a second plugin could use it.

### Design Principles

**1. Plain English First**
- "In plain English" section explains the _why_ without jargon
- No technical terms requiring Google searches

**2. Structured Choice, Not Yes/No**
- You're choosing _which approach_ makes sense
- Each option has a clear use case
- Example options for removing an entry:
  - Remove entirely (best if better alternatives exist)
  - Move to "Deprecated" section (best if historically important)
  - Add a note: "Free tier discontinued" (best if helps migration planning)

**3. Clickable Evidence**
- Each piece of evidence is highlighted, dated, sourced
- See _why_ Claude thinks this is wrong before deciding
- Types: official announcement, pricing page, user reports, broken links

**4. Confidence & Human Override**
- Only shows proposals Claude is confident about (>0.7 confidence)
- You can still request changes
- You can dismiss if Claude was wrong

### Panel Sections

1. **Plain English Summary** — What's happening and why it matters (2-3 sentences)
2. **What We're Proposing** — Radio buttons for each action option with descriptions
3. **Evidence** — Dated, sourced evidence for each claim (with links)
4. **Why It Matters** — Impact on users and the free-for-dev community
5. **Action Buttons** — [Dismiss] [Request Changes] [Approve & Preview PR]

### Flow After Approval

1. Click "Approve & Preview PR"
2. Claude generates markdown PR based on your chosen option
3. See a preview: "Here's what the PR will look like"
4. Copy to clipboard or click "Open in GitHub" to submit

### Data Structure

Claude generates structured proposals (not markdown):

```python
@dataclass
class Proposal:
    id: str
    title: str
    entry_name: str
    summary: str  # plain English
    action_options: list[ActionOption]
    evidence: list[Evidence]
    why_it_matters: str
    confidence: float  # 0.0–1.0

@dataclass
class ActionOption:
    value: str  # "remove" | "move" | "note"
    title: str  # User-facing label
    description: str  # When to pick this option

@dataclass
class Evidence:
    type: str  # "official_announcement" | "pricing_page" | "user_report"
    title: str
    description: str
    date: str
    url: str
    verified_at: str
```

### Benefits

- **You** make the judgment call (remove vs. deprecate vs. note)—Claude doesn't auto-submit
- **Claude** handles research, evidence, PR drafting
- **Evidence is transparent**—every claim is cited
- **Non-technical**—you review _intent_, not code diffs
- **Auditable**—every proposal + decision is logged

---

## Implementation Roadmap

Reordered by effort-vs-payoff: quick, low-risk wins first; speculative tech and upstream-dependent work gated behind validation.

### Phase 1: git-tools Plugin — DONE / CLOSED
- [x] Implement git-tools plugin with core commands (status, log, branch, diff-stats first)
- [x] Verify it works cleanly on all three surfaces (proves the contract generalizes)
- [~] ~~Add remaining commands (tag, stash, blame)~~ — cancelled; duplicates better tools (see §1 status note)

### Phase 2: Smart Discovery (keyword-first)
- [ ] Upgrade free-for-dev search: tokenized matching, category ranking, synonym map
- [ ] Implement `suggest-alternatives` MCP interface on top of it
- [ ] Implement `analyze-architecture` command
- [ ] Add annotations overlay (`annotations.json`)

### Phase 3: Freshness Signals
- [ ] Ship standalone `check-links` command (404/redirect report)
- [ ] Build flagging system (`flag-entry` command, local storage)
- [ ] Add confidence scoring; wire `check-links` results in as high-confidence flags

### Phase 4: Upstream Validation Gate
- [ ] Review free-for-dev contribution norms + recent PR acceptance/latency
- [ ] Submit one hand-crafted fix PR from `check-links` findings; observe how it lands
- [ ] Go/no-go decision on Phases 5–6 based on the result

### Phase 5: PR Proposal Interface (if gate passes)
- [ ] Design the core contract extension for rich review results (deliberately, as a core change)
- [ ] Build PR Proposal Review Panel in TUI
- [ ] Implement `propose-pr` command + approval workflow
- [ ] Markdown-draft + `gh pr create` fallback ships first

### Phase 6: GitHub Integration (if gate passes)
- [ ] Add GitHub auth + in-app PR creation
- [ ] PR preview interface, audit trail, submission history

### Deferred (earn with evidence)
- [ ] Semantic search/embeddings — only if a query benchmark shows keyword search missing real hits; local model as optional extra
- [ ] Structured extraction — start with 2–3 explicitly-stated fields, always dated + source-attributed

---

## Why This Matters

**For Claude Code:**
- Understand repo state during architecture reviews
- Check tech stack against free-for-dev data
- Suggest better alternatives with reasoning

**For free-for-dev:**
- Stay fresh without burdening maintainers
- Evidence-based improvements, not spam
- Transparent audit trail of who suggested what

**For You:**
- One tool for research + decision-making + contribution
- Complexity abstracted away (no markdown reviews)
- Confidence that suggestions are well-sourced

**Flywheel Effect:**
```
Better data in free-for-dev
→ Claude gives better advice
→ More people contribute improvements
→ Data stays fresh
→ Claude's recommendations improve
→ cycle repeats
```

---

## Notes

- All proposals must cite evidence. No vague "this doesn't fit"—it's "here's the announcement + date + link."
- User stays in control. Claude proposes, you approve. No auto-submissions.
- Keeps a local audit trail so you can see what Claude has flagged and what you approved.
- Focus on objective criteria (free tier removed, pricing changed, URL broken) not subjective opinions.
- Build the cheapest version that proves the value; upgrade with evidence, not optimism.
