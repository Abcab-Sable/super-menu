"""Tests for the git-tools plugin.

Follows test_smoke.py conventions: plain asserts, standalone-runnable via
``uv run python tests/test_git_tools.py``, no network. Parser tests feed captured
fixture strings so they pass in CI without a repo; one integration test builds a
throwaway repo in a temp dir.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from super_menu.core.registry import default_registry
from super_menu.plugins.git_tools import gitio

US = gitio.US


# --------------------------------------------------------------------------- #
# parser tests (no subprocess)
# --------------------------------------------------------------------------- #

def test_parse_status_clean_tracking():
    fixture = (
        "# branch.oid 1a2b3c4d5e6f\n"
        "# branch.head main\n"
        "# branch.upstream origin/main\n"
        "# branch.ab +0 -0\n"
    )
    row = gitio.parse_status(fixture)
    assert row["branch"] == "main"
    assert row["upstream"] == "origin/main"
    assert row["ahead"] == 0 and row["behind"] == 0
    assert row["staged"] == 0 and row["unstaged"] == 0 and row["untracked"] == 0
    assert gitio.status_summary(row) == "on main, clean, up to date"


def test_parse_status_dirty_and_ahead():
    fixture = (
        "# branch.oid 1a2b3c4d\n"
        "# branch.head feature\n"
        "# branch.upstream origin/feature\n"
        "# branch.ab +2 -1\n"
        "1 M. N... 100644 100644 100644 aaa bbb staged.py\n"
        "1 .M N... 100644 100644 100644 ccc ddd unstaged.py\n"
        "2 R. N... 100644 100644 100644 eee fff R100 new.py\told.py\n"
        "? untracked.txt\n"
    )
    row = gitio.parse_status(fixture)
    assert row["staged"] == 2      # M. and R.
    assert row["unstaged"] == 1    # .M
    assert row["untracked"] == 1
    assert row["ahead"] == 2 and row["behind"] == 1
    summary = gitio.status_summary(row)
    assert "2 staged" in summary and "1 untracked" in summary
    assert "ahead 2" in summary and "behind 1" in summary


def test_parse_status_detached_head():
    fixture = (
        "# branch.oid deadbeefcafef00d\n"
        "# branch.head (detached)\n"
    )
    row = gitio.parse_status(fixture)
    assert row["branch"] == "(detached) deadbeef"
    assert row["upstream"] == ""  # no upstream line -> empty, no ahead/behind noise
    assert gitio.status_summary(row) == "on (detached) deadbeef, clean"


def test_parse_log_and_emoji_subject():
    fixture = (
        US.join(["abc123", "Ada Lovelace", "2026-07-01T10:00:00+00:00", "first | commit"]) + "\n"
        + US.join(["def456", "Grace Hopper", "2026-07-02T11:00:00+00:00", "ship it 🚀"]) + "\n"
    )
    rows = gitio.parse_log(fixture)
    assert len(rows) == 2
    assert rows[0]["hash"] == "abc123"
    assert rows[0]["subject"] == "first | commit"  # pipe in subject survives US delimiter
    assert rows[1]["author"] == "Grace Hopper"
    assert rows[1]["subject"] == "ship it 🚀"


def test_parse_refs():
    fixture = US.join(
        ["main", "2026-07-02T11:00:00+00:00", "Ada", "initial commit"]
    ) + "\n"
    rows = gitio.parse_refs(fixture)
    assert len(rows) == 1
    assert rows[0]["name"] == "main"
    assert rows[0]["author"] == "Ada"


def test_parse_numstat_with_binary():
    fixture = "10\t2\tsrc/app.py\n-\t-\tlogo.png\n"
    rows = gitio.parse_numstat(fixture)
    assert rows[0] == {"file": "src/app.py", "additions": 10, "deletions": 2, "binary": False}
    assert rows[1]["binary"] is True
    assert rows[1]["additions"] == 0 and rows[1]["deletions"] == 0


# --------------------------------------------------------------------------- #
# integration (throwaway repo) + error path
# --------------------------------------------------------------------------- #

def _init_repo(path: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=t@e.st", *args],
            cwd=path, check=True, capture_output=True, text=True,
        )
    git("init", "-b", "main")
    (path / "a.txt").write_text("hello\n", encoding="utf-8")
    git("add", "a.txt")
    git("commit", "-m", "first commit")
    (path / "b.txt").write_text("world\n", encoding="utf-8")
    git("add", "b.txt")
    git("commit", "-m", "second commit")


def test_integration_log_and_status_roundtrip():
    plugin = default_registry().get("git")
    tmp = Path(tempfile.mkdtemp())
    try:
        _init_repo(tmp)
        # Run handlers from inside the repo by chdir — commands read the cwd.
        import os
        prev = os.getcwd()
        os.chdir(tmp)
        try:
            log_res = plugin.command("log").run({"limit": 10})
            status_res = plugin.command("status").run({})
        finally:
            os.chdir(prev)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    assert log_res.ok
    assert len(log_res.data) == 2
    assert log_res.data[0]["subject"] == "second commit"
    # Result must be JSON-serializable end to end.
    json.dumps(log_res.to_dict())

    assert status_res.ok
    assert status_res.data[0]["branch"] == "main"
    assert "clean" in status_res.summary
    json.dumps(status_res.to_dict())


def test_status_not_a_repo():
    plugin = default_registry().get("git")
    tmp = Path(tempfile.mkdtemp())
    import os
    prev = os.getcwd()
    try:
        os.chdir(tmp)
        result = plugin.command("status").run({})
    finally:
        os.chdir(prev)
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    assert not result.ok
    assert "not a git repository" in result.summary


def test_git_plugin_discovered():
    assert default_registry().get("git") is not None


if __name__ == "__main__":
    test_parse_status_clean_tracking()
    test_parse_status_dirty_and_ahead()
    test_parse_status_detached_head()
    test_parse_log_and_emoji_subject()
    test_parse_refs()
    test_parse_numstat_with_binary()
    test_integration_log_and_status_roundtrip()
    test_status_not_a_repo()
    test_git_plugin_discovered()
    print("all git-tools tests passed")
