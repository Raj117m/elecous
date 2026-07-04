"""Real, evidence-based entity discovery (addresses the n=1-entity-per-
category bottleneck identified by real validation runs).

Entities are never hand-typed guesses. They come from two independent real
signals:

1. Wikipedia's own full-text search relevance ranking (``list=search``),
   which reflects real article content curated by many independent editors
   — not a list I invented.
2. A real pageview-volume check: a discovered title is only kept if it has
   genuine, non-trivial Wikipedia pageview history (spec Rule 2: every
   observation must be traceable; an entity with no real observed activity
   contributes no evidence and is discarded, not silently faked).

This keeps every added entity anchored to two real, independently
verifiable public facts before it ever enters the demand pipeline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta

from elecous.common import timeutil
from elecous.infrastructure.config import Config, active_config
from elecous.infrastructure.http import HttpClient
from elecous.infrastructure.logging import get_logger

SEARCH_URL = "https://en.wikipedia.org/w/api.php"
PAGEVIEWS_URL = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"

#: Category namespace id in MediaWiki.
CATEGORY_NAMESPACE = 14
ARTICLE_NAMESPACE = 0

#: Titles matching these patterns are Wikipedia navigation/meta pages, not
#: real product/phenomenon entities, and are discarded even if they rank in
#: search results.
_EXCLUDE_PATTERNS = re.compile(
    r"^(List of|Category:|Talk:|Template:|Wikipedia:|Portal:|Comparison of|Timeline of|History of|Index of)",
    re.IGNORECASE,
)


@dataclass
class DiscoveredEntity:
    entity: str
    article: str
    query: str  # the real search query that surfaced it (provenance)
    search_snippet: str
    median_daily_pageviews: float


class EntityDiscoveryEngine:
    """Discovers real candidate entities for a phenomenon/category from
    genuine Wikipedia search relevance and verifies them against real
    pageview history before they are trusted."""

    def __init__(self, config: Config | None = None, http: HttpClient | None = None):
        self.config = config or active_config()
        self.http = http or HttpClient(self.config)
        self.log = get_logger("expansion")

    def find_categories(self, keyword: str, limit: int = 5) -> list[str]:
        """Real, existing Wikipedia category titles matching a phenomenon
        keyword (searched in the Category namespace itself). Category
        membership is curated by many independent Wikipedia editors — a
        much cleaner taxonomic signal than free-text search relevance,
        which pulls in unrelated senses of ambiguous words (verified: a
        free-text search for "portable cooling device" returned "Laptop"
        and "Mobile device" as top hits; category search does not)."""
        payload = self.http.get_json(
            SEARCH_URL,
            params={
                "action": "query",
                "list": "search",
                "srsearch": keyword,
                "srnamespace": CATEGORY_NAMESPACE,
                "srlimit": limit,
                "format": "json",
            },
        )
        return [r["title"] for r in payload.get("query", {}).get("search", [])]

    def category_members(self, category: str, limit: int = 60) -> list[dict]:
        """Real article-namespace members of a real Wikipedia category."""
        payload = self.http.get_json(
            SEARCH_URL,
            params={
                "action": "query",
                "list": "categorymembers",
                "cmtitle": category,
                "cmlimit": limit,
                "cmnamespace": ARTICLE_NAMESPACE,
                "format": "json",
            },
        )
        members = payload.get("query", {}).get("categorymembers", [])
        candidates = []
        for m in members:
            title = m["title"]
            if _EXCLUDE_PATTERNS.match(title):
                continue
            candidates.append({"title": title, "snippet": f"member of {category}"})
        return candidates

    def search_candidates(self, query: str, limit: int = 20) -> list[dict]:
        """Real Wikipedia full-text search results, main article namespace
        only. Kept as a fallback when no matching real category exists for
        a phenomenon keyword; category-based discovery (find_categories +
        category_members) is preferred and much less noisy."""
        payload = self.http.get_json(
            SEARCH_URL,
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srnamespace": ARTICLE_NAMESPACE,
                "srlimit": limit,
                "format": "json",
            },
        )
        results = payload.get("query", {}).get("search", [])
        candidates = []
        for r in results:
            title = r["title"]
            if _EXCLUDE_PATTERNS.match(title):
                continue
            snippet = re.sub("<[^>]+>", "", r.get("snippet", ""))
            candidates.append({"title": title, "snippet": snippet})
        return candidates

    def real_median_pageviews(self, article: str, days: int = 90) -> float | None:
        """Real recent pageview median for a candidate article. Returns
        None (never a fabricated number) if the article has no measurable
        traffic or the lookup fails for a real, external reason."""
        from urllib.parse import quote

        end = timeutil.utcnow().date() - timedelta(days=1)
        start = end - timedelta(days=days)
        article_path = quote(article.replace(" ", "_"), safe="")
        url = f"{PAGEVIEWS_URL}/en.wikipedia/all-access/user/{article_path}/daily/{start:%Y%m%d}00/{end:%Y%m%d}00"
        try:
            payload = self.http.get_json(url)
        except Exception:
            return None
        if payload is None:
            return None
        items = payload.get("items", [])
        if not items:
            return None
        values = sorted(item["views"] for item in items)
        mid = len(values) // 2
        median = values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2
        return float(median)

    def discover_via_categories(
        self,
        keyword: str,
        category_limit: int = 3,
        members_per_category: int = 60,
    ) -> tuple[list[dict], str | None]:
        """Preferred discovery path: find real matching categories, pull
        real members. Returns (candidates, category_used_for_provenance)."""
        categories = self.find_categories(keyword, limit=category_limit)
        for category in categories:
            members = self.category_members(category, limit=members_per_category)
            if len(members) >= 5:  # a near-empty category is not useful evidence
                return members, category
        return [], None

    def discover(
        self,
        query: str,
        min_median_pageviews: float = 20.0,
        max_candidates: int = 15,
        search_limit: int = 20,
    ) -> list[DiscoveredEntity]:
        """Full discovery pipeline for one phenomenon keyword: real
        category membership (preferred) or real search (fallback) → real
        pageview verification → keep only entities with genuine measurable
        public attention."""
        candidates, category_used = self.discover_via_categories(query)
        if category_used:
            self.log.info("using category-based discovery", query=query, category=category_used)
        else:
            self.log.info("no matching category found, falling back to full-text search", query=query)
            candidates = self.search_candidates(query, limit=search_limit)

        discovered: list[DiscoveredEntity] = []
        for candidate in candidates:
            if len(discovered) >= max_candidates:
                break
            title = candidate["title"]
            median = self.real_median_pageviews(title)
            if median is None or median < min_median_pageviews:
                self.log.info(
                    "candidate entity rejected: insufficient real pageview evidence",
                    title=title, query=query, median=median,
                )
                continue
            discovered.append(
                DiscoveredEntity(
                    entity=title,
                    article=title,
                    query=query,
                    search_snippet=candidate["snippet"],
                    median_daily_pageviews=median,
                )
            )
            self.log.info("entity discovered from real evidence", title=title, query=query, median=median)
        return discovered
