"""Baskets and metrics of a topic × language cell (analysis §1–2).

Input is what the data layer (plan 04) loads for one language: monthly series from the
history start (`first_month`) to the window end, the window being [window_start:].
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from . import alive, trend
from . import thresholds as T


@dataclass(frozen=True)
class Article:
    qid: str
    views: List[int]  # article + selected redirects
    redirect_views: List[int]  # the redirects' part of `views`


@dataclass(frozen=True)
class Exclusion:
    qid: str
    code: str  # missing_in_lang | late_start | below_alive_threshold | duplicate_of | fetch_failed
    lang: str
    detail: Optional[str] = None  # alive month for late_start, the other QID for duplicate_of


@dataclass(frozen=True)
class LangData:
    lang: str
    first_month: str
    window_start: int
    denominator: List[Optional[int]]  # project views minus main page views
    articles: Dict[str, Article]
    exclusions: Dict[str, Exclusion] = field(default_factory=dict)  # duplicate_of / fetch_failed


@dataclass(frozen=True)
class Baskets:
    common: List[str]
    local: List[str]
    seed: List[str]
    excluded: List[Exclusion]  # why spec QIDs are not in `local` or not in `common`

    @property
    def primary_name(self) -> str:
        return "common" if self.common else "local"

    @property
    def primary(self) -> List[str]:
        return self.common if self.common else self.local


def _lang_exclusions(qids: Sequence[str], data: LangData) -> Dict[str, Exclusion]:
    out: Dict[str, Exclusion] = {}
    for qid in qids:
        if qid in data.exclusions:
            out[qid] = data.exclusions[qid]
        elif qid not in data.articles:
            out[qid] = Exclusion(qid, "missing_in_lang", data.lang)
        else:
            st = alive.status(data.articles[qid].views, data.first_month, data.window_start)
            if not st.alive:
                out[qid] = Exclusion(qid, st.code or "below_alive_threshold", data.lang, st.month)
    return out


def build_baskets(
    qids: Sequence[str], seeds: Sequence[str], data_by_lang: Dict[str, LangData]
) -> Dict[str, Baskets]:
    """common / local / seed baskets for every language of the spec (analysis §1)."""
    excluded = {lang: _lang_exclusions(qids, data) for lang, data in data_by_lang.items()}
    common = [q for q in qids if all(q not in ex for ex in excluded.values())]
    out: Dict[str, Baskets] = {}
    for lang in data_by_lang:
        local = [q for q in qids if q not in excluded[lang]]
        reasons = list(excluded[lang].values())
        for qid in local:
            if qid not in common:
                reasons.append(next(ex[qid] for ex in excluded.values() if qid in ex))
        reasons.sort(key=lambda e: qids.index(e.qid))
        out[lang] = Baskets(common, local, [q for q in seeds if q in local], reasons)
    return out


# --- series metrics -------------------------------------------------------------------


def aggregate(articles: Dict[str, Article], qids: Sequence[str]) -> List[int]:
    n = len(articles[qids[0]].views)
    return [sum(articles[q].views[t] for q in qids) for t in range(n)]


def share_ppm(views: Sequence[int], denominator: Sequence[Optional[int]]) -> List[Optional[float]]:
    return [v / d * 1e6 if d else None for v, d in zip(views, denominator)]


METRICS = ("share", "views")


def metric_series(views: Sequence[int], denominator: Sequence[Optional[int]], metric: str) -> List[Optional[float]]:
    """The series a trend is computed on: share_ppm (options.metric "share") or raw views ("views")."""
    if metric not in METRICS:
        raise ValueError(f"unknown metric {metric!r}")
    return share_ppm(views, denominator) if metric == "share" else [float(v) for v in views]


def other_metric(metric: str) -> str:
    return "views" if metric == "share" else "share"


def _mean(values: Sequence[Optional[float]]) -> Optional[float]:
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None


def level_ppm(share: Sequence[Optional[float]]) -> Optional[float]:
    """Mean share over the last LEVEL_MONTHS months."""
    return _mean(share[-T.LEVEL_MONTHS :])


def monthly_views(views: Sequence[int], window_start: int) -> float:
    """Mean views over the last 12 months of the window (the whole window if shorter)."""
    window = views[window_start:]
    last = window[-min(T.LEVEL_MONTHS, len(window)) :]
    return sum(last) / len(last)


def index_series(share: Sequence[Optional[float]], window_start: int) -> List[Optional[float]]:
    """share / base × 100 over the window; base = mean of the first BASE_MONTHS months of the window.

    The window is at least 12 months, so the base always covers a full year and is not
    shifted by seasonality.
    """
    window = share[window_start:]
    base = _mean(window[: T.BASE_MONTHS])
    if not base:
        return [None] * len(window)
    return [None if v is None else v / base * 100.0 for v in window]


def recent_yoy_pct(share: Sequence[Optional[float]], window_start: int) -> Optional[float]:
    """Last YOY_MONTHS months vs the same months a year earlier, % (window >= MIN_WINDOW_FOR_YOY)."""
    if len(share) - window_start < T.MIN_WINDOW_FOR_YOY:
        return None
    recent = _mean(share[-T.YOY_MONTHS :])
    earlier = _mean(share[-T.YOY_MONTHS - 12 : -12])
    if recent is None or not earlier:
        return None
    return (recent / earlier - 1.0) * 100.0


def zero_share(views: Sequence[int], window_start: int) -> float:
    window = views[window_start:]
    return sum(1 for v in window if not v) / len(window)


@dataclass(frozen=True)
class Contribution:
    qid: str
    delta: float  # mean views, last 12 months minus first 12 (halves of a window < 24)
    share: float  # |delta| / sum of |delta| over the basket


def contributors(articles: Dict[str, Article], qids: Sequence[str], window_start: int) -> List[Contribution]:
    """Contribution of every QID to the change of basket views, largest share first."""
    n_window = len(articles[qids[0]].views) - window_start
    span = min(12, n_window // 2)
    deltas = {}
    for q in qids:
        window = articles[q].views[window_start:]
        deltas[q] = sum(window[-span:]) / span - sum(window[:span]) / span
    total = sum(abs(d) for d in deltas.values())
    out = [Contribution(q, d, abs(d) / total if total else 0.0) for q, d in deltas.items()]
    out.sort(key=lambda c: -c.share)
    return out


def redirect_share(articles: Dict[str, Article], qids: Sequence[str], window_start: int) -> float:
    views = sum(sum(articles[q].views[window_start:]) for q in qids)
    redirects = sum(sum(articles[q].redirect_views[window_start:]) for q in qids)
    return redirects / views if views else 0.0


# --- the cell -------------------------------------------------------------------------


def estimate_or_none(values: Sequence[Optional[float]], first_month: str, window_start: int) -> Optional[trend.TrendEstimate]:
    try:
        return trend.estimate(values, first_month, window_start)
    except trend.NotEnoughPoints:
        return None


@dataclass(frozen=True)
class CellMetrics:
    basket: str  # "common" | "local": the primary basket the trend is computed on
    qids: List[str]
    views: List[int]  # primary basket, history start .. window end
    share: List[Optional[float]]
    trend: Optional[trend.TrendEstimate]  # on the main metric; None: fewer than 3 positive months in the window
    raw_trend: Optional[trend.TrendEstimate]  # the same method on the other metric (a control)
    level_ppm: Optional[float]
    index: List[Optional[float]]
    monthly_views: float  # on `local` (size, analysis §1)
    primary_monthly_views: float  # on the primary basket (low_volume)
    zero_share: float
    recent_yoy_pct: Optional[float]
    contributors: List[Contribution]
    redirect_share: float
    project_trend_pct_yr: Optional[float]
    metric: str = "share"  # options.metric: the main metric of trend, index and recent_yoy_pct
    values: List[Optional[float]] = field(default_factory=list)  # the main metric, history start .. window end

    @property
    def growth_pct_yr(self) -> Optional[float]:
        return None if self.trend is None else self.trend.growth_pct_yr

    @property
    def raw_growth_pct_yr(self) -> Optional[float]:
        return None if self.raw_trend is None else self.raw_trend.growth_pct_yr


def compute_cell(data: LangData, baskets: Baskets, metric: str = "share") -> CellMetrics:
    """All metrics of a cell but equal_weight_growth_pct_yr (slice 3). The trend, `index` and
    `recent_yoy_pct` use the main metric (options.metric), `raw_trend` the other one;
    `level_ppm` is always a share (analysis §2)."""
    qids = baskets.primary
    if not qids:
        raise ValueError(f"{data.lang}: empty basket")
    first, start = data.first_month, data.window_start
    views = aggregate(data.articles, qids)
    share = share_ppm(views, data.denominator)
    values = metric_series(views, data.denominator, metric)
    project = estimate_or_none([float(d) if d else None for d in data.denominator], first, start)
    return CellMetrics(
        basket=baskets.primary_name,
        qids=list(qids),
        views=views,
        share=share,
        trend=estimate_or_none(values, first, start),
        raw_trend=estimate_or_none(metric_series(views, data.denominator, other_metric(metric)), first, start),
        level_ppm=level_ppm(share),
        index=index_series(values, start),
        monthly_views=monthly_views(aggregate(data.articles, baskets.local), start),
        primary_monthly_views=monthly_views(views, start),
        zero_share=zero_share(views, start),
        recent_yoy_pct=recent_yoy_pct(values, start),
        contributors=contributors(data.articles, qids, start),
        redirect_share=redirect_share(data.articles, qids, start),
        project_trend_pct_yr=None if project is None else project.growth_pct_yr,
        metric=metric,
        values=values,
    )
