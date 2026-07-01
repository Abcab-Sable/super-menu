"""Plugin discovery.

Every subpackage of ``super_menu.plugins`` that exposes a top-level ``PLUGIN``
instance (or a ``get_plugin()`` factory) is auto-registered. Dropping a new
folder in ``plugins/`` is all it takes to extend the menu.
"""
from __future__ import annotations

import importlib
import pkgutil
from typing import Optional

from .plugin import Plugin

import super_menu.plugins as _plugins_pkg


def discover_plugins() -> list[Plugin]:
    found: list[Plugin] = []
    for mod_info in pkgutil.iter_modules(_plugins_pkg.__path__):
        if not mod_info.ispkg:
            continue
        module = importlib.import_module(f"super_menu.plugins.{mod_info.name}")
        instance = getattr(module, "PLUGIN", None)
        if instance is None:
            factory = getattr(module, "get_plugin", None)
            if callable(factory):
                instance = factory()
        if isinstance(instance, Plugin):
            found.append(instance)
    found.sort(key=lambda p: p.name.lower())
    return found


class Registry:
    """In-memory index of discovered plugins."""

    def __init__(self, plugins: Optional[list[Plugin]] = None):
        self._plugins = plugins if plugins is not None else discover_plugins()
        self._by_id = {p.id: p for p in self._plugins}

    @property
    def plugins(self) -> list[Plugin]:
        return list(self._plugins)

    def get(self, plugin_id: str) -> Optional[Plugin]:
        return self._by_id.get(plugin_id)


_default: Optional[Registry] = None


def default_registry() -> Registry:
    global _default
    if _default is None:
        _default = Registry()
    return _default
