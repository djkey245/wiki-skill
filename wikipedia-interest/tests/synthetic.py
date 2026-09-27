"""Synthetic monthly series with known truth (testing §1). Deterministic for a given seed."""
import math
import random


def ar1_noise(n, sigma, phi, rng):
    """Stationary AR(1) noise with marginal standard deviation sigma."""
    out = []
    e = rng.gauss(0.0, sigma)
    innovation = sigma * math.sqrt(1.0 - phi * phi)
    for _ in range(n):
        out.append(e)
        e = phi * e + rng.gauss(0.0, innovation)
    return out


def make_share_series(
    *,
    n_history,
    n_window,
    trend_pct_yr=0.0,
    history_trend_pct_yr=None,
    seasonal_amp=0.0,
    sigma=0.1,
    phi=0.0,
    seed=0,
    level=100.0,
):
    """exp(level + trend + seasonal sine + AR(1) noise); the history may have its own trend."""
    rng = random.Random(seed)
    n = n_history + n_window
    window_slope = math.log(1.0 + trend_pct_yr / 100.0) / 12.0
    history_slope = (
        window_slope
        if history_trend_pct_yr is None
        else math.log(1.0 + history_trend_pct_yr / 100.0) / 12.0
    )
    noise = ar1_noise(n, sigma, phi, rng)
    current = math.log(level)
    values = []
    for t in range(n):
        if t > 0:
            current += history_slope if t <= n_history else window_slope
        values.append(math.exp(current + seasonal_amp * math.sin(2 * math.pi * t / 12.0) + noise[t]))
    return values
