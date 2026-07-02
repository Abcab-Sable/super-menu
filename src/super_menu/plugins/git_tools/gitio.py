"""Data layer for the git-tools plugin: one subprocess choke point plus pure
parsers for git's machine-readable output.

Analogue of free_for_dev's ``fetch.py`` — no ``Plugin``/``Command`` imports, so
every parser is unit-testable against captured fixture strings without a repo or
a subprocess. All git calls go through :func:`run_git`; all parsing lives in the
``parse_*`` functions.

Machine formats only (``--porcelain=v2``, ``for-each-ref --format=``,
``--pretty=format:``) with ``\\x1f`` (ASCII unit separator) as the field delimiter,
because commit subjects and branch names can contain ``|``, tabs, or anything else.
No caching: local git is milliseconds.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

# ASCII unit separator — safe field delimiter for git's ``--format`` strings.
US = "\x1f"


class GitError(Exception):
    """A git invocation exited non-zero; message is the trimmed stderr."""


def run_git(*args: str, cwd: Optional[Path] = None) -> str:
    """Run git and return stdout. Raises :class:`GitError` (never
    ``CalledProcessError`` or ``OSError``) carrying stderr on a non-zero exit."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except OSError as exc:  # git binary missing/unlaunchable
        raise GitError(f"git not found on PATH ({exc})") from exc
    if proc.returncode != 0:
        raise GitError(proc.stderr.strip() or f"git {args[0] if args else ''} failed")
    return proc.stdout


def ensure_repo(cwd: Optional[Path] = None) -> None:
    """Raise :class:`GitError` if ``cwd`` is not inside a git work tree."""
    run_git("rev-parse", "--git-dir", cwd=cwd)


# --------------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------------- #

def parse_status(text: str) -> dict:
    """Parse ``git status --porcelain=v2 --branch`` into a single summary row.

    Returns a dict with keys: branch, upstream, ahead, behind, staged,
    unstaged, untracked. Detached HEAD reports ``branch = "(detached) <hash>"``.
    """
    branch = ""
    oid = ""
    upstream = ""
    ahead = behind = 0
    staged = unstaged = untracked = 0

    for line in text.splitlines():
        if line.startswith("# branch.head "):
            branch = line[len("# branch.head "):].strip()
        elif line.startswith("# branch.oid "):
            oid = line[len("# branch.oid "):].strip()
        elif line.startswith("# branch.upstream "):
            upstream = line[len("# branch.upstream "):].strip()
        elif line.startswith("# branch.ab "):
            for part in line[len("# branch.ab "):].split():
                if part.startswith("+"):
                    ahead = int(part[1:])
                elif part.startswith("-"):
                    behind = int(part[1:])
        elif line.startswith(("1 ", "2 ")):
            # Ordinary/renamed change: field 1 is the two-char <XY> staged/unstaged code.
            fields = line.split(" ", 2)
            if len(fields) >= 2 and len(fields[1]) >= 2:
                xy = fields[1]
                if xy[0] != ".":
                    staged += 1
                if xy[1] != ".":
                    unstaged += 1
        elif line.startswith("u "):
            unstaged += 1  # unmerged path — an unresolved working-tree conflict
        elif line.startswith("? "):
            untracked += 1

    if branch == "(detached)":
        short = oid[:8] if oid and oid != "(initial)" else "?"
        branch = f"(detached) {short}"

    return {
        "branch": branch,
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "staged": staged,
        "unstaged": unstaged,
        "untracked": untracked,
    }


def status_summary(row: dict) -> str:
    """Human sentence for a status row, e.g. ``on main, clean, up to date``."""
    bits: list[str] = []
    if not (row["staged"] or row["unstaged"] or row["untracked"]):
        bits.append("clean")
    else:
        if row["staged"]:
            bits.append(f"{row['staged']} staged")
        if row["unstaged"]:
            bits.append(f"{row['unstaged']} unstaged")
        if row["untracked"]:
            bits.append(f"{row['untracked']} untracked")
    if row["upstream"]:
        if row["ahead"] or row["behind"]:
            ab: list[str] = []
            if row["ahead"]:
                ab.append(f"ahead {row['ahead']}")
            if row["behind"]:
                ab.append(f"behind {row['behind']}")
            bits.append(", ".join(ab))
        else:
            bits.append("up to date")
    return f"on {row['branch']}, " + ", ".join(bits)


# --------------------------------------------------------------------------- #
# log
# --------------------------------------------------------------------------- #

LOG_FORMAT = US.join(["%h", "%an", "%aI", "%s"])


def parse_log(text: str) -> list[dict]:
    """Parse the unit-separated ``git log`` stream into commit rows."""
    rows: list[dict] = []
    for line in text.splitlines():
        if not line:
            continue
        parts = line.split(US)
        if len(parts) != 4:
            continue
        h, author, date, subject = parts
        rows.append({"hash": h, "author": author, "date": date, "subject": subject})
    return rows


# --------------------------------------------------------------------------- #
# branch
# --------------------------------------------------------------------------- #

BRANCH_FORMAT = US.join(
    ["%(refname:short)", "%(committerdate:iso-strict)", "%(authorname)", "%(subject)"]
)


def parse_refs(text: str) -> list[dict]:
    """Parse a ``for-each-ref`` stream with :data:`BRANCH_FORMAT` into rows."""
    rows: list[dict] = []
    for line in text.splitlines():
        if not line:
            continue
        parts = line.split(US)
        if len(parts) != 4:
            continue
        name, last_commit, author, subject = parts
        rows.append(
            {
                "name": name,
                "last_commit": last_commit,
                "author": author,
                "subject": subject,
            }
        )
    return rows


# --------------------------------------------------------------------------- #
# diff-stats
# --------------------------------------------------------------------------- #

def parse_numstat(text: str) -> list[dict]:
    """Parse ``git diff --numstat`` lines. Binary files print ``-`` for both
    counts; those become ``0`` with ``binary=true`` on the row."""
    rows: list[dict] = []
    for line in text.splitlines():
        if not line:
            continue
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        add_raw, del_raw, path = parts
        binary = add_raw == "-" or del_raw == "-"
        rows.append(
            {
                "file": path,
                "additions": 0 if binary else int(add_raw),
                "deletions": 0 if binary else int(del_raw),
                "binary": binary,
            }
        )
    return rows
