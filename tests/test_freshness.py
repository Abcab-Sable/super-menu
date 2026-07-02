"""Tests for free-for-dev freshness signals: link classification, the
check-store merge, and flag validation (closed vocabulary, evidence rule,
derived confidence, url_404 machine verification).

Self-contained: an isolated SUPER_MENU_HOME (so the flag/link stores never touch
the user's real data dir), a hand-written fixture index, and a fake urlopen — no
live network. Runnable standalone: ``uv run python tests/test_freshness.py``.
"""
import os
import tempfile
import urllib.error
import urllib.request

# Isolate all on-disk stores BEFORE importing the plugin package.
os.environ["SUPER_MENU_HOME"] = tempfile.mkdtemp(prefix="super-menu-test-")

from super_menu.plugins.free_for_dev import linkcheck, flags, plugin

FIXTURE = [
    {"name": "Auth0", "category": "Authentication",
     "url": "https://auth0.example", "description": "Identity platform."},
    {"name": "Okta", "category": "Authentication",
     "url": "https://okta.example", "description": "Enterprise identity."},
    {"name": "MailGrid", "category": "Email",
     "url": "https://mailgrid.example", "description": "Transactional email."},
]

plugin._entries = lambda: FIXTURE


def _reset():
    flags.save([])
    linkcheck.save_checks({})


# --- a monkeypatchable fake network ---------------------------------------

class _FakeResp:
    def __init__(self, status, url):
        self.status = status
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def getcode(self):
        return self.status


class _fake_net:
    """Context manager swapping ``urllib.request.urlopen`` for ``fn(url, method)``."""

    def __init__(self, fn):
        self.fn = fn
        self._orig = None

    def __enter__(self):
        self._orig = urllib.request.urlopen

        def _urlopen(req, timeout=None):
            return self.fn(req.full_url, req.get_method())

        urllib.request.urlopen = _urlopen
        return self

    def __exit__(self, *a):
        urllib.request.urlopen = self._orig
        return False


def _http_error(url, code):
    return urllib.error.HTTPError(url, code, f"HTTP {code}", {}, None)


# --- classify (pure) ------------------------------------------------------

def test_classify_buckets():
    u = "https://svc.example"
    assert linkcheck.classify(200, u, u) == "ok"
    assert linkcheck.classify(200, u, u + "/moved") == "redirect"  # 2xx, moved
    assert linkcheck.classify(301, u, u) == "redirect"
    assert linkcheck.classify(404, u, u) == "client_error"
    assert linkcheck.classify(503, u, u) == "server_error"
    assert linkcheck.classify(429, u, u) == "ok_throttled"
    assert linkcheck.classify(None, u, u) == "unreachable"


# --- check_one against the fake opener ------------------------------------

def test_check_one_ok():
    with _fake_net(lambda url, m: _FakeResp(200, url)):
        r = linkcheck.check_one("https://svc.example")
    assert r["status"] == "ok" and r["code"] == 200


def test_check_one_redirect_followed():
    # urllib follows a 301 to a 200 at a new URL: 2xx with a different final URL.
    with _fake_net(lambda url, m: _FakeResp(200, "https://svc.example/new")):
        r = linkcheck.check_one("https://svc.example")
    assert r["status"] == "redirect"
    assert r["final_url"] == "https://svc.example/new"


def test_check_one_404():
    def fn(url, m):
        raise _http_error(url, 404)

    with _fake_net(fn):
        r = linkcheck.check_one("https://svc.example")
    assert r["status"] == "client_error" and r["code"] == 404


def test_check_one_timeout_is_unreachable():
    def fn(url, m):
        raise urllib.error.URLError("timed out")

    with _fake_net(fn):
        r = linkcheck.check_one("https://svc.example")
    assert r["status"] == "unreachable" and r["code"] is None


def test_check_one_429_is_not_broken():
    def fn(url, m):
        raise _http_error(url, 429)

    with _fake_net(fn):
        r = linkcheck.check_one("https://svc.example")
    assert r["status"] == "ok_throttled"


