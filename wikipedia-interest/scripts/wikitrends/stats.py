"""Robust trend statistics (standard library only).

Theil–Sen slope, Sen confidence interval, Mann–Kendall test with a variance
correction factor (Hamed–Rao formula), AR(1) autocorrelation from residual
segments, Benjamini–Hochberg, MAD. Cross-checked against scipy and
pymannkendall in tests/test_stats_crosscheck.py.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from statistics import NormalDist, median
from typing import List, Optional, Sequence, Tuple

_NORMAL = NormalDist()


def z_critical(alpha: float) -> float:
    return _NORMAL.inv_cdf(1.0 - alpha / 2.0)


def valid_points(values: Sequence[Optional[float]]) -> Tuple[List[int], List[float]]:
    """Time indices and values of non-missing points (None and NaN are skipped)."""
    times: List[int] = []
    ys: List[float] = []
    for i, value in enumerate(values):
        if value is not None and not math.isnan(value):
            times.append(i)
            ys.append(float(value))
    return times, ys


def pairwise_slopes(times: Sequence[int], ys: Sequence[float]) -> List[float]:
    """Sorted slopes over all pairs of points (times must be strictly increasing)."""
    slopes = [
        (ys[j] - ys[i]) / (times[j] - times[i])
        for i in range(len(times))
        for j in range(i + 1, len(times))
    ]
    slopes.sort()
    return slopes


def theil_sen(values: Sequence[Optional[float]]) -> Tuple[float, float]:
    """(slope, intercept); intercept = median(y - slope * t)."""
    times, ys = valid_points(values)
    if len(ys) < 2:
        raise ValueError("Theil-Sen needs at least 2 points")
    slope = median(pairwise_slopes(times, ys))
    intercept = median(y - slope * t for t, y in zip(times, ys))
    return slope, intercept


def mk_score(ys: Sequence[float]) -> int:
    s = 0
    for i in range(len(ys)):
        yi = ys[i]
        for j in range(i + 1, len(ys)):
            d = ys[j] - yi
            s += (d > 0) - (d < 0)
    return s


def mk_variance(ys: Sequence[float]) -> float:
    """Var(S) under independence, with the tie correction."""
    n = len(ys)
    ties = sum(c * (c - 1) * (2 * c + 5) for c in Counter(ys).values())
    return (n * (n - 1) * (2 * n + 5) - ties) / 18.0


@dataclass(frozen=True)
class MKResult:
    s: int
    var_s: float
    z: float
    p: float


def mk_test(ys: Sequence[float], factor: float = 1.0) -> MKResult:
    """Two-sided Mann–Kendall test with Var(S) multiplied by `factor` (continuity-corrected z)."""
    s = mk_score(ys)
    var_s = mk_variance(ys) * factor
    if factor <= 0:
        raise ValueError(f"non-positive variance factor {factor}")
    if var_s == 0:  # every value tied: S = 0, nothing to test
        return MKResult(s, 0.0, 0.0, 1.0)
    if s > 0:
        z = (s - 1) / math.sqrt(var_s)
    elif s < 0:
        z = (s + 1) / math.sqrt(var_s)
    else:
        z = 0.0
    return MKResult(s, var_s, z, math.erfc(abs(z) / math.sqrt(2.0)))


def sen_ci(sorted_slopes: Sequence[float], var_s: float, alpha: float = 0.05) -> Tuple[float, float]:
    """Sen (1968) CI for the slope using Var(S); same indexing as scipy.stats.theilslopes."""
    nt = len(sorted_slopes)
    c = z_critical(alpha) * math.sqrt(var_s)
    upper = min(int(round((nt + c) / 2.0)), nt - 1)
    lower = max(int(round((nt - c) / 2.0)) - 1, 0)
    return sorted_slopes[lower], sorted_slopes[upper]


def pct_per_year(slope_per_month: float) -> float:
    """Monthly slope of a log series -> % per year."""
    return (math.exp(slope_per_month * 12.0) - 1.0) * 100.0


MAX_PHI = 0.99


def rank(values: Sequence[float]) -> List[float]:
    """1-based ranks; ties get the average rank."""
    n = len(values)
    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        average = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = average
        i = j + 1
    return ranks


def acf(values: Sequence[float], nlags: int) -> Optional[List[float]]:
    """Sample autocorrelation r_0..r_nlags (biased estimator); None for a constant series."""
    n = len(values)
    mean = sum(values) / n
    centered = [v - mean for v in values]
    c0 = sum(v * v for v in centered) / n
    if c0 == 0:
        return None
    return [
        sum(centered[i] * centered[i + k] for i in range(n - k)) / n / c0
        for k in range(nlags + 1)
    ]


def hamed_rao_factor(rho: Sequence[float], n: int) -> float:
    """n/n* from Hamed & Rao (1998) for autocorrelations rho[0..] (rho[0] = 1)."""
    if n < 3:
        return 1.0
    total = sum(
        (n - k) * (n - k - 1) * (n - k - 2) * rho[k] for k in range(1, min(len(rho), n))
    )
    return 1.0 + 2.0 * total / (n * (n - 1) * (n - 2))


def significant_rank_acf(detrended: Sequence[float], alpha: float = 0.05) -> List[float]:
    """Classic Hamed–Rao input: rank autocorrelations, insignificant lags set to 0.

    Not used by the main method (it under-corrects on short residual segments, see
    references/methodology.md); kept as the reference variant for cross-checks and calibration R1.
    """
    n = len(detrended)
    r = acf(rank(detrended), n - 1)
    if r is None:
        return [1.0]
    bound = z_critical(alpha) / math.sqrt(n)
    return [1.0] + [v if abs(v) > bound else 0.0 for v in r[1:]]


def ar1_phi(segments: Sequence[Sequence[float]]) -> float:
    """Pooled lag-1 autocorrelation of detrended residual segments, bias-corrected, clamped to [0, MAX_PHI].

    Lag products never cross segment boundaries. The sample estimate on a linearly
    detrended segment of length L is biased by about -(2 + 4*phi)/L, which is added back.
    """
    numerator = 0.0
    denominator = 0.0
    points = 0
    used = 0
    for segment in segments:
        if len(segment) < 3:
            continue
        mean = sum(segment) / len(segment)
        centered = [v - mean for v in segment]
        numerator += sum(centered[i] * centered[i + 1] for i in range(len(centered) - 1))
        denominator += sum(v * v for v in centered)
        points += len(segment)
        used += 1
    if used == 0 or denominator == 0:
        return 0.0
    phi = numerator / denominator
    phi += (2.0 + 4.0 * phi) / (points / used)
    return min(MAX_PHI, max(0.0, phi))


def ar1_rho(phi: float, n: int) -> List[float]:
    return [phi ** k for k in range(n)]


MAD_TO_SIGMA = 1.4826


def benjamini_hochberg(pvalues: Sequence[float]) -> List[float]:
    """BH-adjusted p-values in the original order."""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    adjusted = [0.0] * m
    running = 1.0
    for position in range(m - 1, -1, -1):
        i = order[position]
        running = min(running, pvalues[i] * m / (position + 1))
        adjusted[i] = running
    return adjusted


def mad(values: Sequence[float]) -> float:
    """Unscaled median absolute deviation."""
    center = median(values)
    return median(abs(v - center) for v in values)


def robust_z(values: Sequence[Optional[float]]) -> List[Optional[float]]:
    """(x - median) / (1.4826 * MAD); None stays None; zero MAD gives 0 or ±inf."""
    _, ys = valid_points(values)
    center = median(ys)
    scale = MAD_TO_SIGMA * mad(ys)
    out: List[Optional[float]] = []
    for value in values:
        if value is None:
            out.append(None)
        elif scale == 0:
            out.append(0.0 if value == center else math.copysign(math.inf, value - center))
        else:
            out.append((value - center) / scale)
    return out


# --- step versus line (analysis §5, step_like) -------------------------------------------


def _rss2(rows: Sequence[Tuple[float, float, float]]) -> Optional[float]:
    """Residual sum of squares of y ~ b1*x1 + b2*x2 (rows are (x1, x2, y)); None if singular."""
    s11 = sum(a * a for a, _, _ in rows)
    s12 = sum(a * b for a, b, _ in rows)
    s22 = sum(b * b for _, b, _ in rows)
    s1y = sum(a * y for a, _, y in rows)
    s2y = sum(b * y for _, b, y in rows)
    det = s11 * s22 - s12 * s12
    if abs(det) < 1e-12 * max(s11 * s22, 1.0):
        return None
    b1 = (s1y * s22 - s2y * s12) / det
    b2 = (s2y * s11 - s1y * s12) / det
    return sum((y - b1 * a - b2 * b) ** 2 for a, b, y in rows)


def bic(rss: float, m: int, k: int) -> float:
    return m * math.log(max(rss, 1e-300) / m) + k * math.log(m)


@dataclass(frozen=True)
class StepFit:
    gain: float  # BIC_linear - BIC_step: > 0 means the step explains the series better
    at: int  # window position of the first month of the upper (or lower) level


def step_versus_line(values: Sequence[Optional[float]], phi: float, min_segment: int) -> Optional[StepFit]:
    """Two levels with a break point against a linear trend, both on the Cochrane–Orcutt
    prewhitened series y_t - phi*y_(t-1) (BIC assumes independent residuals).

    The break point runs over window positions min_segment .. n - min_segment and counts as a
    parameter: 3 for the step (two levels + the break), 2 for the line. Only pairs of adjacent
    months that both have a value take part. None with fewer than 2 * min_segment such pairs.
    """
    n = len(values)
    pairs = [t for t in range(1, n) if values[t] is not None and values[t - 1] is not None]
    m = len(pairs)
    if m < 2 * min_segment:
        return None
    y = {t: float(values[t]) - phi * float(values[t - 1]) for t in pairs}  # type: ignore[arg-type]
    line = _rss2([(1.0 - phi, t - phi * (t - 1), y[t]) for t in pairs])
    if line is None:
        return None
    best: Optional[Tuple[float, int]] = None
    for k in range(min_segment, n - min_segment + 1):
        rows = []
        for t in pairs:
            now, before = float(t >= k), float(t - 1 >= k)
            rows.append(((1.0 - now) - phi * (1.0 - before), now - phi * before, y[t]))
        rss = _rss2(rows)
        if rss is not None and (best is None or rss < best[0]):
            best = (rss, k)
    if best is None:
        return None
    return StepFit(bic(line, m, 2) - bic(best[0], m, 3), best[1])

