"""Shared paths and small config helpers."""
from __future__ import annotations

import os
from pathlib import Path


def data_home() -> Path:
    """Per-user data root for caches/indexes the menu builds at runtime."""
    base = os.environ.get("SUPER_MENU_HOME")
    if base:
        root = Path(base)
    elif os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "super-menu"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "super-menu"
    root.mkdir(parents=True, exist_ok=True)
    return root


def plugin_data_dir(plugin_id: str) -> Path:
    d = data_home() / plugin_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_dotenv(path: Path | None = None) -> dict[str, str]:
    """Load ``KEY=VALUE`` lines from a ``.env`` file into ``os.environ``.

    Zero-dependency (no python-dotenv). Looks in the current directory by default.
    An already-set environment variable wins, so a real shell export overrides the
    file. Blank lines, ``#`` comments, an optional ``export`` prefix, and quoted
    values are handled. A missing file is fine — returns ``{}``.
    """
    path = Path(path) if path else Path.cwd() / ".env"
    loaded: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return loaded
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            loaded[key] = value
            os.environ.setdefault(key, value)  # don't clobber a real export
    return loaded
