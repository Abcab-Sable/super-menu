"""Fetch and parse the free-for-dev README into a structured, searchable index.

Source: https://github.com/ripienaar/free-for-dev (README.md)

The README is a long markdown document of ``## Section`` / ``### Subsection``
headings followed by bullet lists of ``- [Name](url) - description`` entries.
We flatten it into ``{name, url, description, category}`` records cached on disk
so search works offline and Claude Code can query it instantly.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from super_menu.core.config import plugin_data_dir

RAW_URL = "https://raw.githubusercontent.com/ripienaar/free-for-dev/master/README.md"
PLUGIN_ID = "free-for-dev"

# Headings before the real catalog starts (intro / table-of-contents noise).
_SKIP_HEADINGS = {"table of contents", "free for dev"}

# Matches a top-level bullet that is a link entry. Captures name, url, rest.
_ENTRY_RE = re.compile(r"^\s*[-*]\s+\[(?P<name>[^\]]+)\]\((?P<url>[^)]+)\)(?P<rest>.*)$")
_HEADING_RE = re.compile(r"^(#{2,4})\s+(?P<title>.+?)\s*#*$")


@dataclass
class Entry:
    name: str
    url: str
    description: str
    category: str


def _index_path() -> Path:
    return plugin_data_dir(PLUGIN_ID) / "index.json"


def _seed_path() -> Path:
    # A copy shipped with the package so the plugin works before first `update`.
    return Path(__file__).parent / "data" / "index.json"


def parse_markdown(md: str) -> list[Entry]:
    entries: list[Entry] = []
    category = "Uncategorized"
    for line in md.splitlines():
        h = _HEADING_RE.match(line)
        if h:
            title = h.group("title").strip()
            if title.lower() not in _SKIP_HEADINGS:
                category = title
            continue
        m = _ENTRY_RE.match(line)
        if not m:
            continue
        url = m.group("url").strip()
        # Skip the README's table-of-contents bullets — ``- [Section](#anchor)``
        # links to in-page headings, not real services. Only genuine external
        # http(s) links are catalog entries; anything else (``#anchor``, relative
        # paths, ``mailto:``) would just pollute search and analyze-architecture.
        if not url.lower().startswith(("http://", "https://")):
            continue
        rest = m.group("rest")
        # Strip a leading separator (—, -, :, etc.) from the description.
        desc = re.sub(r"^\s*[-–—:]\s*", "", rest).strip()
        entries.append(Entry(
            name=m.group("name").strip(),
            url=url,
            description=desc,
            category=category,
        ))
    return entries


def fetch_markdown(timeout: float = 30.0) -> str:
    req = urllib.request.Request(RAW_URL, headers={"User-Agent": "super-menu/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (trusted URL)
        return resp.read().decode("utf-8")


def update_index() -> dict:
    """Fetch the latest README, parse it, and write the cache. Returns metadata."""
    md = fetch_markdown()
    entries = parse_markdown(md)
    payload = {
        "source": RAW_URL,
        "fetched_at": int(time.time()),
        "count": len(entries),
        "entries": [asdict(e) for e in entries],
    }
    path = _index_path()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=0), encoding="utf-8")
    return {"count": len(entries), "path": str(path), "fetched_at": payload["fetched_at"]}


def load_index() -> Optional[dict]:
    """Load the cached index, falling back to the packaged seed if present."""
    for path in (_index_path(), _seed_path()):
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
    return None
