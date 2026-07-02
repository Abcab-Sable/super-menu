"""Freshness flag store for free-for-dev entries.

A flag records an *objective, verifiable* reason an entry may be stale — never a
subjective "this service is bad". That constraint is made structural, not
aspirational, by a **closed ``reason_type`` vocabulary** (:data:`REASON_TYPES`):
there is simply no reason_type for an opinion. Two further rules fall out of the
same table:

* **Evidence** — most reason_types require an ``evidence_url``; the two that are
  machine-verifiable (``url_404`` via linkcheck, ``category_mismatch`` by
  inspection) do not.
* **Confidence** — derived from the table, *never* accepted from the caller. A
  flag filed against an unverified pricing complaint cannot claim 0.95.

Records are append-and-amend (dismiss sets ``status``; nothing is deleted) so the
store doubles as an audit trail.

Storage: ``plugin_data_dir("free-for-dev") / "flags.json"`` — a JSON list.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from super_menu.core.config import plugin_data_dir

PLUGIN_ID = "free-for-dev"

SEVERITIES = ["critical", "warning", "info"]
STATUSES = ["pending_review", "dismissed", "actioned"]

# The closed vocabulary. ``evidence`` = an evidence_url is required to file it;
# ``confidence`` = the fixed, caller-independent confidence for that reason type.
#
# To extend: add a row here (and, if it is machine-verifiable, teach the command
# how to verify it — see ``url_404`` in plugin.cmd_flag_entry). Do NOT add a
# reason_type for a subjective judgement; that is the whole point of the closure.
REASON_TYPES: dict[str, dict] = {
    "url_404": {"evidence": False, "confidence": 0.95},           # auto-verified
    "service_discontinued": {"evidence": True, "confidence": 0.80},
    "free_tier_removed": {"evidence": True, "confidence": 0.75},
    "pricing_changed": {"evidence": True, "confidence": 0.70},
    "category_mismatch": {"evidence": False, "confidence": 0.60},
}

MAX_REASON_LEN = 200


def reason_types() -> list[str]:
    return list(REASON_TYPES)


def evidence_required(reason_type: str) -> bool:
    return REASON_TYPES[reason_type]["evidence"]


def confidence_for(reason_type: str) -> float:
    """The fixed confidence for a reason type. Callers cannot override this."""
    return REASON_TYPES[reason_type]["confidence"]


def _path() -> Path:
    return plugin_data_dir(PLUGIN_ID) / "flags.json"


def load() -> list[dict]:
    """Return the flag list, or ``[]`` if none/unreadable."""
    path = _path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def save(flags: list[dict]) -> None:
    _path().write_text(
        json.dumps(flags, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def find_pending(entry: str, reason_type: str) -> dict | None:
    """The existing pending_review flag for this (entry, reason_type), if any."""
    for f in load():
        if (f.get("entry") == entry and f.get("reason_type") == reason_type
                and f.get("status") == "pending_review"):
            return f
    return None


def build(entry: str, reason_type: str, reason: str, severity: str,
          evidence_url: str | None) -> dict:
    """Construct a flag record (with table-derived confidence) without saving.

    Split out so a bulk filer (``check-links --flag_broken``) can build many
    records and persist them in a single write instead of one per flag.
    """
    return {
        "entry": entry,
        "reason_type": reason_type,
        "reason": reason,
        "severity": severity,
        "evidence_url": evidence_url or "",
        "flagged_by": "claude-code",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "pending_review",
        "confidence": confidence_for(reason_type),  # derived, not caller-supplied
    }


def add(entry: str, reason_type: str, reason: str, severity: str,
        evidence_url: str | None) -> dict:
    """Append a single flag with table-derived confidence. No validation here —
    the command validates the vocabulary/evidence/dedupe rules before calling."""
    record = build(entry, reason_type, reason, severity, evidence_url)
    flags = load()
    flags.append(record)
    save(flags)
    return record


def dismiss(entry: str, reason_type: str) -> dict | None:
    """Set the pending flag for (entry, reason_type) to ``dismissed``.

    Returns the updated record, or ``None`` if there was no pending match.
    Operates on stored records only, so an orphaned/renamed entry stays
    dismissable.
    """
    flags = load()
    for f in flags:
        if (f.get("entry") == entry and f.get("reason_type") == reason_type
                and f.get("status") == "pending_review"):
            f["status"] = "dismissed"
            f["dismissed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            save(flags)
            return f
    return None


def pending_entries() -> set[str]:
    """Entry names that carry at least one pending_review flag."""
    return {f["entry"] for f in load()
            if f.get("status") == "pending_review" and f.get("entry")}
