"""Pageview series from AQS: articles with redirects, project traffic minus the main page, data boundary (data §1–2).

URL format as in mwviews: title with spaces -> '_', then quote(title, safe='') so '/' is encoded;
timestamps YYYYMMDDHH. A month missing from a per-article answer is a zero (data §2 p.5).
"""
from __future__ import annotations

import calendar
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import months, redirects, sitematrix
from . import thresholds as T
from .deadline import DeadlineReached
from .errors import WikitrendsError
from .http import FATAL_NETWORK_CODES, Response
from .metrics import Article, Exclusion, LangData

AQS = "https://wikimedia.org/api/rest_v1/metrics/pageviews"
AGENT = "user"


def aqs_title(title: str) -> str:
    return urllib.parse.quote(title.replace(" ", "_"), safe="")


def _start_ts(month: str) -> str:
    return month.replace("-", "") + "0100"


def _end_ts(month: str) -> str:
    year, mon = months.parse(month)
    return f"{year:04d}{mon:02d}{calendar.monthrange(year, mon)[1]:02d}00"


def per_article_url(project: str, title: str, first: str, last: str) -> str:
    return f"{AQS}/per-article/{project}/all-access/{AGENT}/{aqs_title(title)}/monthly/{_start_ts(first)}/{_end_ts(last)}"


def aggregate_url(project: str, first: str, last: str) -> str:
    return f"{AQS}/aggregate/{project}/all-access/{AGENT}/monthly/{_start_ts(first)}/{_end_ts(last)}"


def parse_items(response: Response) -> Dict[str, int]:
    """{"items": [{"timestamp": "2024010100", "views": 10}, ...]} -> {"2024-01": 10}."""
    return {f"{i['timestamp'][:4]}-{i['timestamp'][4:6]}": int(i["views"]) for i in response.json().get("items", [])}


def history_start(window_start: str) -> str:
    """History is counted back from the window START, never before EARLIEST_MONTH (data §1)."""
    first = months.add(window_start, -T.HISTORY_BEFORE_WINDOW)
    return first if months.diff(first, T.EARLIEST_MONTH) >= 0 else T.EARLIEST_MONTH


# --- per-article series ---------------------------------------------------------------


@dataclass
class SeriesResult:
    values: Dict[str, List[int]] = field(default_factory=dict)  # title -> views, first..last
    failed: Dict[str, str] = field(default_factory=dict)  # title -> reason
    not_started: List[str] = field(default_factory=list)  # deadline reached before the request


def fetch_series(client: Any, cache: Any, project: str, titles: Sequence[str], first: str, last: str) -> SeriesResult:
    """Series of every title; only missing or expired months go to the network, one request per title."""
    span = months.span(first, last)
    result = SeriesResult()
    cached: Dict[str, Dict[str, int]] = {}
    urls: Dict[str, Tuple[str, List[str]]] = {}
    for title in dict.fromkeys(titles):
        fresh, missing = cache.get_views(project, title, AGENT, span)
        cached[title] = fresh
        if missing:
            requested = months.span(missing[0], missing[-1])
            urls[per_article_url(project, title, requested[0], requested[-1])] = (title, requested)
    replies = client.get_many(list(urls)) if urls else {}
    for url, (title, requested) in urls.items():
        reply = replies.get(url)
        if reply is None:
            result.not_started.append(title)
            continue
        if isinstance(reply, WikitrendsError):
            result.failed[title] = reply.message
            continue
        if reply.status == 404:
            cache.put_not_found(project, title, AGENT, requested)
            fetched = {}
        elif reply.status == 200:
            fetched = parse_items(reply)
            cache.put_views(project, title, AGENT, fetched, requested, last)
        else:
            result.failed[title] = f"HTTP {reply.status}"
            continue
        cached[title].update({m: fetched.get(m, 0) for m in requested})
    for title, values in cached.items():
        if title not in result.failed and title not in result.not_started:
            result.values[title] = [values.get(m, 0) for m in span]
    return result


def fetch_aggregate(client: Any, cache: Any, project: str, first: str, last: str) -> Tuple[Dict[str, int], List[str]]:
    """Project traffic, always re-requested (data §5): returns (values, revised months).

    Revised months invalidate the cached per-article views of this project, so fetch the
    aggregate before the articles.
    """
    url = aggregate_url(project, first, last)
    reply = client.get(url)
    if reply.status != 200:
        raise WikitrendsError("network_error", f"aggregate for {project}: HTTP {reply.status}", url=url)
    values = parse_items(reply)
    revised = cache.put_project_views(project, AGENT, values, last)
    if revised:
        cache.invalidate_views(project, revised)
    return values, revised


def latest_month(client: Any, main_pages: Dict[str, str], today: str) -> Optional[str]:
    """Latest month published in every project both in `aggregate` and in `per-article`
    (probed on the main page), or None. `today` is the current, incomplete month (data §1)."""
    first, last = months.add(today, -T.AVAILABILITY_PROBE_MONTHS), months.add(today, -1)
    urls = {}
    for project, main in main_pages.items():
        urls[project] = (aggregate_url(project, first, last), per_article_url(project, main, first, last))
    replies = client.get_many([u for pair in urls.values() for u in pair])
    if any(u not in replies for pair in urls.values() for u in pair):
        raise DeadlineReached()  # get_many leaves out what the deadline stopped: not "nothing published"
    latest: List[str] = []
    for project, pair in urls.items():
        found = []
        for url in pair:
            reply = replies.get(url)
            ok = isinstance(reply, Response) and reply.status == 200
            found.append(set(parse_items(reply)) if ok else set())  # type: ignore[arg-type]
        both = found[0] & found[1]
        if not both:
            return None
        latest.append(max(both))
    return min(latest) if latest else None


