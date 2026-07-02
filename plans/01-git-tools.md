# Plan 01 — git-tools plugin

## Goal

A second plugin that surfaces git state as queryable tables on all three surfaces.
Also serves as proof that the plugin contract generalizes beyond free-for-dev
(subprocess-backed instead of fetch-and-index).

## Identity

- Package: `src/super_menu/plugins/git_tools/`
- `Plugin.id = "git"` → CLI `super-menu git log`, MCP tool `git__log`.
  Short, stable, and sidesteps the docstring rule that ids be hyphen-free
  (which `free-for-dev` already violates — see fix-in-passing below).
- `name = "Git Tools"`, `icon = "🔀"` (icons are safe: cli.py forces UTF-8).

## File layout

```
src/super_menu/plugins/git_tools/
├── __init__.py      # from .plugin import PLUGIN  (mirrors free_for_dev)
├── plugin.py        # Plugin subclass + Command definitions
└── gitio.py         # subprocess wrapper + output parsers (no Command imports)
```

`gitio.py` is the analogue of free_for_dev's `fetch.py`: pure data layer, unit-testable
without a Plugin instance.

## The subprocess wrapper (gitio.py)

One choke point for every git call:

```python
def run_git(*args: str, cwd: Path | None = None) -> str:
    """Run git, return stdout. Raises GitError (not CalledProcessError) with stderr."""
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8"
    )
    if proc.returncode != 0:
        raise GitError(proc.stderr.strip() or f"git {args[0]} failed")
    return proc.stdout
```

- Handlers call `ensure_repo()` first (`git rev-parse --git-dir`); on failure return
  `CommandResult.err("not a git repository: <cwd>")`. `Command.run` already converts
  stray exceptions to `err`, but explicit checks give better messages.
- Machine formats only: `--porcelain`, `for-each-ref --format=`, `--pretty=format:`
  with `%x1f` (unit separator) as the field delimiter — commit subjects can contain
  `|`, tabs, anything else.
- **No caching** (per FEATURE_IDEAS): local git is milliseconds.

## Commands (build in this order)

### Tranche 1 — core

| Command | Params | git invocation | Columns |
|---|---|---|---|
| `status` | — | `status --porcelain=v2 --branch` | branch, upstream, ahead, behind, staged, unstaged, untracked |
| `log` | `author` str, `since` int (days), `limit` int=20 | `log --pretty=format:%h%x1f%an%x1f%aI%x1f%s -n <limit> [--author=] [--since=<n>.days]` | hash, author, date, subject |
| `branch` | `remote` bool, `merged` bool, `sort` str choices=[name,date] | `for-each-ref refs/heads [refs/remotes] --format=... --sort=` | name, last_commit, author, subject |
| `diff-stats` | `against` str="main", `limit_files` int=50 | `diff --numstat <against>...HEAD` | file, additions, deletions |

Notes:
- `status` returns a **single-row table** (kind="table", one dict) so it renders
  uniformly; summary carries the human sentence ("on main, clean, up to date").
- `branch` "behind main" per-branch counts cost one `rev-list --count` per branch —
  **skip in tranche 1**, add later behind a `with_counts` bool param if wanted.
- `diff-stats`: `--numstat` prints `-` for binary files; parse to 0 with a
  `binary=true` marker in the row.

### Tranche 2 — after tranche 1 works on all three surfaces

| Command | Params | Notes |
|---|---|---|
| `tag` | `pattern` str, `limit` int=30 | `for-each-ref refs/tags --format=` (annotated vs lightweight both covered) |
| `stash` | `pattern` str (optional) | `stash list --pretty=format:`; when `pattern` given, filter with `stash list -G<pattern>` — flat command, no subcommands (the contract has no nesting) |
| `blame` | `file` str required, `since` int (days) | `blame --line-porcelain -- <file>`; condense runs of the same author into one row: line_start, line_end, author, date |

## Testing (`tests/test_git_tools.py`)

Follow test_smoke.py conventions (plain asserts, standalone-runnable, no network):

1. Parser tests feed captured fixture strings into `gitio` parse functions — no
   subprocess, works in CI without a repo.
2. One integration test builds a throwaway repo in `tempfile.mkdtemp()` (git init,
   two commits with `-c user.name=... -c user.email=...`), asserts `log`/`status`
   round-trip through `Command.run` and `to_dict()` is JSON-serializable.
3. Error path: run `status` handler with cwd pointing at an empty temp dir →
   `ok=False`, "not a git repository" in summary.
4. Extend test_smoke.py's discovery test: `reg.get("git") is not None`.

## Edge cases

- Detached HEAD: `status` reports `branch = "(detached)"` + short hash, not an error.
- Repo with zero commits: `log` returns ok with empty data, summary "no commits yet".
- Non-ASCII author names / subjects: covered by `encoding="utf-8"` on subprocess;
  add one fixture with an emoji subject.
- `blame` on a file not tracked: clean `err`, message includes the path.

## Fix in passing

`Plugin.id` docstring (`core/plugin.py:110`) claims ids must be hyphen/underscore-free,
but `free-for-dev` has hyphens and works everywhere. Correct the docstring to match
reality (lowercase, `[a-z0-9-]`) rather than renaming the reference plugin.

## Exit criteria

- All tranche-1 commands render in TUI form/table, CLI `--json`, and MCP.
- `uv run python tests/test_git_tools.py` and existing smoke tests pass.
- README/CLAUDE.md plugin list mentions the new plugin.

## Out of scope

Mutations (checkout, rebase, stash pop) — this plugin is read-only by design; write
operations have different safety requirements and belong to a separate discussion.
