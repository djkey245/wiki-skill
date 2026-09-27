"""Checks -> direction, confidence, reasons (analysis §4–6), slices 1–2.

Every reason of analysis §5 but weighting_sensitive and the article-share part of
concentrated (slice 3). Checks run on the main metric of the cell (options.metric).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from . import breaks, metrics, months, stats, trend
from . import thresholds as T

SEVERITY_ORDER = ("blocking", "high", "medium", "low", "info")

SEVERITY = {
    "not_significant": "blocking",
    "spike_driven": "high",
    "unstable_basket": "high",
    "concentrated": "high",
    "autocorr_capped": "high",
    "short_window": "medium",
    "short_history": "medium",
    "thin_basket": "medium",
    "no_common_basket": "medium",
    "single_article": "medium",
    "small_effect": "info",
    "step_like": "high",
    "seed_divergence": "high",
    "data_break": "medium",
    "trend_reversal": "medium",
    "recent_only": "medium",
    "redirect_heavy": "low",
    "data_revised": "info",
    "metric_disagreement": "info",
    "project_trend": "info",
}  # low_volume: "high" or "blocking", decided by the check

CONFIDENCE_ORDER = ("low", "medium", "high")


@dataclass(frozen=True)
class Reason:
    code: str
    severity: str
    detail: Dict[str, object] = field(default_factory=dict)


def reason(code: str, **detail: object) -> Reason:
    return Reason(code, SEVERITY[code], dict(detail))


def point_category(growth_pct_yr: float, flat: float = T.FLAT_PCT) -> str:
    """up (>= +F) / down (<= -F) / flat. The one category function every check uses (analysis §5)."""
    if growth_pct_yr >= flat:
        return "up"
    if growth_pct_yr <= -flat:
        return "down"
    return "flat"


def direction_of(est: trend.TrendEstimate, flat: float = T.FLAT_PCT, alpha: float = T.ALPHA) -> Tuple[str, List[Reason]]:
    """Direction table of analysis §4; the first matching row wins."""
    low, high = est.growth_ci
    significant = est.p < alpha
    if -flat <= low and high <= flat:
        return "stable", [reason("small_effect")] if significant else []
    if significant and abs(est.growth_pct_yr) >= flat:
        return ("growing" if est.growth_pct_yr > 0 else "declining"), []
    if significant:
        return "small_effect", [reason("small_effect")]
    return "unclear", [reason("not_significant")]


def check_low_volume(monthly_views: float, zero_share: float, basket: str) -> Optional[Reason]:
    """Primary-basket volume; the text names the basket and its own value (analysis §5)."""
    detail = {"basket": basket, "monthly_views": round(monthly_views), "zero_share": zero_share}
    if monthly_views < T.LOW_VOLUME_BLOCKING or zero_share > T.MAX_ZERO_SHARE:
        return Reason("low_volume", "blocking", detail)
    if monthly_views < T.LOW_VOLUME_HIGH:
        return Reason("low_volume", "high", detail)
    return None


def overall_confidence(direction: str, reasons: Sequence[Reason], *, short_window: bool, short_history: bool) -> Tuple[str, str]:
    """(direction, confidence) by analysis §6; slice-1 deviation: short_history caps at low (overview §3)."""
    severities = [r.severity for r in reasons]
    if "blocking" in severities:
        return "unclear", "low"
    highs, mediums = severities.count("high"), severities.count("medium")
    if direction in ("growing", "declining", "stable", "small_effect") and highs == 0 and mediums <= 1:
        confidence = "high"
    elif (highs == 1 and mediums == 0) or (highs == 0 and mediums == 2):
        confidence = "medium"
    else:
        confidence = "low"
    if short_window:
        confidence = min(confidence, "medium", key=CONFIDENCE_ORDER.index)
    if short_history:
        confidence = "low"
    return direction, confidence


def spike_months(deseasonalized: Sequence[Optional[float]]) -> List[int]:
    """Window positions of isolated outliers (robust z > SPIKE_Z on residuals from the Theil–Sen line).

    Outliers running without a break to the end of the window are a shift at the end,
    not a spike: they belong to recent_only (slice 2), so one month never gives both reasons.
    """
    slope, intercept = stats.theil_sen(deseasonalized)
    residuals = [None if y is None else y - (intercept + slope * t) for t, y in enumerate(deseasonalized)]
    z = stats.robust_z(residuals)
    outliers = [t for t, v in enumerate(z) if v is not None and abs(v) > T.SPIKE_Z]
    last = max(t for t, v in enumerate(residuals) if v is not None)
    while outliers and outliers[-1] == last:
        outliers.pop()
        last -= 1
        while last >= 0 and residuals[last] is None:
            last -= 1
    return outliers


def _changes_estimate(
    old: trend.TrendEstimate, new: Optional[trend.TrendEstimate]
) -> bool:
    """Category changes, or for up/down the slope shrinks by more than SPIKE_SLOPE_DROP."""
    if new is None:
        return True
    old_cat, new_cat = point_category(old.growth_pct_yr), point_category(new.growth_pct_yr)
    if old_cat != new_cat:
        return True
    return old_cat != "flat" and abs(new.slope) < (1.0 - T.SPIKE_SLOPE_DROP) * abs(old.slope)


def check_spike(cell: metrics.CellMetrics, first_month: str, window_start: int) -> Optional[Reason]:
    est = cell.trend
    assert est is not None
    spikes = spike_months(est.deseasonalized)
    if not spikes:
        return None
    values = list(cell.values)
    for t in spikes:
        values[window_start + t] = None
    if not _changes_estimate(est, metrics.estimate_or_none(values, first_month, window_start)):
        return None
    return reason("spike_driven", months=[months.add(first_month, window_start + t) for t in spikes])


def basket_estimate(data: metrics.LangData, qids: Sequence[str], metric: str) -> Optional[trend.TrendEstimate]:
    values = metrics.metric_series(metrics.aggregate(data.articles, qids), data.denominator, metric)
    return metrics.estimate_or_none(values, data.first_month, data.window_start)


def leave_one_out(data: metrics.LangData, qids: Sequence[str], category: str, metric: str = "share") -> List[str]:
    """QIDs whose removal changes the point-estimate category of the basket."""
    changed = []
    for q in qids:
        est = basket_estimate(data, [r for r in qids if r != q], metric)
        if est is None or point_category(est.growth_pct_yr) != category:
            changed.append(q)
    return changed


def check_concentrated(
    data: metrics.LangData, cell: metrics.CellMetrics, seeds: Sequence[str], category: str,
    seed_est: Optional[trend.TrendEstimate] = None,
) -> Optional[Reason]:
    """Slice 1: only the contribution part — one article gives > 60% of absolute contributions,
    unless it is a seed and the seed basket has the same category (calibration R5)."""
    top = cell.contributors[0]
    if top.share <= T.CONCENTRATED_CONTRIBUTION:
        return None
    if top.qid in seeds:
        if seed_est is None:
            seed_est = basket_estimate(data, list(seeds), cell.metric)
        if seed_est is not None and point_category(seed_est.growth_pct_yr) == category:
            return None
    return reason("concentrated", qid=top.qid, share=top.share)


def check_step(est: trend.TrendEstimate, first_month: str, window_start: int) -> Optional[Reason]:
    """(window >= 24) a significant trend that a step explains better than a line (analysis §5)."""
    if len(est.deseasonalized) < T.SHORT_WINDOW_MONTHS or est.p >= T.ALPHA:
        return None
    fit = stats.step_versus_line(est.deseasonalized, est.phi, T.STEP_MIN_SEGMENT)
    if fit is None or fit.gain <= T.BIC_STEP_MARGIN:
        return None
    return reason("step_like", month=months.add(first_month, window_start + fit.at))


def check_recent_only(cell: metrics.CellMetrics, first_month: str, window_start: int) -> Optional[Reason]:
    """(window >= 24) without the last RECENT_ONLY_MONTHS months the category changes, or for up/down
    the slope shrinks by more than half. Isolated spikes are masked first: a spike that returns to
    its level is spike_driven, never recent_only (analysis §5)."""
    est = cell.trend
    assert est is not None
    if len(est.deseasonalized) < T.SHORT_WINDOW_MONTHS:
        return None
    values = list(cell.values)
    spikes = spike_months(est.deseasonalized)
    for t in spikes:
        values[window_start + t] = None
    base = metrics.estimate_or_none(values, first_month, window_start) if spikes else est
    if base is None:
        return None
    cut = metrics.estimate_or_none(values[: -T.RECENT_ONLY_MONTHS], first_month, window_start)
    if not _changes_estimate(base, cut):
        return None
    return reason("recent_only", months=T.RECENT_ONLY_MONTHS)


def check_trend_reversal(category: str, recent_yoy: Optional[float]) -> Optional[Reason]:
    """up/down while the last 6 months against a year earlier go the other way by > 10 %."""
    if category == "flat" or recent_yoy is None or abs(recent_yoy) <= T.TREND_REVERSAL_PCT:
        return None
    if (category == "up") == (recent_yoy > 0):
        return None
    return reason("trend_reversal", recent_yoy_pct=round(recent_yoy, 1))


def opposite(a: str, b: str) -> bool:
    return {a, b} == {"up", "down"}


def check_seed(
    seed_est: Optional[trend.TrendEstimate], category: str
) -> Tuple[str, Optional[Reason]]:
    """seed_divergence: the seed basket's trend is significant and of the opposite category.
    Status "not_checked" when the seeds are absent in this language (analysis §5)."""
    if seed_est is None:
        return "not_checked", None
    seed_category = point_category(seed_est.growth_pct_yr)
    if seed_est.p < T.ALPHA and opposite(seed_category, category):
        return "diverges", reason("seed_divergence", growth_pct_yr=round(seed_est.growth_pct_yr, 2))
    return "agrees", None


def check_metrics(cell: metrics.CellMetrics, category: str) -> List[Reason]:
    """metric_disagreement: the two metrics point in opposite directions; project_trend: context
    whenever their categories differ at all (the edition's traffic explains the gap)."""
    if cell.raw_trend is None:
        return []
    raw_category = point_category(cell.raw_trend.growth_pct_yr)
    out = []
    if opposite(category, raw_category):
        out.append(reason("metric_disagreement", other=metrics.other_metric(cell.metric),
                          raw_growth_pct_yr=round(cell.raw_trend.growth_pct_yr, 2)))
    if raw_category != category and cell.project_trend_pct_yr is not None:
        out.append(reason("project_trend", project_trend_pct_yr=round(cell.project_trend_pct_yr, 2)))
    return out


def check_breaks(
    est: trend.TrendEstimate, first_month: str, window_start: int, known: Sequence[breaks.Break]
) -> Tuple[List[Dict[str, object]], Optional[Reason]]:
    """Level shift at every known break inside the window (data §6)."""
    checks = breaks.check(est.deseasonalized, est.history, months.add(first_month, window_start), known)
    found = [c["month"] for c in checks if c["status"] == "significant"]
    return checks, (reason("data_break", months=found) if found else None)


@dataclass(frozen=True)
class Assessment:
    direction: str
    confidence: str
    category: Optional[str]  # point-estimate category of growth_pct_yr; None without a trend
    reasons: List[Reason]
    seed_divergence: str = "not_checked"  # agrees | diverges | not_checked
    breaks: List[Dict[str, object]] = field(default_factory=list)  # data_break checks for result.json


def _sorted(reasons: List[Reason]) -> List[Reason]:
    return sorted(reasons, key=lambda r: (SEVERITY_ORDER.index(r.severity), r.code))


def assess(
    data: metrics.LangData, baskets: metrics.Baskets, cell: metrics.CellMetrics, seeds: Sequence[str], *,
    revised: Sequence[str] = (), known_breaks: Sequence[breaks.Break] = breaks.BREAKS,
) -> Assessment:
    """`revised`: historical months of this language that Wikimedia recalculated (data_revised)."""
    reasons: List[Reason] = []
    if revised:
        reasons.append(reason("data_revised", months=list(revised)))
    n = len(cell.qids)
    n_window = len(cell.views) - data.window_start
    if baskets.primary_name == "local" and not baskets.common:
        reasons.append(reason("no_common_basket"))
    if n == 1:
        reasons.append(reason("single_article"))
    elif n <= T.THIN_BASKET_MAX:
        reasons.append(reason("thin_basket", n=n))
    if n_window < T.SHORT_WINDOW_MONTHS:
        reasons.append(reason("short_window", months=n_window))
    low_volume = check_low_volume(cell.primary_monthly_views, cell.zero_share, cell.basket)
    if low_volume is not None:
        reasons.append(low_volume)
    if cell.redirect_share > T.REDIRECT_HEAVY_SHARE:
        reasons.append(reason("redirect_heavy", share=round(cell.redirect_share, 3)))

    est = cell.trend
    if est is None:  # < 3 positive months: zero share is > 20%, low_volume already blocks
        return Assessment("unclear", "low", None, _sorted(reasons))

    direction, direction_reasons = direction_of(est)
    reasons += direction_reasons
    category = point_category(est.growth_pct_yr)
    if est.autocorr_capped:
        reasons.append(reason("autocorr_capped", inflation=est.inflation_raw))
    if est.short_history:
        reasons.append(reason("short_history", history_months=est.history_months))
    first, start = data.first_month, data.window_start
    seed_est = basket_estimate(data, baskets.seed, cell.metric) if baskets.seed else None
    for found in (
        check_spike(cell, first, start),
        check_step(est, first, start),
        check_recent_only(cell, first, start),
        check_trend_reversal(category, cell.recent_yoy_pct),
    ):
        if found is not None:
            reasons.append(found)
    seed_status, divergence = check_seed(seed_est, category)
    if divergence is not None:
        reasons.append(divergence)
    break_checks, data_break = check_breaks(est, first, start, known_breaks)
    if data_break is not None:
        reasons.append(data_break)
    reasons += check_metrics(cell, category)
    if n >= 3:
        changed = leave_one_out(data, cell.qids, category, cell.metric)
        if changed:
            reasons.append(reason("unstable_basket", qids=changed))
        concentrated = check_concentrated(data, cell, baskets.seed, category, seed_est)
        if concentrated is not None:
            reasons.append(concentrated)

    direction, confidence = overall_confidence(
        direction, reasons, short_window=n_window < T.SHORT_WINDOW_MONTHS, short_history=est.short_history
    )
    return Assessment(direction, confidence, category, _sorted(reasons), seed_status, break_checks)
