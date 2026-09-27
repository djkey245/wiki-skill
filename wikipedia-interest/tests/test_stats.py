import math

import pytest

from wikitrends import stats


def test_valid_points_skips_none_and_nan():
    assert stats.valid_points([1.0, None, 3.0, float("nan"), 5.0]) == ([0, 2, 4], [1.0, 3.0, 5.0])


def test_theil_sen_exact_line_with_gaps():
    values = [1.0 + 2.0 * t if t not in (3, 7) else None for t in range(12)]
    slope, intercept = stats.theil_sen(values)
    assert slope == pytest.approx(2.0)
    assert intercept == pytest.approx(1.0)


def test_theil_sen_robust_to_outlier():
    values = [float(t) for t in range(20)]
    values[10] = 1000.0
    slope, _ = stats.theil_sen(values)
    assert slope == pytest.approx(1.0)


def test_theil_sen_needs_two_points():
    with pytest.raises(ValueError):
        stats.theil_sen([1.0, None])


def test_mk_score_and_variance():
    assert stats.mk_score([1, 2, 3, 4]) == 6
    assert stats.mk_score([4, 3, 2, 1]) == -6
    assert stats.mk_score([1, 1, 1]) == 0
    assert stats.mk_variance([1, 2, 3, 4]) == pytest.approx(4 * 3 * 13 / 18)
    # one tie group of size 2: subtract 2*1*9
    assert stats.mk_variance([1, 2, 2, 4]) == pytest.approx((4 * 3 * 13 - 18) / 18)


def test_mk_test_continuity_correction_and_p():
    result = stats.mk_test([1.0, 2.0, 3.0, 4.0, 5.0])
    assert result.s == 10
    assert result.var_s == pytest.approx(5 * 4 * 15 / 18)
    assert result.z == pytest.approx(9 / math.sqrt(50 / 3))
    assert result.p == pytest.approx(math.erfc(result.z / math.sqrt(2)))


def test_mk_test_factor_scales_variance_and_rejects_non_positive():
    base = stats.mk_test([1.0, 3.0, 2.0, 5.0, 4.0])
    scaled = stats.mk_test([1.0, 3.0, 2.0, 5.0, 4.0], factor=2.0)
    assert scaled.var_s == pytest.approx(2 * base.var_s)
    assert scaled.p > base.p
    with pytest.raises(ValueError):
        stats.mk_test([1.0, 2.0, 3.0], factor=-1.0)


def test_sen_ci_brackets_slope_and_widens_with_variance():
    times = list(range(24))
    ys = [0.1 * t + (0.5 if t % 2 else -0.5) for t in times]
    slopes = stats.pairwise_slopes(times, ys)
    var_s = stats.mk_variance(ys)
    low, high = stats.sen_ci(slopes, var_s)
    wide_low, wide_high = stats.sen_ci(slopes, 4 * var_s)
    assert low < 0.1 < high
    assert wide_low <= low and high <= wide_high


def test_pct_per_year():
    assert stats.pct_per_year(math.log(1.2) / 12) == pytest.approx(20.0)
    assert stats.pct_per_year(0.0) == 0.0


def test_rank_average_ties():
    assert stats.rank([10, 20, 20, 5]) == [2.0, 3.5, 3.5, 1.0]


def test_acf_alternating_and_constant():
    r = stats.acf([1.0, -1.0] * 10, 2)
    assert r[0] == pytest.approx(1.0)
    assert r[1] == pytest.approx(-0.95)
    assert r[2] == pytest.approx(0.9)
    assert stats.acf([3.0] * 5, 2) is None


def test_hamed_rao_factor_no_autocorrelation_is_one():
    assert stats.hamed_rao_factor([1.0, 0.0, 0.0], 24) == 1.0
    assert stats.hamed_rao_factor([1.0], 2) == 1.0


def test_hamed_rao_factor_ar1_grows_with_phi():
    n = 24
    f3 = stats.hamed_rao_factor(stats.ar1_rho(0.3, n), n)
    f5 = stats.hamed_rao_factor(stats.ar1_rho(0.5, n), n)
    f9 = stats.hamed_rao_factor(stats.ar1_rho(0.9, n), n)
    assert 1.0 < f3 < f5 < f9
    assert f5 == pytest.approx(2.5415, abs=1e-4)


def test_ar1_phi_white_noise_near_zero_and_ignores_short_segments():
    import random

    rng = random.Random(3)
    segments = [[rng.gauss(0, 1) for _ in range(36)], [rng.gauss(0, 1) for _ in range(24)], [1.0, 2.0]]
    assert 0.0 <= stats.ar1_phi(segments) < 0.3
    assert stats.ar1_phi([]) == 0.0
    assert stats.ar1_phi([[1.0, 1.0, 1.0]]) == 0.0


def test_ar1_phi_smooth_series_is_high_and_clamped():
    segment = [math.sin(t / 10.0) for t in range(60)]
    assert stats.ar1_phi([segment]) == stats.MAX_PHI


def test_benjamini_hochberg_known_values():
    assert stats.benjamini_hochberg([0.01, 0.04, 0.03, 0.005]) == pytest.approx([0.02, 0.04, 0.04, 0.02])
    assert stats.benjamini_hochberg([0.9, 0.95]) == pytest.approx([0.95, 0.95])
    assert stats.benjamini_hochberg([]) == []


def test_mad_and_robust_z():
    assert stats.mad([1, 2, 3, 4, 100]) == 1
    z = stats.robust_z([1.0, 2.0, 3.0, 4.0, 100.0, None])
    assert z[2] == 0.0
    assert z[4] == pytest.approx(97 / 1.4826)
    assert z[5] is None


def test_robust_z_zero_mad():
    assert stats.robust_z([5.0, 5.0, 5.0, 9.0]) == [0.0, 0.0, 0.0, math.inf]


def test_mk_test_all_tied_is_not_significant():
    result = stats.mk_test([2.0] * 24, factor=1.5)
    assert (result.s, result.var_s, result.p) == (0, 0.0, 1.0)
    assert stats.sen_ci(stats.pairwise_slopes(list(range(24)), [2.0] * 24), result.var_s) == (0.0, 0.0)
