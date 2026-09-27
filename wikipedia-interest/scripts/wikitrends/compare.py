"""Pairwise tests, groups, size x dynamics matrix, criteria statuses, recommendation order
(recommendations §1–5). No text here: sentences come from templates.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import stats, trend
from . import thresholds as T
from .confidence import Reason

GROUPS = ("promising", "emerging", "steady", "weakening", "no_clear_trend", "insufficient_data")
QUADRANTS = (  # the order of recommendations §5 p.1
    "big_growing", "growing_niche", "unconfirmed", "big_stable", "small_stable", "no_trend", "weakening", "insufficient",
)
CRITERIA_ORDER = ("met", "unconfirmed", "not_met")


# --- groups (recommendations §2) ---------------------------------------------------------


def group_of(direction: str, confidence: str, reasons: Sequence[Reason]) -> str:
    """The decision table; low_volume wins when both blocking reasons are present."""
    blocking = {r.code for r in reasons if r.severity == "blocking"}
    if "low_volume" in blocking:
        return "insufficient_data"
    if blocking:
        if direction != "unclear" or blocking != {"not_significant"}:
            raise ValueError(f"impossible combination: {direction}, blocking {sorted(blocking)}")
        return "no_clear_trend"
    if direction == "unclear":
        raise ValueError("unclear always has a blocking reason")
    if confidence == "low":
        return "emerging" if direction == "growing" else "no_clear_trend"
    return {"growing": "promising", "stable": "steady", "small_effect": "steady", "declining": "weakening"}[direction]


# --- matrix (recommendations §3) ---------------------------------------------------------


def size_flags(values: Dict[str, Optional[float]]) -> Dict[str, Optional[bool]]:
    """True = at or above the median ("big"). None for every item when fewer than
    MIN_MATRIX_ITEMS items have a value (the median split degenerates), and for an item without one."""
    present = {k: v for k, v in values.items() if v is not None}
    if len(present) < T.MIN_MATRIX_ITEMS:
        return {k: None for k in values}
    middle = median(present.values())
    return {k: (None if v is None else v >= middle) for k, v in values.items()}


def quadrant(group: str, big: Optional[bool]) -> Optional[str]:
    """Every group x size pair maps to exactly one quadrant; None without a matrix."""
    if big is None:
        return None
    if group == "promising":
        return "big_growing" if big else "growing_niche"
    if group == "steady":
        return "big_stable" if big else "small_stable"
    return {"emerging": "unconfirmed", "weakening": "weakening", "no_clear_trend": "no_trend",
            "insufficient_data": "insufficient"}[group]


def order_rank(group: str, quad: Optional[str]) -> int:
    """Quadrant order; without a matrix the same order of groups, big before niche."""
    return QUADRANTS.index(quad if quad is not None else quadrant(group, True))  # type: ignore[arg-type]


# --- criteria (recommendations §4) -------------------------------------------------------


def criteria_status(
    criteria: Dict[str, Any], group: str, growth_pct_yr: Optional[float], monthly_views: Optional[float]
) -> Tuple[Dict[str, str], Optional[bool]]:
    """({criterion: met | not_met | unconfirmed}, criteria_met); ({}, None) without criteria."""
    out: Dict[str, str] = {}
    if "min_monthly_views" in criteria:
        ok = monthly_views is not None and monthly_views >= criteria["min_monthly_views"]
        out["min_monthly_views"] = "met" if ok else "not_met"
    if "min_growth_pct_yr" in criteria:
        threshold = criteria["min_growth_pct_yr"]
        above = growth_pct_yr is not None and growth_pct_yr >= threshold
        if above and (group == "promising" or (threshold <= 0 and group == "steady")):
            out["min_growth_pct_yr"] = "met"
        elif above and group in ("emerging", "no_clear_trend"):
            out["min_growth_pct_yr"] = "unconfirmed"
        else:
            out["min_growth_pct_yr"] = "not_met"
    if not out:
        return out, None
    return out, all(v == "met" for v in out.values())


def criteria_rank(statuses: Dict[str, str]) -> int:
    """met -> unconfirmed -> not_met; no criteria ranks as met."""
    return max((CRITERIA_ORDER.index(s) for s in statuses.values()), default=0)


# --- recommendations (recommendations §5) ------------------------------------------------


@dataclass(frozen=True)
class Item:
    key: str  # a language (within a topic) or a topic id (within a language)
    group: str
    quadrant: Optional[str]
    criteria: Dict[str, str]
    growth_pct_yr: Optional[float]


@dataclass(frozen=True)
class Recommendations:
    explore: List[Item]  # up to EXPLORE_MAX, promising / emerging
    postpone: List[Item]  # weakening, insufficient_data, a not_met criterion
    rest: List[Item]


def order(items: Sequence[Item]) -> List[Item]:
    """Lexicographic: quadrant -> criteria status -> growth descending (then the key, for stability)."""
    def key(i: Item) -> Tuple[int, int, float, str]:
        growth = i.growth_pct_yr if i.growth_pct_yr is not None else float("-inf")
        return order_rank(i.group, i.quadrant), criteria_rank(i.criteria), -growth, i.key
    return sorted(items, key=key)


def recommend(items: Sequence[Item]) -> Recommendations:
    ranked = order(items)
    postpone = [i for i in ranked if i.group in ("weakening", "insufficient_data") or "not_met" in i.criteria.values()]
    explore = [i for i in ranked if i.group in ("promising", "emerging") and i not in postpone][: T.EXPLORE_MAX]
    rest = [i for i in ranked if i not in explore and i not in postpone]
    return Recommendations(explore, postpone, rest)


# --- pairwise tests (recommendations §1) -------------------------------------------------


@dataclass(frozen=True)
class Pair:
    a: str
    b: str
    diff_pct_yr: float  # growth of share_a / share_b, %/yr: > 0 means a grows faster
    ci: Tuple[float, float]
    p: float
    p_adj: float  # Benjamini–Hochberg within the family (a topic's languages or a language's topics)

    @property
    def significant(self) -> bool:
        return self.p_adj < T.ALPHA


def ratio(share_a: Sequence[Optional[float]], share_b: Sequence[Optional[float]]) -> List[Optional[float]]:
    return [a / b if a and b else None for a, b in zip(share_a, share_b)]


def pairwise(series: Dict[str, Sequence[Optional[float]]], first_month: str, window_start: int) -> List[Pair]:
    """Every pair of the family, tested with the main trend method on log(share_a / share_b), BH-adjusted.

    All series run from the same `first_month`; a pair with fewer than 3 usable months is skipped.
    """
    keys = list(series)
    raw: List[Tuple[str, str, trend.TrendEstimate]] = []
    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            try:
                est = trend.estimate(ratio(series[a], series[b]), first_month, window_start)
            except trend.NotEnoughPoints:
                continue
            raw.append((a, b, est))
    adjusted = stats.benjamini_hochberg([e.p for _, _, e in raw])
    return [Pair(a, b, e.growth_pct_yr, e.growth_ci, e.p, p) for (a, b, e), p in zip(raw, adjusted)]


def neighbours(ranked: Sequence[str]) -> List[Tuple[str, str]]:
    """Adjacent pairs of a ranking: the pairs stdout keeps besides the significant ones (§1)."""
    return [(ranked[i], ranked[i + 1]) for i in range(len(ranked) - 1)]


def find_pair(pairs: Sequence[Pair], a: str, b: str) -> Optional[Pair]:
    for p in pairs:
        if (p.a, p.b) in ((a, b), (b, a)):
            return p
    return None
