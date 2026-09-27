import random

import pytest

from synthetic import make_share_series
from wikitrends import seasonal, stats, trend
from wikitrends import thresholds as T

FIRST = "2020-01"


def est(values, window_start=36):
    return trend.estimate(values, FIRST, window_start)


def false_trend_rate(n_series, **kwargs):
    hits = 0
    for seed in range(n_series):
        values = make_share_series(n_history=36, n_window=24, seed=seed, **kwargs)
        hits += est(values).p < T.ALPHA
    return hits / n_series


def test_linear_growth_with_seasonality_is_detected():
    values = make_share_series(n_history=36, n_window=24, trend_pct_yr=20, seasonal_amp=0.2, sigma=0.05, seed=1)
    result = est(values)
    low, high = result.growth_ci
    assert result.p < T.ALPHA
    assert low < 20 < high
    assert result.growth_pct_yr == pytest.approx(20, abs=5)
    assert not result.short_history
    assert result.n == 24


def test_seasonality_alone_is_not_a_trend():
    assert false_trend_rate(300, seasonal_amp=0.3, sigma=0.1) <= 0.10


def test_ar1_noise_false_trend_rate_within_r1_budget():
    # calibration R1: <= 10% false growing/declining at phi = 0.5 (prototype: 7.8%)
    assert false_trend_rate(600, sigma=0.15, phi=0.5) <= 0.10


def test_strong_autocorrelation_hits_the_ceiling():
    capped = 0
    for seed in range(200):
        values = make_share_series(n_history=36, n_window=24, sigma=0.15, phi=0.95, seed=seed)
        result = est(values)
        capped += result.autocorr_capped
        assert result.inflation == T.MAX_VARIANCE_INFLATION or not result.autocorr_capped
    assert capped / 200 >= 0.7  # prototype: 84%


def test_break_in_history_does_not_inflate_phi():
    values = make_share_series(n_history=36, n_window=24, history_trend_pct_yr=40, sigma=0.05, seed=2)
    result = est(values)
    assert result.phi < 0.3
    # the same residuals from ONE line over the whole series look strongly autocorrelated
    logs = seasonal.log_series(values)
    deseasonalized = seasonal.deseasonalize(logs, FIRST, seasonal.seasonal_profile(logs, FIRST))
    naive = seasonal.local_trend_segments(deseasonalized, 0)
    assert stats.ar1_phi(naive) > 0.6


def test_flat_low_noise_series_has_narrow_ci():
    values = make_share_series(n_history=36, n_window=36, sigma=0.01, seed=4)
    low, high = est(values).growth_ci
    assert -5 < low and high < 5


def test_history_length_and_short_history_flag():
    long_history = make_share_series(n_history=36, n_window=48, sigma=0.1, seed=5)
    assert est(long_history).history_months == 36
    assert not est(long_history).short_history
    short = make_share_series(n_history=12, n_window=24, sigma=0.1, seed=5)
    result = trend.estimate(short, FIRST, 12)
    assert result.history_months == 12
    assert result.short_history


def test_zero_months_are_skipped():
    values = make_share_series(n_history=36, n_window=24, trend_pct_yr=20, sigma=0.05, seed=6)
    values[40] = 0.0
    values[50] = 0.0
    result = est(values)
    assert result.n == 22
    assert result.deseasonalized[4] is None


def test_too_few_points_raise():
    values = [100.0] * 36 + [0.0] * 22 + [100.0, 100.0]
    with pytest.raises(trend.NotEnoughPoints):
        est(values)


def test_test_and_ci_agree():
    # testing §1 / calibration R1: "CI excludes 0 <=> p < 0.05" in >= 99% of series
    rng = random.Random(1)
    agree = 0
    for i in range(1000):
        values = make_share_series(
            n_history=36, n_window=24, trend_pct_yr=rng.uniform(-30, 30), sigma=0.15, phi=0.3, seed=10_000 + i
        )
        result = est(values)
        excludes_zero = result.ci_low > 0 or result.ci_high < 0
        agree += excludes_zero == (result.p < T.ALPHA)
    assert agree / 1000 >= 0.99
