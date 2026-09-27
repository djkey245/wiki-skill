import math

import pytest

from wikitrends import seasonal, stats


def sine_logs(n, amp=0.5, level=4.0, slope=0.0):
    return [level + slope * t + amp * math.sin(2 * math.pi * t / 12) for t in range(n)]


def test_log_series_skips_zero_and_missing():
    assert seasonal.log_series([1.0, 0.0, None, math.e]) == [0.0, None, None, 1.0]


def test_calendar_index():
    assert seasonal.calendar_index("2020-01", 0) == 0
    assert seasonal.calendar_index("2020-03", 10) == 0  # 2021-01
    assert seasonal.calendar_index("2019-12", 1) == 0


def test_centered_ma_of_line_is_the_line_and_edges_are_none():
    logs = [2.0 + 0.1 * t for t in range(30)]
    ma = seasonal.centered_ma12(logs)
    assert ma[:6] == [None] * 6 and ma[-6:] == [None] * 6
    for t in range(6, 24):
        assert ma[t] == pytest.approx(logs[t])


def test_centered_ma_needs_all_13_points():
    logs = [1.0] * 30
    logs[10] = None
    ma = seasonal.centered_ma12(logs)
    assert all(ma[t] is None for t in range(4, 17))
    assert ma[17] == pytest.approx(1.0)


def test_profile_is_centered_and_recovers_pure_seasonality():
    logs = sine_logs(60)
    profile = seasonal.seasonal_profile(logs, "2020-01")
    assert sum(profile) == pytest.approx(0.0, abs=1e-12)
    for month in range(12):
        assert profile[month] == pytest.approx(0.5 * math.sin(2 * math.pi * month / 12), abs=1e-9)


def test_profile_follows_calendar_not_position():
    logs = sine_logs(60)
    shifted = seasonal.seasonal_profile(logs, "2020-04")
    # position 0 is April (index 3)
    assert shifted[3] == pytest.approx(0.0, abs=1e-9)
    assert shifted[6] == pytest.approx(0.5, abs=1e-9)


def test_deseasonalize_removes_seasonality_and_keeps_trend():
    logs = sine_logs(60, slope=0.01)
    profile = seasonal.seasonal_profile(logs, "2020-01")
    flat = seasonal.deseasonalize(logs, "2020-01", profile)
    slope, intercept = stats.theil_sen(flat)
    assert slope == pytest.approx(0.01, abs=1e-12)
    assert intercept == pytest.approx(4.0, abs=1e-9)
    assert max(abs(v - (4.0 + 0.01 * t)) for t, v in enumerate(flat)) < 1e-9


def test_deseasonalize_keeps_missing():
    logs = sine_logs(36)
    logs[5] = None
    profile = seasonal.seasonal_profile(logs, "2020-01")
    assert seasonal.deseasonalize(logs, "2020-01", profile)[5] is None


def test_local_trend_segments_fit_each_part_separately():
    # history rises, window is flat: both residual segments are ~0
    series = [0.05 * t for t in range(36)] + [0.05 * 35] * 24
    segments = seasonal.local_trend_segments(series, 36)
    assert [len(s) for s in segments] == [36, 24]
    assert max(abs(v) for s in segments for v in s) < 1e-12


def test_local_trend_segments_skip_too_short_history():
    series = [1.0, 2.0] + [float(t) for t in range(24)]
    assert [len(s) for s in seasonal.local_trend_segments(series, 2)] == [24]
