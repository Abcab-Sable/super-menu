# Plan 02 — free-for-dev smart discovery (keyword-first)

## Goal

Upgrade free-for-dev from raw substring matching (`plugin.py:_matches`) to ranked
multi-term search, then build `suggest-alternatives`, `analyze-architecture`, and the
annotations overlay on top of it. No embeddings — Claude rephrases queries; the plugin
just needs decent lexical recall and ranking.

## File layout (all inside the existing plugin)

```
src/super_menu/plugins/free_for_dev/
├── fetch.py         # unchanged
├── search.py        # NEW: tokenize, score, rank, synonyms
├── annotations.py   # NEW: load/save/merge user overlay
└── plugin.py        # rewire cmd_search; add 3 commands
```

## Step 1 — search layer (`search.py`)

```python
def tokenize(text: str) -> list[str]           # lowercase, split on non-alnum, drop stopwords
def expand(tokens: list[str]) -> set[str]      # apply SYNONYMS map
def score(entry: dict, tokens: set[str]) -> float
def search(entries: list[dict], query: str, category: str | None, limit: int) -> list[tuple[float, dict]]
```

Scoring — simple additive, tuned by hand, no IDF machinery for ~1.5k short entries:

| Signal | Weight |
|---|---|
| token == name token | 3.0 |
| token in name (substring) | 2.0 |
| token == category token | 1.5 |
| token in description | 1.0 |
| all query tokens matched somewhere (AND bonus) | +2.0 |

`SYNONYMS: dict[str, set[str]]` — seed small and honest (~20 entries), grow from real
misses: `postgres → postgresql`, `auth → authentication authorization sso oauth`,
`db → database`, `k8s → kubernetes`, `ci → "continuous integration"` etc. Keep it a
module-level dict in `search.py`; move to a data file only if it grows past ~50 lines.

Rewire `cmd_search` to use it. **Behavioral contract:** every query the old substring
search matched must still match (token-substring covers it); results gain ordering.
Existing `columns` and result shape unchanged so TUI/CLI/MCP need no edits.

## Step 2 — `suggest-alternatives`

```
Param("technology", required=True, help="Named service, e.g. 'Auth0'")
Param("criteria",  help="Optional constraints, e.g. 'open-source, EU data'")
Param("limit", type="int", default=10)
```

Algorithm (pure lexical, deliberately dumb — Claude does the reasoning on top):

1. Search entries for `technology`. If an entry's *name* matches strongly, take its
   `category` as the anchor; else take the top result's category.
2. Candidates = all entries in the anchor category, minus the named technology itself.
3. Re-rank candidates by `criteria` tokens scored against descriptions (criteria is a
   plain str — comma/space separated; no list Param type exists).
4. Return table: `name | category | score | url | description`. Summary names the
   anchor category so the caller can tell whether the anchor guess was right
   ("12 alternatives to 'Auth0' in category 'Authentication…'").

Failure mode to handle explicitly: technology not in the index at all (e.g. "Oracle
Exadata") → `err` with a hint to pass `category` — add an optional
`Param("category")` override for exactly this case.

## Step 3 — `analyze-architecture`

```
Param("path", required=True, help="Markdown/YAML/text doc to scan")
Param("limit", type="int", default=5, help="Max alternatives per technology")
```

1. Read the file (`pathlib.Path(path).read_text(encoding="utf-8")`; `err` if missing).
2. Detect technology mentions: case-insensitive whole-word match of **entry names**
   (plus synonym keys) against the doc text. Names shorter than 3 chars or that are
   common English words ("Files", "Notes") need a smallignore-list — expect to grow it.
3. For each hit, run step-2's candidate logic with `limit`.
4. Output kind="table": `technology | category | alternatives (comma-joined top names)`.
   Full nested detail goes in `data` rows under an `alternatives` key (JSON-serializable
   list) — the table renderer shows the joined string column; `--json`/MCP consumers get
   the structure.

## Step 4 — annotations overlay (`annotations.py`)

Storage: `plugin_data_dir("free-for-dev") / "annotations.json"`:

```json
{ "Auth0": { "tag": "avoid", "note": "free tier gone", "updated_at": "2026-07-02" } }
```

Keyed by entry name (the index has no stable ids; document that rename = orphaned
annotation, acceptable at this scale).

Commands:
- `annotate` — `Param("name", required=True)`, `Param("tag", choices=["star","avoid","using","note"])`,
  `Param("note")`. Upserts; empty tag+note deletes.
- `annotations` — list all, kind="table".

Merge into search output: `cmd_search` / `suggest-alternatives` gain an `annotation`
column (blank for most rows). Star-tagged entries get +1.5 score, avoid-tagged −3.0 —
the user's overlay visibly shapes ranking, which is the whole point.

## Testing (`tests/test_discovery.py`)

- Fixture index of ~15 hand-written entries (no network, no real index needed).
- Ranking asserts: exact-name match outranks description match; synonym query
  ("postgres") finds "PostgreSQL Hosting Co".
- Regression: every substring the old `_matches` would hit is still returned.
- `suggest-alternatives`: anchor category resolution; unknown-technology err path;
  criteria re-ranking changes order.
- `analyze-architecture`: fixture doc mentioning 3 known + 1 unknown tech; ignore-list
  suppresses a common-word entry name.
- Annotations: upsert → search shows column; avoid-tag sinks an entry's rank.

## Exit criteria

- `super-menu free-for-dev suggest-alternatives --technology Auth0` returns sane ranked
  output from the real index; same via MCP tool `free-for-dev__suggest-alternatives`.
- Old search queries still hit; new ones rank sensibly.
- The synonym map + ignore-list have comments telling future editors how to extend them.

## Out of scope

Embeddings (deferred — needs the query benchmark first, see FEATURE_IDEAS "Deferred"),
structured field extraction, multi-source indexes.
