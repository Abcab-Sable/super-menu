"""Shared pytest setup: tests must never touch the network.

``SUPER_MENU_OFFLINE`` short-circuits the OSM road-underlay fetch in
``core/roads.py`` (tests that exercise the fetch path inject a fake fetcher and
manage the flag themselves). Standalone runners (``python tests/test_x.py``)
don't load conftest — network-sensitive test modules set the same default at
import time.
"""
import os

os.environ.setdefault("SUPER_MENU_OFFLINE", "1")