def test_check_one_head_405_retries_get():
    def fn(url, method):
        if method == "HEAD":
            raise _http_error(url, 405)
        return _FakeResp(200, url)

    with _fake_net(fn):
        r = linkcheck.check_one("https://svc.example")
    assert r["status"] == "ok" and r["code"] == 200


# --- store merge semantics ------------------------------------------------

def test_merge_accumulates_across_runs():
    _reset()
    linkcheck.merge_checks([
        {"url": "https://a.example", "status": "ok", "code": 200, "final_url": ""},
    ])
    linkcheck.merge_checks([
        {"url": "https://b.example", "status": "client_error", "code": 404,
         "final_url": ""},
    ])
    store = linkcheck.load_checks()
    assert set(store) == {"https://a.example", "https://b.example"}
    assert store["https://b.example"]["status"] == "client_error"
    assert "checked_at" in store["https://a.example"]


# --- flag validation ------------------------------------------------------

def test_flag_unknown_reason_type_rejected():
    _reset()
    res = plugin.cmd_flag_entry("Auth0", "service_is_bad", "I dislike it")
    assert not res.ok and "unknown reason_type" in res.summary


def test_flag_missing_evidence_rejected():
    _reset()
    res = plugin.cmd_flag_entry("Auth0", "pricing_changed", "went up")
    assert not res.ok and "evidence_url" in res.summary


def test_flag_confidence_is_derived_from_table():
    _reset()
    res = plugin.cmd_flag_entry("Auth0", "pricing_changed", "went up",
                                evidence_url="https://blog.example/pricing")
    assert res.ok
    # Confidence is not a parameter — it comes from the reason_type table.
    assert res.data["confidence"] == flags.confidence_for("pricing_changed") == 0.70


def test_flag_unknown_entry_rejected():
    _reset()
    res = plugin.cmd_flag_entry("Nonexistotron", "category_mismatch", "wrong bucket")
    assert not res.ok and "no entry named" in res.summary


def test_duplicate_pending_flag_rejected():
    _reset()
    ok1 = plugin.cmd_flag_entry("Auth0", "category_mismatch", "wrong bucket")
    assert ok1.ok
    dup = plugin.cmd_flag_entry("Auth0", "category_mismatch", "still wrong")
    assert not dup.ok and "already exists" in dup.summary


# --- url_404 machine verification -----------------------------------------

def test_url_404_without_record_triggers_recheck_and_files():
    _reset()
    # No stored linkcheck record → the flag command must re-probe the URL.
    with _fake_net(lambda url, m: (_ for _ in ()).throw(_http_error(url, 404))):
        res = plugin.cmd_flag_entry("Auth0", "url_404", "link dead")
    assert res.ok, res.summary
    assert res.data["confidence"] == 0.95
    # The re-check result was merged into the store.
    assert linkcheck.load_checks()["https://auth0.example"]["status"] == "client_error"


def test_url_404_rejected_when_url_is_live():
    _reset()
    with _fake_net(lambda url, m: _FakeResp(200, url)):
        res = plugin.cmd_flag_entry("Auth0", "url_404", "link dead")
    assert not res.ok and "did not return an HTTP 4xx" in res.summary


def _unreachable(url, method):
    raise urllib.error.URLError("timed out")


def test_url_404_rejected_on_transient_unreachable():
    # A one-off connection failure is not a confirmed dead link — url_404 needs
    # an actual 4xx, so a transient unreachable must be rejected, not filed.
    _reset()
    with _fake_net(_unreachable):
        res = plugin.cmd_flag_entry("Auth0", "url_404", "link dead")
    assert not res.ok and "did not return an HTTP 4xx" in res.summary
    assert flags.load() == []


def test_unreachable_not_auto_flagged():
    # check-links --flag_broken must not permanently flag a flaky/unreachable
    # probe as a critical url_404; only real 4xx responses qualify.
    _reset()
    with _fake_net(_unreachable):
        res = plugin.cmd_check_links(limit=10, flag_broken=True)
    assert "filed 0 url_404 flag(s)" in res.summary
    assert flags.load() == []


# --- wire-up: pending flag surfaces in search + flags listing --------------

