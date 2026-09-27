"""Seasonal profile from the long history, deseasonalization and local-trend residuals (analysis §3)."""
from __future__ import annotations

import math
from statistics import median
from typing import List, Optional, Sequence

from . import months
from .stats import theil_sen, valid_points

MIN_SEGMENT_POINTS = 3


def log_series(values: Sequence[Optional[float]]) -> List[Optional[float]]:
    """log of a positive metric; zero or missing months do not take part (None)."""
    return [math.log(v) if v is not None and v > 0 else None for v in values]


def calendar_index(first_month: str, offset: int) -> int:
    """0 = January for the month `offset` months after `first_month`."""
    return (months.to_index(first_month) + offset) % 12


def centered_ma12(logs: Sequence[Optional[float]]) -> List[Optional[float]]:
    """Centered 2x12 moving average; None where any of the 13 points is missing."""
    n = len(logs)
    out: List[Optional[float]] = [None] * n
    for t in range(6, n - 6):
        window = logs[t - 6 : t + 7]
        if any(v is None for v in window):
            continue
        out[t] = (0.5 * window[0] + sum(window[1:12]) + 0.5 * window[12]) / 12.0  # type: ignore[operator]
    return out


def seasonal_profile(logs: Sequence[Optional[float]], first_month: str) -> List[float]:
    """Median deviation from the 2x12 MA per calendar month, centered to zero mean (index 0 = January)."""
    moving = centered_ma12(logs)
    buckets: List[List[float]] = [[] for _ in range(12)]
    for t, (value, trend) in enumerate(zip(logs, moving)):
        if value is not None and trend is not None:
            buckets[calendar_index(first_month, t)].append(value - trend)
    raw = [median(bucket) if bucket else 0.0 for bucket in buckets]
    mean = sum(raw) / 12.0
    return [r - mean for r in raw]


def deseasonalize(
    logs: Sequence[Optional[float]], first_month: str, profile: Sequence[float]
) -> List[Optional[float]]:
    return [
        None if v is None else v - profile[calendar_index(first_month, t)]
        for t, v in enumerate(logs)
    ]


def local_trend_segments(
    deseasonalized: Sequence[Optional[float]], window_start: int
) -> List[List[float]]:
    """Residuals from separate Theil–Sen fits on the history before the window and on the window.

    One line over the whole series would leave a break in dynamics in the residuals,
    and the break would look like strong autocorrelation.
    """
    segments: List[List[float]] = []
    for part in (deseasonalized[:window_start], deseasonalized[window_start:]):
        times, ys = valid_points(part)
        if len(ys) < MIN_SEGMENT_POINTS:
            continue
        slope, intercept = theil_sen(part)
        segments.append([y - (intercept + slope * t) for t, y in zip(times, ys)])
    return segments
