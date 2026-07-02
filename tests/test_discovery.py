"""Tests for free-for-dev smart discovery: ranking, synonyms, suggest,
analyze-architecture, and the annotation overlay.

Self-contained: a hand-written fixture index (no network, no real cache) and an
isolated SUPER_MENU_HOME so annotations never touch the user's real data dir.
Runnable standalone: ``uv run python tests/test_discovery.py``.
"""
import os
import tempfile

# Isolate annotation storage BEFORE importing the plugin package.
os.environ["SUPER_MENU_HOME"] = tempfile.mkdtemp(prefix="super-menu-test-")

from super_menu.plugins.free_for_dev import search, annotations, plugin

# ~15 hand-written entries across a handful of categories.
FIXTURE = [
    {"name": "Auth0", "category": "Authentication",
     "url": "https://auth0.example", "description": "Identity platform, SSO and OAuth."},
    {"name": "Okta", "category": "Authentication",
     "url": "https://okta.example", "description": "Enterprise identity and single sign-on."},
    {"name": "Keycloak", "category": "Authentication",
     "url": "https://keycloak.example", "description": "Open-source identity and access management."},
    {"name": "FusionAuth", "category": "Authentication",
     "url": "https://fusionauth.example", "description": "Auth for developers, self-hosted."},
    {"name": "PostgreSQL Hosting Co", "category": "Database Hosting",
     "url": "https://pghost.example", "description": "Managed Postgres clusters."},
    {"name": "ElephantDB", "category": "Database Hosting",
     "url": "https://elephantdb.example", "description": "Hosted database with a postgresql backend."},
    {"name": "MongoBox", "category": "Database Hosting",
     "url": "https://mongobox.example", "description": "NoSQL document database hosting."},
    {"name": "BuildFast", "category": "CI and CD",
     "url": "https://buildfast.example", "description": "Continuous integration pipelines."},
    {"name": "ShipIt", "category": "CI and CD",
     "url": "https://shipit.example", "description": "CD and deployment automation."},
    {"name": "SwiftEdge", "category": "CDN",
     "url": "https://swiftedge.example", "description": "Global content delivery network."},
    {"name": "PixelCDN", "category": "CDN",
     "url": "https://pixelcdn.example", "description": "Image and asset delivery network."},
    {"name": "MailGrid", "category": "Email",
     "url": "https://mailgrid.example", "description": "Transactional email and SMTP relay."},
    {"name": "Notes", "category": "Productivity",
     "url": "https://notes.example", "description": "Simple note-taking app."},
    {"name": "MetricHub", "category": "Monitoring",
     "url": "https://metrichub.example", "description": "Metrics and observability dashboards."},
    {"name": "QueueRunner", "category": "Messaging",
     "url": "https://queuerunner.example", "description": "Managed message broker and pubsub."},
]

# Route command handlers at the fixture instead of the real index.
plugin._entries = lambda: FIXTURE


def _reset_annotations():
    annotations.save({})


# --- search ranking -------------------------------------------------------

def test_exact_name_outranks_description():
    ranked = search.search(FIXTURE, "PostgreSQL")
    names = [e["name"] for _, e in ranked]
    assert names[0] == "PostgreSQL Hosting Co", names
    # ElephantDB only mentions postgresql in its description — must rank lower.
    assert names.index("PostgreSQL Hosting Co") < names.index("ElephantDB")


def test_synonym_query_finds_entry():
    # "postgres" must reach "PostgreSQL Hosting Co" via the synonym map.
    names = [e["name"] for _, e in search.search(FIXTURE, "postgres")]
    assert "PostgreSQL Hosting Co" in names


def test_regression_substring_matches_still_returned():
    """Every entry the old substring _matches would hit is still returned."""
    def old_matches(entry, q):
        q = q.lower()
        return (q in entry["name"].lower() or q in entry["description"].lower()
                or q in entry["category"].lower())

    for query in ["postgres", "identity", "database", "delivery", "auth0",
                  "email", "free", "tier"]:
        old = {e["name"] for e in FIXTURE if old_matches(e, query)}
        new = {e["name"] for _, e in search.search(FIXTURE, query, limit=0)}
        assert old <= new, f"query {query!r}: lost {old - new}"


def test_stopword_only_query_falls_back_to_substring():
    # "and" tokenizes to nothing (stopword) — the old substring search returned
    # everything containing it, so the fallback must too, not zero rows.
    names = {e["name"] for _, e in search.search(FIXTURE, "and", limit=0)}
    expected = {
        e["name"] for e in FIXTURE
        if "and" in f"{e['name']} {e['category']} {e['description']}".lower()
    }
    assert names == expected and names, names


# --- suggest-alternatives -------------------------------------------------

def test_suggest_anchor_category_resolution():
    _reset_annotations()
    res = plugin.cmd_suggest_alternatives("Auth0")
    assert res.ok
    names = [r["name"] for r in res.data]
    assert "Auth0" not in names  # the named tech is excluded
    assert set(names) == {"Okta", "Keycloak", "FusionAuth"}
    assert "Authentication" in res.summary


