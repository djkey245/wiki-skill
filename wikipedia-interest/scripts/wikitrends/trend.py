"""Main trend method (analysis §3).

1. seasonal profile on history + window, 2. deseasonalize, 3. Theil–Sen on the window,
4. AR(1) phi on local-trend residuals of the whole history -> Hamed–Rao factor n/n*
   with rho_k = phi**k, clamped to [1, MAX_VARIANCE_INFLATION],
5. Mann–Kendall with the corrected Var(S), 6. Sen CI with the same Var(S).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import List, Optional, Sequence, Tuple

from . import seasonal, stats
from . import thresholds as T

MIN_TREND_POINTS = 3


class NotEnoughPoints(ValueError):
    """Fewer than MIN_TREND_POINTS positive months in the window."""


@dataclass(frozen=True)
class TrendEstimate:
    slope: float
    ci_low: float
    ci_high: float
    p: float
    s: int
    var_s: float
    n: int
    phi: float
    inflation_raw: float
    inflation: float
    autocorr_capped: bool
    history_months: int
    short_history: bool
    deseasonalized: List[Optional[float]]
    history: List[Optional[float]] = field(default_factory=list)  # deseasonalized months before the window

    @property
    def growth_pct_yr(self) -> float:
        return stats.pct_per_year(self.slope)

    @property
    def growth_ci(self) -> Tuple[float, float]:
        return stats.pct_per_year(self.ci_low), stats.pct_per_year(self.ci_high)


def estimate(
    values: Sequence[Optional[float]],
    first_month: str,
    window_start: int,
    *,
    alpha: float = T.ALPHA,
    max_inflation: float = T.MAX_VARIANCE_INFLATION,
) -> TrendEstimate:
    """Trend of a positive monthly metric.

    `values` runs from the history start (`first_month`) to the window end;
    the window is values[window_start:]. Zero or missing months are skipped.
    """
    logs = seasonal.log_series(values)
    profile = seasonal.seasonal_profile(logs, first_month)
    deseasonalized = seasonal.deseasonalize(logs, first_month, profile)
    window = deseasonalized[window_start:]
    times, ys = stats.valid_points(window)
    if len(ys) < MIN_TREND_POINTS:
        raise NotEnoughPoints(f"{len(ys)} positive months in the window, need {MIN_TREND_POINTS}")

    slopes = stats.pairwise_slopes(times, ys)
    slope = median(slopes)

    phi = stats.ar1_phi(seasonal.local_trend_segments(deseasonalized, window_start))
    raw = stats.hamed_rao_factor(stats.ar1_rho(phi, len(ys)), len(ys))
    inflation = min(max(raw, 1.0), max_inflation)

    mk = stats.mk_test(ys, inflation)
    low, high = stats.sen_ci(slopes, mk.var_s, alpha)
    history = sum(1 for v in logs[:window_start] if v is not None)
    return TrendEstimate(
        slope=slope,
        ci_low=low,
        ci_high=high,
        p=mk.p,
        s=mk.s,
        var_s=mk.var_s,
        n=len(ys),
        phi=phi,
        inflation_raw=raw,
        inflation=inflation,
        autocorr_capped=raw > max_inflation,
        history_months=history,
        short_history=history < T.MIN_HISTORY_FOR_PROFILE,
        deseasonalized=window,
        history=deseasonalized[:window_start],
    )
