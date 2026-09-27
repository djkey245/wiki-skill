"""Semi-synthetic series (calibration §2): real noise, seasonality and autocorrelation, a known change.

The null is NOT "the window minus its own Theil–Sen line" (its slope would be zero by
construction). It is the residuals of LOCAL trends (history and window fitted apart, as in
analysis §3 p.4) shuffled in blocks of 6-12 months over the whole series, plus the real seasonal
profile: blocks from the history land in the window, so its slope is not zero by construction,
while the autocorrelation inside blocks survives. A known trend exp(beta*t) or a step is then
added to this null, never to the raw series with its own unknown trend.
"""
from __future__ import annotations

import math
import random
from typing import List, Optional, Sequence

from wikitrends import seasonal, stats

BLOCK_MIN, BLOCK_MAX = 6, 12


def residuals(logs: Sequence[Optional[float]], first_month: str, window_start: int) -> List[Optional[float]]:
    """Deseasonalized log minus the local Theil–Sen line of its segment (history / window)."""
    profile = seasonal.seasonal_profile(logs, first_month)
    des = seasonal.deseasonalize(logs, first_month, profile)
    out: List[Optional[float]] = [None] * len(des)
    for lo, hi in ((0, window_start), (window_start, len(des))):
        part = des[lo:hi]
        if sum(v is not None for v in part) < 3:
            continue
        slope, intercept = stats.theil_sen(part)
        for t, v in enumerate(part):
            out[lo + t] = None if v is None else v - (intercept + slope * t)
    return out


def block_shuffle(values: Sequence[Optional[float]], rng: random.Random) -> List[Optional[float]]:
    """Cut into consecutive blocks of 6-12 months, shuffle the blocks, trim to the original length."""
    blocks, t = [], 0
    while t < len(values):
        size = rng.randint(BLOCK_MIN, BLOCK_MAX)
        blocks.append(list(values[t : t + size]))
        t += size
    rng.shuffle(blocks)
    return [v for b in blocks for v in b][: len(values)]


def null_series(values: Sequence[Optional[float]], first_month: str, window_start: int,
                rng: random.Random) -> List[Optional[float]]:
    """A positive series (like share_ppm) without a trend: shuffled local residuals + the real
    seasonal profile around the window's median level. Zero / missing months stay missing."""
    logs = seasonal.log_series(values)
    profile = seasonal.seasonal_profile(logs, first_month)
    shuffled = block_shuffle(residuals(logs, first_month, window_start), rng)
    level = sorted(v for v in logs[window_start:] if v is not None)
    center = level[len(level) // 2]
    return [None if r is None else math.exp(center + r + profile[seasonal.calendar_index(first_month, t)])
            for t, r in enumerate(shuffled)]


def with_trend(values: Sequence[Optional[float]], pct_yr: float) -> List[Optional[float]]:
    slope = math.log(1.0 + pct_yr / 100.0) / 12.0
    return [None if v is None else v * math.exp(slope * t) for t, v in enumerate(values)]


def with_step(values: Sequence[Optional[float]], at: int, log_size: float) -> List[Optional[float]]:
    return [None if v is None else v * (math.exp(log_size) if t >= at else 1.0) for t, v in enumerate(values)]
