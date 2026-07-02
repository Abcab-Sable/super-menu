"""Ranked multi-term search over the free-for-dev index.

Replaces raw substring matching (the old ``plugin._matches``) with tokenized,
weighted, ranked search. No embeddings — Claude Code rephrases queries; the
plugin just needs decent lexical recall and a sensible ranking. For ~1.5k short
entries a hand-tuned additive score beats any IDF machinery, and stays legible.

Behavioral contract: any query the old substring search matched must still
match here. Token-substring scoring (weight 2.0) covers that; results merely
gain ordering.
"""
from __future__ import annotations

import re

# Words carried by almost every entry — they add noise, not signal. Kept small
# on purpose: a stopword that is also a meaningful tech term (e.g. "go") would
# silently drop recall, so only include words that are never the thing searched.
_STOPWORDS = {
    "a", "an", "and", "the", "for", "of", "to", "with", "in", "on", "or",
    "your", "you", "is", "are", "as", "at", "by", "from", "free", "tier",
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Query synonyms / expansions. Seeded small and honest; grow it from real
# misses (a query that should have hit an entry but didn't). Keys and values are
# already-tokenized lowercase words. A value may be several words ("continuous
# integration") — each is added to the expanded token set independently.
#
# To extend: add `"<query token>": {"<extra>", "<extra>"}`. Move this to a data
# file only once it grows past ~50 lines; until then a module dict is simplest.
SYNONYMS: dict[str, set[str]] = {
    "postgres": {"postgresql"},
    "postgresql": {"postgres"},
    "psql": {"postgres", "postgresql"},
    "db": {"database"},
    "database": {"db"},
    "auth": {"authentication", "authorization", "sso", "oauth", "oidc"},
    "authn": {"authentication"},
    "authz": {"authorization"},
    "sso": {"auth", "authentication", "saml"},
    "k8s": {"kubernetes"},
    "kubernetes": {"k8s"},
    "ci": {"continuous", "integration"},
    "cd": {"continuous", "delivery", "deployment"},
    "cicd": {"ci", "cd", "continuous", "integration", "delivery"},
    "vm": {"virtual", "machine"},
    "email": {"smtp", "mail"},
    "mail": {"email", "smtp"},
    "queue": {"messaging", "broker", "pubsub"},
    "cron": {"scheduler", "scheduled", "jobs"},
    "logs": {"logging", "observability"},
    "metrics": {"monitoring", "observability"},
    "cdn": {"content", "delivery", "network"},
}

# Scoring weights (see plan 02). Additive: an entry's score is the sum over
# every (query token, entry field) hit. Tuned by hand; adjust here, not inline.
_W_NAME_EXACT = 3.0   # query token equals a whole token of the entry name
_W_NAME_SUB = 2.0     # query token appears as a substring of the name
_W_CAT_EXACT = 1.5    # query token equals a whole token of the category
_W_DESC = 1.0         # query token appears in the description
_W_AND_BONUS = 2.0    # every original query token matched somewhere


def tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumerics, drop stopwords."""
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


def expand(tokens: list[str]) -> set[str]:
    """Union of the tokens with their synonym expansions."""
    out = set(tokens)
    for t in tokens:
        out |= SYNONYMS.get(t, set())
    return out


def score(entry: dict, query_tokens: list[str], expanded: set[str]) -> float:
    """Additive relevance of one entry against an already-tokenized query.

    ``query_tokens`` is the original (unexpanded) token list — used for the AND
    bonus so synonyms don't get a free "everything matched". ``expanded`` is the
    synonym-widened set used for the per-field hits.
    """
    name = entry.get("name", "").lower()
    category = entry.get("category", "").lower()
    description = entry.get("description", "").lower()
    name_tokens = set(_TOKEN_RE.findall(name))
    cat_tokens = set(_TOKEN_RE.findall(category))

    total = 0.0
    for t in expanded:
        if t in name_tokens:
            total += _W_NAME_EXACT
        elif t in name:
            total += _W_NAME_SUB
        if t in cat_tokens:
            total += _W_CAT_EXACT
        if t in description:
            total += _W_DESC

    # AND bonus: reward entries that cover every original query token somewhere
    # (via the entry's own text, using synonym expansion of the entry side too).
    if query_tokens:
        haystack = f"{name} {category} {description}"
        if all(
            _token_present(t, name_tokens, cat_tokens, haystack)
            for t in query_tokens
        ):
            total += _W_AND_BONUS
    return total


def _token_present(t: str, name_tokens: set[str], cat_tokens: set[str],
                   haystack: str) -> bool:
    if t in haystack:
        return True
    # Also count a query token as present if any of its synonyms appear — keeps
    # the AND bonus in sync with the per-field scoring above.
    return any(s in haystack for s in SYNONYMS.get(t, set()))


def search(entries: list[dict], query: str, category: str | None = None,
           limit: int = 20) -> list[tuple[float, dict]]:
    """Rank ``entries`` against ``query``. Returns ``(score, entry)`` descending.

    ``category`` restricts to entries whose category contains that substring
    (case-insensitive), matching the old behavior. Entries scoring zero are
    dropped so recall equals the old substring search, not the whole index.
    """
    tokens = tokenize(query)
    cl = category.lower() if category else None

    # A query that tokenizes to nothing (all stopwords, e.g. "free" for a tool
    # literally named *free-for-dev*) would score 0 everywhere and drop every
    # row — silently breaking the substring-compat contract. Fall back to raw,
    # unranked substring matching over name/description/category in that case.
    if not tokens:
        raw = query.strip().lower()
        if not raw:
            return []
        fallback: list[tuple[float, dict]] = []
        for e in entries:
            if cl is not None and cl not in e.get("category", "").lower():
                continue
            hay = f"{e.get('name', '')} {e.get('category', '')} {e.get('description', '')}".lower()
            if raw in hay:
                fallback.append((1.0, e))
        fallback.sort(key=lambda pair: pair[1].get("name", "").lower())
        if limit and limit > 0:
            return fallback[:limit]
        return fallback

    expanded = expand(tokens)
    scored: list[tuple[float, dict]] = []
    for e in entries:
        if cl is not None and cl not in e.get("category", "").lower():
            continue
        s = score(e, tokens, expanded)
        if s > 0:
            scored.append((s, e))

    # Stable secondary sort by name keeps output deterministic across runs.
    scored.sort(key=lambda pair: (-pair[0], pair[1].get("name", "").lower()))
    if limit and limit > 0:
        return scored[:limit]
    return scored
