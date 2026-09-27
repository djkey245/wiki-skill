"""Topic search (CirrusSearch + pageprops, wbsearchentities fallback), morelike and outgoing links (data §3)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence

from . import thresholds as T
from .sitematrix import Site
from .wikidata import WIKIDATA_API


@dataclass(frozen=True)
class Hit:
    title: str
    qid: Optional[str]  # pageprops.wikibase_item; None for pages without a Wikidata item


def query_pages(client: Any, api: str, params: Mapping[str, str], max_requests: int = 1) -> Iterator[Dict[str, Any]]:
    """Pages of an Action API query, following `continue` for at most max_requests requests."""
    request = dict(params)
    for _ in range(max_requests):
        data = client.get_action(api, request)
        yield from data.get("query", {}).get("pages", [])
        if "continue" not in data:
            return
        request = {**params, **data["continue"]}


def _hits(pages: Sequence[Dict[str, Any]]) -> List[Hit]:
    ordered = sorted(pages, key=lambda p: p.get("index", 0))
    return [Hit(p["title"], p.get("pageprops", {}).get("wikibase_item")) for p in ordered if not p.get("missing")]


def _search(client: Any, cache: Any, site: Site, query: str, limit: int) -> List[Hit]:
    key = f"{site.code}|{limit}|{query}"
    cached = cache.get_meta("search", key)
    if cached is not None:
        return [Hit(*item) for item in cached]
    params = {
        "action": "query", "generator": "search", "gsrsearch": query, "gsrnamespace": "0",
        "gsrlimit": str(limit), "prop": "pageprops", "ppprop": "wikibase_item",
    }
    hits = _hits(list(query_pages(client, site.api, params)))
    cache.put_meta("search", key, [[h.title, h.qid] for h in hits])
    return hits


def search_pages(client: Any, cache: Any, site: Site, query: str, limit: int = T.SEARCH_LIMIT) -> List[Hit]:
    """Full-text search in the language of the query (handles word forms), best first."""
    return _search(client, cache, site, query, limit)


def morelike(client: Any, cache: Any, site: Site, title: str, limit: int = T.MORELIKE_LIMIT) -> List[Hit]:
    return [h for h in _search(client, cache, site, f"morelike:{title}", limit) if h.title != title]


@dataclass(frozen=True)
class EntityHit:
    qid: str
    label: str
    description: str


def wbsearch(client: Any, cache: Any, query: str, language: str, limit: int = 5) -> List[EntityHit]:
    """Fallback when CirrusSearch finds nothing with a Wikidata item."""
    key = f"{language}|{limit}|{query}"
    cached = cache.get_meta("wbsearch", key)
    if cached is None:
        data = client.get_action(
            WIKIDATA_API,
            {"action": "wbsearchentities", "search": query, "language": language, "uselang": language,
             "type": "item", "limit": str(limit)},
            maxlag=False,
        )
        cached = [[i["id"], i.get("label", ""), i.get("description", "")] for i in data.get("search", [])]
        cache.put_meta("wbsearch", key, cached)
    return [EntityHit(*item) for item in cached]


def outgoing_links(client: Any, cache: Any, site: Site, title: str, max_pages: int = T.MAX_LINK_PAGES) -> List[Hit]:
    """Main-namespace links of a page with their QIDs; at most max_pages requests."""
    key = f"{site.code}|{max_pages}|{title}"
    cached = cache.get_meta("links", key)
    if cached is not None:
        return [Hit(*item) for item in cached]
    params = {
        "action": "query", "generator": "links", "titles": title, "gplnamespace": "0", "gpllimit": "max",
        "prop": "pageprops", "ppprop": "wikibase_item", "redirects": "1",
    }
    merged: Dict[str, Optional[str]] = {}
    for page in query_pages(client, site.api, params, max_pages):
        if page.get("missing"):
            continue
        qid = page.get("pageprops", {}).get("wikibase_item")
        merged[page["title"]] = merged.get(page["title"]) or qid
    hits = [Hit(t, q) for t, q in merged.items()]
    cache.put_meta("links", key, [[h.title, h.qid] for h in hits])
    return hits


def qids_for_titles(client: Any, site: Site, titles: Sequence[str]) -> Dict[str, Optional[str]]:
    """QID of each title after normalization and redirects, in batches of ACTION_API_BATCH."""
    out: Dict[str, Optional[str]] = {}
    for i in range(0, len(titles), T.ACTION_API_BATCH):
        batch = list(titles[i : i + T.ACTION_API_BATCH])
        data = client.get_action(
            site.api,
            {"action": "query", "titles": "|".join(batch), "prop": "pageprops", "ppprop": "wikibase_item",
             "redirects": "1"},
        )
        query = data.get("query", {})
        final = {t: t for t in batch}
        for step in ("normalized", "redirects"):
            mapping = {item["from"]: item["to"] for item in query.get(step, [])}
            final = {t: mapping.get(f, f) for t, f in final.items()}
        qid_of = {p["title"]: p.get("pageprops", {}).get("wikibase_item") for p in query.get("pages", []) if not p.get("missing")}
        for title in batch:
            out[title] = qid_of.get(final[title])
    return out