def test_suggest_unknown_technology_errs():
    res = plugin.cmd_suggest_alternatives("Nonexistotron9000")
    assert not res.ok
    assert "category" in res.summary  # hint to pass the override


def test_category_override_does_not_overexclude_generic_name():
    # Explicit override with a generic anchor ("auth") must not substring-drop
    # every provider whose name contains it (FusionAuth, Auth0, …).
    _reset_annotations()
    res = plugin.cmd_suggest_alternatives("auth", category="Authentication")
    assert res.ok
    names = {r["name"] for r in res.data}
    assert {"FusionAuth", "Auth0", "Okta", "Keycloak"} <= names, names


def test_suggest_criteria_reranks():
    _reset_annotations()
    base = [r["name"] for r in plugin.cmd_suggest_alternatives("Auth0").data]
    crit = [r["name"] for r in plugin.cmd_suggest_alternatives("Auth0", criteria="open-source").data]
    assert base != crit, (base, crit)
    # Keycloak is the only open-source one → it should lead with that criteria.
    assert crit[0] == "Keycloak", crit


# --- analyze-architecture -------------------------------------------------

def test_analyze_detects_known_ignores_unknown_and_common_words():
    _reset_annotations()
    doc = (
        "Our stack uses Auth0 for login, Keycloak as a fallback, and BuildFast "
        "for CI. We also evaluated Oracle Exadata. See the Notes doc for details."
    )
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(doc)
        path = fh.name
    try:
        res = plugin.cmd_analyze_architecture(path)
    finally:
        os.unlink(path)

    assert res.ok
    techs = {r["technology"] for r in res.data}
    assert {"Auth0", "Keycloak", "BuildFast"} <= techs
    assert "Oracle Exadata" not in techs  # unknown → not in index
    assert "Notes" not in techs           # on the ignore-list
    # Each row carries a joined string column and the full nested detail.
    row = next(r for r in res.data if r["technology"] == "Auth0")
    assert isinstance(row["alternatives"], str)
    assert isinstance(row["alternatives_detail"], list)


def test_analyze_ignores_generic_synonym_words():
    # Generic prose ("email", "metrics", "queue") are synonym keys but NOT
    # abbreviations — they must not fabricate a detected technology.
    doc = "We send email alerts and track metrics for every queue."
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(doc)
        path = fh.name
    try:
        res = plugin.cmd_analyze_architecture(path)
    finally:
        os.unlink(path)
    assert res.ok
    assert res.data == [], res.data


# --- annotations ----------------------------------------------------------

def test_annotation_shows_in_search_column():
    _reset_annotations()
    plugin.cmd_annotate("Auth0", tag="star", note="we use this")
    res = plugin.cmd_search("Auth0")
    row = next(r for r in res.data if r["name"] == "Auth0")
    assert "annotation" in row
    assert row["annotation"] and "star" in row["annotation"]


def test_annotate_resolves_case_insensitively():
    _reset_annotations()
    res = plugin.cmd_annotate("auth0", tag="star", note="we use this")
    assert res.ok and res.data["name"] == "Auth0", res.data
    shown = plugin.cmd_search("Auth0").data
    row = next(r for r in shown if r["name"] == "Auth0")
    assert row["annotation"], row  # nudge is visible because it landed on canonical
    _reset_annotations()


def test_annotate_unknown_name_errs_with_suggestion():
    _reset_annotations()
    res = plugin.cmd_annotate("Okt", tag="star")
    assert not res.ok
    assert "Okta" in res.summary, res.summary


def test_annotate_clear_orphan_name_ok():
    # Clearing bypasses resolution so a stale annotation on a renamed entry
    # stays deletable even though the name no longer resolves.
    _reset_annotations()
    res = plugin.cmd_annotate("GhostServiceXYZ")
    assert res.ok, res.summary


def test_avoid_tag_sinks_rank():
    _reset_annotations()
    before = [r["name"] for r in plugin.cmd_search("identity").data]
    assert "Auth0" in before
    plugin.cmd_annotate("Auth0", tag="avoid", note="pricing")
    after = [r["name"] for r in plugin.cmd_search("identity").data]
    # Auth0 must fall behind the entries it previously tied with.
    assert after.index("Auth0") > before.index("Auth0"), (before, after)
    assert after[-1] == "Auth0", after
    _reset_annotations()


if __name__ == "__main__":
    test_exact_name_outranks_description()
    test_synonym_query_finds_entry()
    test_regression_substring_matches_still_returned()
    test_stopword_only_query_falls_back_to_substring()
    test_suggest_anchor_category_resolution()
    test_suggest_unknown_technology_errs()
    test_category_override_does_not_overexclude_generic_name()
    test_suggest_criteria_reranks()
    test_analyze_detects_known_ignores_unknown_and_common_words()
    test_analyze_ignores_generic_synonym_words()
    test_annotation_shows_in_search_column()
    test_annotate_resolves_case_insensitively()
    test_annotate_unknown_name_errs_with_suggestion()
    test_annotate_clear_orphan_name_ok()
    test_avoid_tag_sinks_rank()
    print("all discovery tests passed")
