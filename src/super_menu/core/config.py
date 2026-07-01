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
