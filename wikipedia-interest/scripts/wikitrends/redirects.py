"""Sitelink resolution, redirect lists and redirect ranking (data §2).

query.normalized / query.redirects items are {from, to[, tofragment]} (pywikibot APISite.getredirtarget);
prop=redirects items carry `fragment` for redirects to a section.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import thresholds as T
from .sitematrix import Site


@dataclass(frozen=True)
class PageRedirects:
    title: str  # the resolved article
    redirects: List[str] = field(default_factory=list)  # whole-page redirects
    section: List[str] = field(default_factory=list)  # redirects to a section: excluded, reported


@dataclass(frozen=True)
class Resolution:
    target: Dict[str, Optional[str]]  # requested title -> article title (None: no such page)
    pages: Dict[str, PageRedirects]  # article title -> its redirects


def resolve(client: Any, cache: Any, site: Site, titles: Sequence[str]) -> Resolution:
    """Resolve sitelink titles (a sitelink may point to a redirect) and list the redirects of every article.

    One batched request per ACTION_API_BATCH titles, plus `rdcontinue` pages.
    """
    target: Dict[str, Optional[str]] = {}
    pages: Dict[str, PageRedirects] = {}
    todo = []
    for title in dict.fromkeys(titles):
        cached = cache.get_meta("redirects", f"{site.code}|{title}")
        if cached is None:
            todo.append(title)
        else:
            target[title] = cached["target"]
            if cached["page"] is not None:
                pages[cached["target"]] = PageRedirects(**cached["page"])
    for i in range(0, len(todo), T.ACTION_API_BATCH):
        batch = todo[i : i + T.ACTION_API_BATCH]
        found = _resolve_batch(client, site, batch)
        for title in batch:
            article = found.target[title]
            page = found.pages.get(article) if article else None
            target[title] = article
            if page is not None:
                pages[article] = page  # type: ignore[index]
            cache.put_meta("redirects", f"{site.code}|{title}", {"target": article, "page": None if page is None else page.__dict__})
    return Resolution(target, pages)


def _resolve_batch(client: Any, site: Site, batch: Sequence[str]) -> Resolution:
    params = {
        "action": "query", "titles": "|".join(batch), "redirects": "1", "prop": "redirects",
        "rdprop": "title|fragment", "rdnamespace": "0", "rdlimit": "max",
    }
    request = dict(params)
    final = {t: t for t in batch}
    whole: Dict[str, List[str]] = {}
    section: Dict[str, List[str]] = {}
    existing = set()
    first = True
    while True:
        data = client.get_action(site.api, request)
        query = data.get("query", {})
        if first:
            for step in ("normalized", "redirects"):
                mapping = {item["from"]: item["to"] for item in query.get(step, [])}
                final = {t: mapping.get(f, f) for t, f in final.items()}
            first = False
        for page in query.get("pages", []):
            if page.get("missing") or page.get("invalid"):
                continue
            existing.add(page["title"])
            for rd in page.get("redirects", []):
                (section if rd.get("fragment") else whole).setdefault(page["title"], []).append(rd["title"])
        if "continue" not in data:
            break
        request = {**params, **data["continue"]}
    target = {t: (final[t] if final[t] in existing else None) for t in batch}
    pages = {a: PageRedirects(a, whole.get(a, []), section.get(a, [])) for a in existing}
    return Resolution(target, pages)


def recent_views(client: Any, site: Site, titles: Sequence[str], days: int = T.REDIRECT_RANK_DAYS) -> Dict[str, int]:
    """Views of each page over the last `days` days (PageViewInfo prop=pageviews, 50 pages a request)."""
    out: Dict[str, int] = {t: 0 for t in titles}
    for i in range(0, len(titles), T.ACTION_API_BATCH):
        params = {"action": "query", "titles": "|".join(titles[i : i + T.ACTION_API_BATCH]), "prop": "pageviews",
                  "pvipdays": str(days)}
        request = dict(params)
        while True:
            data = client.get_action(site.api, request)
            for page in data.get("query", {}).get("pages", []):
                daily = page.get("pageviews") or {}
                out[page["title"]] = out.get(page["title"], 0) + sum(v for v in daily.values() if v)
            if "continue" not in data:
                break
            request = {**params, **data["continue"]}
    return out


@dataclass(frozen=True)
class Selection:
    chosen: List[str]
    dropped: int
    dropped_recent_share: float  # share of recent redirect views that the dropped redirects had


def select(redirects: Sequence[str], recent: Optional[Dict[str, int]], max_redirects: int) -> Selection:
    """All redirects if there are at most max_redirects (or recent is None: limit lifted), else the top by recent views."""
    if recent is None or len(redirects) <= max_redirects:
        return Selection(list(redirects), 0, 0.0)
    ranked = sorted(redirects, key=lambda t: (-recent.get(t, 0), t))
    chosen, dropped = ranked[:max_redirects], ranked[max_redirects:]
    total = sum(recent.get(t, 0) for t in redirects)
    share = sum(recent.get(t, 0) for t in dropped) / total if total else 0.0
    return Selection(chosen, len(dropped), share)