# --- one language -----------------------------------------------------------------------


@dataclass(frozen=True)
class LangPlan:
    lang: str  # the code as written in the spec
    site: sitematrix.Site
    main_page: str
    first_month: str
    window_start: str
    end: str
    articles: Dict[str, str]  # QID -> resolved article title, deduplicated, spec order
    exclusions: Dict[str, Exclusion]  # duplicate_of
    redirects: Dict[str, redirects.PageRedirects]  # article title -> its redirects


def plan_lang(
    client: Any, cache: Any, lang: str, site: sitematrix.Site, sitelinks: Dict[str, str], window_start: str, end: str
) -> LangPlan:
    """Metadata step: main page, sitelink resolution, redirect lists, deduplication (data §2 p.1–2).

    `sitelinks` maps QID -> sitelink title in spec order; QIDs without a sitelink are absent.
    """
    main = sitematrix.main_page(client, cache, site)
    resolution = redirects.resolve(client, cache, site, list(sitelinks.values()))
    owner: Dict[str, str] = {}
    articles: Dict[str, str] = {}
    exclusions: Dict[str, Exclusion] = {}
    for qid, title in sitelinks.items():
        article = resolution.target.get(title)
        if article is None:
            continue  # no such page: missing_in_lang
        if article in owner:
            exclusions[qid] = Exclusion(qid, "duplicate_of", lang, owner[article])
            continue
        owner[article] = qid
        articles[qid] = article
    return LangPlan(lang, site, main, history_start(window_start), window_start, end, articles, exclusions,
                    {a: resolution.pages.get(a, redirects.PageRedirects(a)) for a in articles.values()})


def estimate_requests(cache: Any, plan: LangPlan, max_redirects: int = T.MAX_REDIRECTS) -> int:
    """Conservative request bound: the redirect limit can lift after fetching article series."""
    span = months.span(plan.first_month, plan.end)
    titles = [plan.main_page, *plan.articles.values()]
    ranking = 0
    for article in plan.articles.values():
        found = plan.redirects[article].redirects
        titles += found
        if len(found) > max_redirects:
            ranking += -(-len(found) // T.PAGEVIEWS_TITLES_PER_REQUEST)
    uncached = sum(1 for t in dict.fromkeys(titles) if cache.get_views(plan.site.project, t, AGENT, span)[1])
    return 1 + uncached + ranking


@dataclass(frozen=True)
class RedirectInfo:
    chosen: List[str]
    section: List[str]  # redirects to a section, excluded (result.json)
    dropped: int
    dropped_recent_share: float
    limit_lifted: bool  # the article series starts mid-history (renamed or new): all redirects taken


@dataclass
class LangCollection:
    status: str  # "ok" | "partial" | "failed"
    data: Optional[LangData] = None
    redirect_info: Dict[str, RedirectInfo] = field(default_factory=dict)
    revised_months: List[str] = field(default_factory=list)
    error: Optional[str] = None


def _starts_mid_history(series: Sequence[int]) -> bool:
    return bool(series) and series[0] == 0 and any(series)


def fetch_lang(client: Any, cache: Any, plan: LangPlan, max_redirects: int = T.MAX_REDIRECTS) -> LangCollection:
    """AQS step for one language; the result is the input of metrics.build_baskets (plan 03)."""
    project, first, last = plan.site.project, plan.first_month, plan.end
    try:
        aggregate, revised = fetch_aggregate(client, cache, project, first, last)
        base = fetch_series(client, cache, project, [plan.main_page, *plan.articles.values()], first, last)
        if base.not_started:
            return LangCollection("partial", revised_months=revised)
        if plan.main_page in base.failed:
            return LangCollection("failed", error=f"main page: {base.failed[plan.main_page]}")

        taken = set(plan.articles.values())
        info: Dict[str, RedirectInfo] = {}
        for qid, article in plan.articles.items():
            if article in base.failed:
                continue
            page = plan.redirects[article]
            candidates = [r for r in page.redirects if r not in taken]
            lifted = _starts_mid_history(base.values[article])
            recent = None
            if len(candidates) > max_redirects and not lifted:
                recent = redirects.recent_views(client, plan.site, candidates)
            chosen = redirects.select(candidates, recent, max_redirects)
            taken.update(chosen.chosen)
            info[qid] = RedirectInfo(chosen.chosen, list(page.section), chosen.dropped, chosen.dropped_recent_share, lifted)

        extra = fetch_series(client, cache, project, [t for i in info.values() for t in i.chosen], first, last)
        if extra.not_started:
            return LangCollection("partial", revised_months=revised)
    except DeadlineReached:
        return LangCollection("partial")
    except WikitrendsError as exc:
        if exc.code in FATAL_NETWORK_CODES:
            raise
        return LangCollection("failed", error=exc.message)

    span = months.span(first, last)
    main = base.values[plan.main_page]
    denominator = [aggregate[m] - main[t] if m in aggregate else None for t, m in enumerate(span)]
    articles: Dict[str, Article] = {}
    exclusions = dict(plan.exclusions)
    for qid, article in plan.articles.items():
        chosen = info[qid].chosen if qid in info else []
        if article in base.failed or any(t in extra.failed for t in chosen):
            exclusions[qid] = Exclusion(qid, "fetch_failed", plan.lang)
            continue
        own = base.values[article]
        rds = [extra.values[t] for t in chosen]
        redirect_views = [sum(r[t] for r in rds) for t in range(len(span))]
        articles[qid] = Article(qid, [own[t] + redirect_views[t] for t in range(len(span))], redirect_views)
    data = LangData(plan.lang, first, months.diff(plan.window_start, first), denominator, articles, exclusions)
    return LangCollection("ok", data, info, revised)
