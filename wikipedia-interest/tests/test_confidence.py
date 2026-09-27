import math

import pytest

from wikitrends import confidence as C
from wikitrends import thresholds as T
from wikitrends import trend


def fake_estimate(growth, ci, p, **overrides):
    """A TrendEstimate with the given growth %/yr, growth CI and p."""
    to_slope = lambda g: math.log(1 + g / 100) / 12  # noqa: E731
    fields = dict(
        slope=to_slope(growth), ci_low=to_slope(ci[0]), ci_high=to_slope(ci[1]), p=p, s=0, var_s=1.0, n=24,
        phi=0.0, inflation_raw=1.0, inflation=1.0, autocorr_capped=False, history_months=36,
        short_history=False, deseasonalized=[],
    )
    fields.update(overrides)
    return trend.TrendEstimate(**fields)


@pytest.mark.parametrize(
    "growth, expected",
    [(5.0, "up"), (4.99, "flat"), (0.0, "flat"), (-4.99, "flat"), (-5.0, "down"), (30.0, "up"), (-30.0, "down")],
)
def test_point_category(growth, expected):
    assert C.point_category(growth) == expected


@pytest.mark.parametrize(
    "growth, ci, p, direction, codes",
    [
        (3.0, (1.0, 4.0), 0.01, "stable", ["small_effect"]),  # significant, but inside the corridor
        (0.5, (-4.0, 4.5), 0.60, "stable", []),
        (-3.0, (-4.9, -1.0), 0.01, "stable", ["small_effect"]),
        (6.0, (1.0, 11.0), 0.02, "growing", []),  # the CI need not clear F (calibration R26)
        (-12.0, (-20.0, -4.0), 0.001, "declining", []),
        (3.0, (0.5, 7.0), 0.03, "small_effect", ["small_effect"]),
        (-3.0, (-8.0, -0.5), 0.03, "small_effect", ["small_effect"]),
        (8.0, (-2.0, 18.0), 0.20, "unclear", ["not_significant"]),
        (0.0, (-6.0, 6.0), 0.90, "unclear", ["not_significant"]),
    ],
)
def test_direction_table(growth, ci, p, direction, codes):
    got, reasons = C.direction_of(fake_estimate(growth, ci, p))
    assert got == direction
    assert [r.code for r in reasons] == codes
    assert all(r.severity == C.SEVERITY[r.code] for r in reasons)


def reasons(*codes):
    return [C.Reason(code, severity) for code, severity in codes]


@pytest.mark.parametrize(
    "direction, rs, flags, expected",
    [
        ("growing", [], {}, ("growing", "high")),
        ("stable", reasons(("small_effect", "info"), ("redirect_heavy", "low")), {}, ("stable", "high")),
        ("growing", reasons(("thin_basket", "medium")), {}, ("growing", "high")),
        ("growing", reasons(("thin_basket", "medium"), ("short_window", "medium")), {}, ("growing", "medium")),
        ("growing", reasons(("spike_driven", "high")), {}, ("growing", "medium")),
        ("growing", reasons(("spike_driven", "high"), ("thin_basket", "medium")), {}, ("growing", "low")),
        ("growing", reasons(("spike_driven", "high"), ("concentrated", "high")), {}, ("growing", "low")),
        ("growing", reasons(("a", "medium"), ("b", "medium"), ("c", "medium")), {}, ("growing", "low")),
        ("growing", reasons(("low_volume", "blocking")), {}, ("unclear", "low")),
        ("unclear", reasons(("not_significant", "blocking")), {}, ("unclear", "low")),
        ("small_effect", reasons(("small_effect", "info")), {}, ("small_effect", "high")),
        # window < 24 months: at most medium
        ("growing", reasons(("short_window", "medium")), {"short_window": True}, ("growing", "medium")),
        # slice-1 deviation (overview §3): short_history caps at low
        ("growing", reasons(("short_history", "medium")), {"short_history": True}, ("growing", "low")),
    ],
)
def test_overall_confidence(direction, rs, flags, expected):
    kwargs = {"short_window": False, "short_history": False}
    kwargs.update(flags)
    assert C.overall_confidence(direction, rs, **kwargs) == expected


@pytest.mark.parametrize(
    "views, zeros, severity",
    [(5000, 0.0, None), (999, 0.0, "high"), (1000, 0.0, None), (99, 0.0, "blocking"), (5000, 0.25, "blocking"), (5000, 0.2, None)],
)
def test_low_volume(views, zeros, severity):
    r = C.check_low_volume(views, zeros, "common")
    assert (r and r.severity) == severity
    if r:
        assert r.detail["basket"] == "common" and r.detail["monthly_views"] == views


def test_spike_months_isolated_vs_shift_at_the_end():
    flat = [0.01 * ((t * 7) % 5 - 2) for t in range(24)]
    isolated = list(flat)
    isolated[21] += 1.0  # third month from the end, the series returns to its level
    assert C.spike_months(isolated) == [21]
    shift = list(flat)
    for t in (21, 22, 23):
        shift[t] += 1.0  # a shift at the end without return: recent_only (slice 2), not a spike
    assert C.spike_months(shift) == []
    both = list(shift)
    both[15] += 1.0
    assert C.spike_months(both) == [15]
    with_gap = list(shift)
    with_gap[22] = None
    assert C.spike_months(with_gap) == []
