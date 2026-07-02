"""git-tools plugin: surfaces read-only git state as queryable tables.

Proof that the plugin contract generalizes past free-for-dev's fetch-and-index
shape to a subprocess-backed data source. Every command reads; nothing here
mutates the repository (no checkout/rebase/stash-pop — those have different
safety requirements and belong to a separate discussion).

Commands run against the current working directory, so ``super-menu git status``
reports on whatever repo you invoke it from.
"""
from __future__ import annotations

from super_menu.core.plugin import Plugin, Command, Param, CommandResult
from . import gitio


def _not_a_repo() -> CommandResult:
    return CommandResult.err("not a git repository (or no git on PATH)")


def cmd_status() -> CommandResult:
    try:
        gitio.ensure_repo()
        out = gitio.run_git("status", "--porcelain=v2", "--branch")
    except gitio.GitError:
        return _not_a_repo()
    row = gitio.parse_status(out)
    return CommandResult.ok_(
        data=[row],  # single-row table so it renders uniformly with the others
        summary=gitio.status_summary(row),
        kind="table",
        columns=["branch", "upstream", "ahead", "behind",
                 "staged", "unstaged", "untracked"],
    )


def cmd_log(author: str | None = None, since: int | None = None,
            limit: int = 20) -> CommandResult:
    try:
        gitio.ensure_repo()
    except gitio.GitError:
        return _not_a_repo()
    args = ["log", f"--pretty=format:{gitio.LOG_FORMAT}", "-n", str(max(1, limit))]
    if author:
        args.append(f"--author={author}")
    if since:
        args.append(f"--since={since}.days")
    try:
        out = gitio.run_git(*args)
    except gitio.GitError as exc:
        # A repo with no commits yet is a normal, empty result — not an error.
        if "does not have any commits" in str(exc):
            return CommandResult.ok_(
                data=[], summary="no commits yet", kind="table",
                columns=["hash", "author", "date", "subject"],
            )
        return CommandResult.err(str(exc))
    rows = gitio.parse_log(out)
    return CommandResult.ok_(
        data=rows,
        summary=f"{len(rows)} commit(s)"
        + (f" by author~'{author}'" if author else "")
        + (f" in the last {since}d" if since else ""),
        kind="table",
        columns=["hash", "author", "date", "subject"],
    )


def cmd_branch(remote: bool = False, merged: bool = False,
               sort: str = "name") -> CommandResult:
    try:
        gitio.ensure_repo()
    except gitio.GitError:
        return _not_a_repo()
    refs = ["refs/heads"]
    if remote:
        refs.append("refs/remotes")
    sort_key = "-committerdate" if sort == "date" else "refname"
    args = ["for-each-ref", *refs, f"--format={gitio.BRANCH_FORMAT}",
            f"--sort={sort_key}"]
    if merged:
        args.append("--merged")
    try:
        out = gitio.run_git(*args)
    except gitio.GitError as exc:
        return CommandResult.err(str(exc))
    rows = gitio.parse_refs(out)
    return CommandResult.ok_(
        data=rows,
        summary=f"{len(rows)} branch(es)"
        + (" (incl. remotes)" if remote else "")
        + (" merged into HEAD" if merged else ""),
        kind="table",
        columns=["name", "last_commit", "author", "subject"],
    )


def cmd_diff_stats(against: str = "main", limit_files: int = 50) -> CommandResult:
    try:
        gitio.ensure_repo()
    except gitio.GitError:
        return _not_a_repo()
    try:
        out = gitio.run_git("diff", "--numstat", f"{against}...HEAD")
    except gitio.GitError as exc:
        return CommandResult.err(str(exc))
    rows = gitio.parse_numstat(out)
    total = len(rows)
    rows = rows[: max(1, limit_files)]
    adds = sum(r["additions"] for r in rows)
    dels = sum(r["deletions"] for r in rows)
    return CommandResult.ok_(
        data=rows,
        summary=f"{total} file(s) changed vs '{against}', +{adds}/-{dels}"
        + (f" (showing {len(rows)})" if total > len(rows) else ""),
        kind="table",
        columns=["file", "additions", "deletions", "binary"],
    )


class GitToolsPlugin(Plugin):
    id = "git"
    name = "Git Tools"
    description = "Read-only git state (status, log, branches, diffs) as tables."
    icon = "🔀"

    def commands(self) -> list[Command]:
        return [
            Command(
                name="status",
                help="Working-tree summary: branch, upstream, and change counts.",
                handler=cmd_status,
            ),
            Command(
                name="log",
                help="Recent commits, optionally filtered by author or age.",
                handler=cmd_log,
                params=[
                    Param("author", help="Filter by author (substring/regex)."),
                    Param("since", type="int", help="Only commits from the last N days."),
                    Param("limit", type="int", default=20, help="Max commits."),
                ],
            ),
            Command(
                name="branch",
                help="List branches with their tip commit.",
                handler=cmd_branch,
                params=[
                    Param("remote", type="bool", default=False,
                          help="Include remote-tracking branches."),
                    Param("merged", type="bool", default=False,
                          help="Only branches merged into HEAD."),
                    Param("sort", default="name", choices=["name", "date"],
                          help="Sort by name or last-commit date."),
                ],
            ),
            Command(
                name="diff-stats",
                help="Per-file additions/deletions between a ref and HEAD.",
                handler=cmd_diff_stats,
                params=[
                    Param("against", default="main",
                          help="Base ref to diff against (uses merge base)."),
                    Param("limit_files", type="int", default=50,
                          help="Max files to list."),
                ],
            ),
        ]


PLUGIN = GitToolsPlugin()