def test_pending_flag_marks_search_row():
    _reset()
    plugin.cmd_flag_entry("Auth0", "category_mismatch", "wrong bucket")
    row = next(r for r in plugin.cmd_search("Auth0").data if r["name"] == "Auth0")
    assert "⚑" in row["annotation"], row


def _always_404(url, method):
    raise _http_error(url, 404)


def test_check_links_flag_broken_files_once_and_dedupes():
    _reset()
    with _fake_net(_always_404):
        res = plugin.cmd_check_links(limit=10, flag_broken=True)
    assert res.ok and "filed 3 url_404 flag(s)" in res.summary
    filed = flags.load()
    assert {f["entry"] for f in filed} == {"Auth0", "Okta", "MailGrid"}
    assert all(f["reason_type"] == "url_404" and f["confidence"] == 0.95
               for f in filed)
    # A second run must not duplicate the pending flags.
    with _fake_net(_always_404):
        again = plugin.cmd_check_links(limit=10, flag_broken=True)
    assert "filed 0 url_404 flag(s)" in again.summary
    assert len(flags.load()) == 3


def test_throttled_counted_separately_and_surfaced():
    # A 429 is throttled/unknown, not healthy: it must be counted on its own and
    # shown in the default (problems) table rather than folded into "ok".
    _reset()
    with _fake_net(lambda url, m: _http_error(url, 429)):
        res = plugin.cmd_check_links(limit=10)
    assert "3 throttled" in res.summary and "0 ok" in res.summary, res.summary
    assert {r["status"] for r in res.data} == {"ok_throttled"}, res.data


def test_check_links_refreshes_oldest_first():
    # Once every URL is checked, re-runs must re-probe the *stalest* URL first
    # (oldest checked_at), not the same head of the index every time.
    _reset()
    linkcheck.save_checks({
        "https://auth0.example":
            {"status": "ok", "code": 200, "final_url": "",
             "checked_at": "2026-01-03T00:00:00+00:00"},
        "https://okta.example":
            {"status": "ok", "code": 200, "final_url": "",
             "checked_at": "2026-01-02T00:00:00+00:00"},
        "https://mailgrid.example":
            {"status": "ok", "code": 200, "final_url": "",
             "checked_at": "2026-01-01T00:00:00+00:00"},  # oldest
    })
    captured = {}
    orig = linkcheck.check_many

    def _capture(items, **kw):
        captured["items"] = items
        return []

    linkcheck.check_many = _capture
    try:
        plugin.cmd_check_links(limit=1)
    finally:
        linkcheck.check_many = orig
    assert [u for _, u in captured["items"]] == ["https://mailgrid.example"], captured


def test_dismiss_flag_updates_status():
    _reset()
    plugin.cmd_flag_entry("Auth0", "category_mismatch", "wrong bucket")
    res = plugin.cmd_dismiss_flag("Auth0", "category_mismatch")
    assert res.ok and res.data["status"] == "dismissed"
    # A dismissed flag no longer blocks a fresh one of the same type.
    again = plugin.cmd_flag_entry("Auth0", "category_mismatch", "wrong again")
    assert again.ok


if __name__ == "__main__":
    test_classify_buckets()
    test_check_one_ok()
    test_check_one_redirect_followed()
    test_check_one_404()
    test_check_one_timeout_is_unreachable()
    test_check_one_429_is_not_broken()
    test_check_one_head_405_retries_get()
    test_merge_accumulates_across_runs()
    test_flag_unknown_reason_type_rejected()
    test_flag_missing_evidence_rejected()
    test_flag_confidence_is_derived_from_table()
    test_flag_unknown_entry_rejected()
    test_duplicate_pending_flag_rejected()
    test_url_404_without_record_triggers_recheck_and_files()
    test_url_404_rejected_when_url_is_live()
    test_url_404_rejected_on_transient_unreachable()
    test_unreachable_not_auto_flagged()
    test_pending_flag_marks_search_row()
    test_check_links_flag_broken_files_once_and_dedupes()
    test_throttled_counted_separately_and_surfaced()
    test_check_links_refreshes_oldest_first()
    test_dismiss_flag_updates_status()
    print("all freshness tests passed")
