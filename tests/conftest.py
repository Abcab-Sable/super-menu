"""Shared pytest setup: tests must never touch the network.

``SUPER_MENU_OFFLINE`` short-circuits the OSM road-underlay fetch in
``core/roads.py`` (tests that exercise the fetch path inject a fake fetcher and
manage the flag themselves). Standalone runners (``python tests/test_x.py``)
don't load conftest — network-sensitive test modules set the same default at
import time.

The routing engine is selected separately: ``plugin.active_adapter()`` reads
``VALHALLA_URL`` / ``ORS_API_KEY`` from the environment, so a developer or CI box
that has either exported (the deploy README sets ``VALHALLA_URL``) would push the
suite onto a live engine — breaking ``engine == "offline-estimate"`` assertions
and firing real geocode/route calls. Clear them so the suite is deterministic and
offline regardless of the ambient shell (tests that need a specific engine
monkeypatch it, which restores afterwards).
"""
import os

os.environ.setdefault("SUPER_MENU_OFFLINE", "1")
os.environ.pop("VALHALLA_URL", None)
os.environ.pop("ORS_API_KEY", None)
