"""User annotations overlay for free-for-dev entries.

A small JSON file, keyed by entry *name*, that lets the user tag entries
(star / avoid / using / note) and have those tags visibly shape search ranking.
The index has no stable ids, so name is the key: renaming an upstream entry
orphans its annotation. That is acceptable at this scale — a few hundred hand
annotations against a slowly-moving catalog.

Storage: ``plugin_data_dir("free-for-dev") / "annotations.json"``::

    { "Auth0": { "tag": "avoid", "note": "free tier gone",
                 "updated_at": "2026-07-02" } }
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from super_menu.core.config import plugin_data_dir

PLUGIN_ID = "free-for-dev"

# Tags the user may apply. ``note`` means "no ranking effect, just a comment".
TAGS = ["star", "avoid", "using", "note"]

# How each tag nudges a matched entry's search score. The overlay visibly
# reshapes ranking — that is the whole point of the feature.
_SCORE_ADJUST = {
    "star": 1.5,
    "avoid": -3.0,
    "using": 0.0,
    "note": 0.0,
}


def _path() -> Path:
    return plugin_data_dir(PLUGIN_ID) / "annotations.json"


def load() -> dict[str, dict]:
    """Return the annotation map, or an empty dict if none/unreadable."""
    path = _path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save(annotations: dict[str, dict]) -> None:
    _path().write_text(
        json.dumps(annotations, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def upsert(name: str, tag: str | None = None, note: str | None = None) -> dict:
    """Create or update the annotation for ``name``.

    An empty tag *and* empty note deletes the annotation (the natural "clear"
    gesture). Returns ``{"action": "set"|"deleted", "name": ...}``.
    """
    annotations = load()
    tag = (tag or "").strip()
    note = (note or "").strip()

    if not tag and not note:
        existed = annotations.pop(name, None) is not None
        save(annotations)
        return {"action": "deleted" if existed else "noop", "name": name}

    annotations[name] = {
        "tag": tag,
        "note": note,
        "updated_at": date.today().isoformat(),
    }
    save(annotations)
    return {"action": "set", "name": name, **annotations[name]}


def score_adjust(name: str, annotations: dict[str, dict]) -> float:
    """Ranking delta contributed by ``name``'s tag (0.0 if none)."""
    entry = annotations.get(name)
    if not entry:
        return 0.0
    return _SCORE_ADJUST.get(entry.get("tag", ""), 0.0)


def label(name: str, annotations: dict[str, dict]) -> str:
    """Short display string for the annotation column ('' if none)."""
    entry = annotations.get(name)
    if not entry:
        return ""
    tag = entry.get("tag", "")
    note = entry.get("note", "")
    if tag and note:
        return f"{tag}: {note}"
    return tag or note
