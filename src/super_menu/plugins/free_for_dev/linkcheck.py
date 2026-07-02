"""Concurrent URL health checker for free-for-dev entries.

Stdlib only (``urllib`` + ``concurrent.futures``), matching :mod:`fetch`'s
approach — no third-party HTTP client. Each entry URL is probed with HEAD
(falling back to GET when a CDN rejects HEAD) and classified into a small closed
set of statuses. Results are persisted per URL so partial runs (``limit=100``)
accumulate coverage across invocations rather than overwriting each other.

Storage: ``plugin_data_dir("free-for-dev") / "link_checks.json"``::

    { "https://svc.example": {"status": "ok", "code": 200,
                              "final_url": "", "checked_at": "2026-07-02T..."} }

The single-URL probe goes through :func:`_urlopen`, which calls
``urllib.request`` by attribute so tests can monkeypatch the network away.
"""
from __future__ import annotations

import json
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from super_menu.core.config import plugin_data_dir

PLUGIN_ID = "free-for-dev"
_UA = "super-menu/0.1"

# Statuses that mean "this entry needs a human to look at it".
PROBLEM_STATUSES = frozenset({"client_error", "server_error", "unreachable"})
# Statuses that count as a *confirmed* dead link for the url_404 flag tier: an
# actual HTTP 4xx response. ``unreachable`` (a single timeout / DNS hiccup /
# refused connection) is deliberately excluded — one failed probe is transient,
# not proof the link is dead, so it must never auto-file or verify the
# highest-confidence, ``critical``-severity flag. Such links still surface in the
# check-links table as problems; they just can't become url_404 flags on one try.
DEAD_LINK_STATUSES = frozenset({"client_error"})

# Display/sort priority: problems first, redirects next, healthy last.
_STATUS_RANK = {
    "unreachable": 0,
    "server_error": 1,
    "client_error": 2,
    "redirect": 3,
    "ok_throttled": 4,
    "ok": 5,
}


def _path() -> Path:
    return plugin_data_dir(PLUGIN_ID) / "link_checks.json"


def load_checks() -> dict[str, dict]:
    """Return the per-URL check store, or ``{}`` if none/unreadable."""
    path = _path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save_checks(checks: dict[str, dict]) -> None:
    _path().write_text(
        json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def merge_checks(results: list[dict]) -> dict[str, dict]:
    """Fold fresh ``results`` into the stored map and persist it.

    Keyed by the *requested* URL so re-checking updates in place; the merge (not
    overwrite) is what lets successive ``limit``-capped runs accumulate coverage.
    Returns the updated store.
    """
    store = load_checks()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for r in results:
        store[r["url"]] = {
            "status": r["status"],
            "code": r["code"],
            "final_url": r["final_url"],
            "checked_at": now,
        }
    save_checks(store)
    return store


def status_rank(status: str) -> int:
    """Sort key: lower = more urgent. Unknown statuses sort as healthy."""
    return _STATUS_RANK.get(status, 99)


def _split_fragment(url: str) -> tuple[str, str]:
    """Drop the ``#anchor`` before requesting; keep it for display."""
    base, _, frag = url.partition("#")
    return base, frag


def classify(code: int | None, requested: str, final: str) -> str:
    """Map an HTTP status code (+ final URL) to a closed status vocabulary."""
    if code is None:
        return "unreachable"
    if code == 429:
        # Rate limiting is not a broken link — never flag it as one.
        return "ok_throttled"
    if 300 <= code < 400:
        return "redirect"
    if 400 <= code < 500:
        return "client_error"
    if 500 <= code < 600:
        return "server_error"
    if 200 <= code < 300:
        # urllib follows redirects transparently, so a moved URL surfaces as 2xx
        # with a different final URL. Treat that as a redirect worth surfacing.
        if final and _split_fragment(final)[0] != requested:
            return "redirect"
        return "ok"
    return "unreachable"


def _urlopen(url: str, method: str, timeout: float):
    req = urllib.request.Request(url, method=method, headers={"User-Agent": _UA})
    # noqa: S310 — entry URLs come from the trusted free-for-dev catalog.
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310


def check_one(url: str, timeout: float = 10.0) -> dict:
    """Probe a single URL. Returns ``{status, code, final_url, note}``.

    HEAD first; a 403/405 (common HEAD rejection) retries once with GET. Any
    connection-level failure (DNS, timeout, refused) is ``unreachable``.
    """
    clean, _frag = _split_fragment(url)
    # Guard non-HTTP(S) or empty URLs (parse artifacts, mailto:, relative links)
    # before urllib turns them into a ValueError that would kill the whole batch.
    if not clean.lower().startswith(("http://", "https://")):
        return {"status": "unreachable", "code": None, "final_url": "",
                "note": "not an http(s) url"}
    try:
        try:
            resp = _urlopen(clean, "HEAD", timeout)
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 405):
                resp = _urlopen(clean, "GET", timeout)
            else:
                raise
        with resp:
            code = getattr(resp, "status", None)
            if code is None:
                code = resp.getcode()
            final = getattr(resp, "url", "") or clean
    except urllib.error.HTTPError as exc:
        status = classify(exc.code, clean, getattr(exc, "url", "") or clean)
        return {"status": status, "code": exc.code, "final_url": "",
                "note": str(getattr(exc, "reason", "") or "")}
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError,
            ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        return {"status": "unreachable", "code": None, "final_url": "",
                "note": str(reason)}

    status = classify(code, clean, final)
    display_final = final if _split_fragment(final)[0] != clean else ""
    return {"status": status, "code": code, "final_url": display_final, "note": ""}


def check_many(items: list[tuple[str, str]], timeout: float = 10.0,
               max_workers: int = 8, per_host: int = 2) -> list[dict]:
    """Probe ``(name, url)`` pairs concurrently, politely (≤ per_host per host).

    Returns one result dict per item: ``{name, url, status, code, final_url,
    note}``. Order is arbitrary (completion order) — callers sort for display.
    """
    host_sems: dict[str, threading.Semaphore] = {}
    sems_lock = threading.Lock()

    def _host_sem(host: str) -> threading.Semaphore:
        with sems_lock:
            return host_sems.setdefault(host, threading.Semaphore(per_host))

    def _work(name: str, url: str) -> dict:
        host = urllib.parse.urlparse(url).hostname or ""
        sem = _host_sem(host)
        with sem:
            res = check_one(url, timeout)
        return {"name": name, "url": url, **res}

    results: list[dict] = []
    if not items:
        return results
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_work, name, url) for name, url in items]
        for fut in as_completed(futures):
            results.append(fut.result())
    return results
