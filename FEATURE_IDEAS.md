# Super-menu Feature Ideas

## Overview

This document captures ideas for improving super-menu to unlock new capabilities with the free-for-dev GitHub repository and beyond. The core vision: **make Claude Code a first-class citizen** of super-menu, allowing it to intelligently consume and contribute to resource data.

---

## 1. git-tools Plugin

An extensible git plugin that surfaces repository state and history as queryable tables. Enables Claude Code to understand repo context during architecture reviews and coding sessions.

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

**MCP**: Claude checks repo state while coding:
- "Am I on main or a feature branch?"
- "How many commits are in this PR?"
- "Is this branch safe to rebase?"

### Implementation Notes

- Cache `log` for 30s, `branch` for 60s (avoid re-running expensive commands)
- Use standard git flags (`--oneline`, `--pretty=format:`) for portable output
- Assume `.git/` exists in cwd; error gracefully if not
- Use `git status --porcelain` (stable API) instead of human-readable output
- On Windows: use `pathlib` for all path handling

---

## 2. Enhanced free-for-dev: Semantic Search & RAG

Transform free-for-dev from substring search into intelligent resource discovery. Claude can find alternatives even when terminology differs.

### Core Improvements

#### A. Semantic Search / RAG Layer

- Embed free-for-dev entries (name + description) using embeddings
- Cache embeddings locally (deterministic + expensive to re-compute)
- When Claude queries "database service with free tier", get semantically similar results
- Unlock: "find alternatives to Postgres" without saying "Postgres"

**Implementation:**
- Use OpenAI's `text-embedding-3-small` or open model (e.g., `sentence-transformers`)
- Store embeddings in `~/.super-menu/plugins/free-for-dev/embeddings.json`
- Only update when free-for-dev index is refreshed

#### B. Structured Extraction from free-for-dev

- Currently: `name | description | category | url`
- Extract: **free tier limits, data residency, support level, integrations, pricing tier**
- Parse descriptions to build a structured schema

**Example:**
```json
{
  "name": "Supabase",
  "category": "Database",
  "free_tier": {
    "storage_gb": 500,
    "monthly_credits": null,
    "api_calls_unlimited": true,
    "data_residency": "US only"
  },
  "integrations": ["Prisma", "TypeORM", "Next.js"],
  "pricing_tiers": ["free", "pro", "enterprise"]
}
```

#### C. Architecture Document Analyzer

**New command:** `analyze-architecture <path-to-doc>`

- Reads markdown/YAML architecture docs
- Extracts technology mentions: "PostgreSQL", "Auth0", "Stripe", etc.
- Looks up each in free-for-dev, suggests alternatives
- Returns structured data: `{ technology: "Auth0", mentioned: true, free_alternatives: [...] }`

**Use case:** Claude reviews your architecture and flags paid services with free alternatives.

#### D. Claude-Native MCP Interface

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

**Example flow:**
```
Claude: "I see you're using Auth0. Let me check free alternatives..."
→ MCP: suggest_alternatives("Auth0", criteria="open-source preferred")
→ Returns: Keycloak, Supabase Auth, Firebase (free tier), etc.
→ Claude: "Keycloak is self-hosted and free. Firebase gives you managed + free tier. Here's the tradeoff..."
```

#### E. Multi-source & Custom Categories

- Allow users to tag/annotate resources: "⭐ we use this in prod", "❌ don't recommend"
- Build personal overlay on top of free-for-dev data
- Claude considers that context in recommendations

**Storage:**
```
~/.super-menu/plugins/free-for-dev/
├── index.json
├── embeddings.json
└── annotations.json  # user tags + ratings
```

---

## 3. Flagging System

Allow Claude to mark entries as problematic and propose fixes, with you as the gatekeeper.

### How It Works

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

---

## 4. Draft PR Workflow with Human-in-the-Loop

Claude proposes PRs back to free-for-dev, but you stay in control.

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

---

## 5. PR Proposal Review Panel

A dedicated TUI interface for reviewing Claude's PR proposals. **Markdown is not a good format for non-technical users**—this panel makes it approachable.

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

### Phase 1: Semantic Search Foundation
- [ ] Add embedding + semantic search to free-for-dev plugin
- [ ] Cache embeddings locally
- [ ] Implement `analyze-architecture` command

### Phase 2: Structured Data Extraction
- [ ] Parse free-for-dev descriptions → extract free tier limits, data residency, etc.
- [ ] Update plugin data model to include structured metadata
- [ ] Implement `suggest-alternatives` MCP interface

### Phase 3: Flagging & Detection
- [ ] Build flagging system (`flag-entry` command)
- [ ] Add confidence scoring
- [ ] Implement automatic URL checks (404 detection)

### Phase 4: PR Proposal Interface
- [ ] Design + build PR Proposal Review Panel in TUI
- [ ] Implement `propose-pr` command
- [ ] Build approval workflow (options selection, change requests)

### Phase 5: GitHub Integration
- [ ] Add GitHub auth + PR creation
- [ ] Generate markdown PRs from user-chosen options
- [ ] Build PR preview interface
- [ ] Audit trail + submission history

### Phase 6: git-tools Plugin (Bonus)
- [ ] Implement git-tools plugin with core commands
- [ ] Add MCP interface for Claude Code
- [ ] Cache strategy for performance

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
