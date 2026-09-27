"""Reference checks against scipy / pymannkendall (dev dependencies; skipped without them)."""
import math
import random

import pytest

np = pytest.importorskip("numpy")
scipy_stats = pytest.importorskip("scipy.stats")
mk = pytest.importorskip("pymannkendall")

from wikitrends import stats  # noqa: E402


def random_series(rng, n, ties):
    ys = [rng.gauss(0.03 * rng.uniform(-2, 2) * t, 1.0) for t in range(n)]
    return [round(y, 1) for y in ys] if ties else ys


@pytest.mark.parametrize("ties", [False, True])
def test_theil_sen_and_sen_ci_match_scipy(ties):
    rng = random.Random(11 if ties else 12)
    for _ in range(200):
        n = rng.choice([12, 18, 24, 36])
        ys = random_series(rng, n, ties)
        times = list(range(n))
        ref = scipy_stats.theilslopes(ys, times, alpha=0.95)
        slope, _ = stats.theil_sen(ys)
        low, high = stats.sen_ci(stats.pairwise_slopes(times, ys), stats.mk_variance(ys), 0.05)
        assert slope == pytest.approx(ref[0], abs=1e-12)
        assert low == pytest.approx(ref[2], abs=1e-12)
        assert high == pytest.approx(ref[3], abs=1e-12)


@pytest.mark.parametrize("ties", [False, True])
def test_original_mann_kendall_matches_pymannkendall(ties):
    rng = random.Random(21 if ties else 22)
    for _ in range(200):
        ys = random_series(rng, rng.choice([12, 24, 36]), ties)
        ref = mk.original_test(ys)
        ours = stats.mk_test(ys)
        assert ours.s == ref.s
        assert ours.var_s == pytest.approx(ref.var_s)
        assert ours.z == pytest.approx(ref.z, abs=1e-12)
        assert ours.p == pytest.approx(ref.p, abs=1e-12)


def test_hamed_rao_formula_matches_pymannkendall():
    # continuous data: ranks of detrended values must not depend on float rounding
    rng = random.Random(31)
    checked = 0
    for _ in range(200):
        n = rng.choice([12, 24, 36])
        ys = [rng.gauss(0.05 * t, 1.0) + 0.3 * math.sin(t) for t in range(n)]
        slope, _ = stats.theil_sen(ys)
        rho = stats.significant_rank_acf([y - (k + 1) * slope for k, y in enumerate(ys)], 0.05)
        factor = stats.hamed_rao_factor(rho, n)
        if factor <= 0:  # pymannkendall returns nan here; the main method clamps to >= 1
            continue
        ref = mk.hamed_rao_modification_test(ys)
        ours = stats.mk_test(ys, factor)
        assert ours.var_s == pytest.approx(ref.var_s, rel=1e-9)
        assert ours.p == pytest.approx(ref.p, abs=1e-9)
        checked += 1
    assert checked >= 150


def test_benjamini_hochberg_matches_scipy():
    if not hasattr(scipy_stats, "false_discovery_control"):
        pytest.skip("scipy < 1.11")
    rng = random.Random(41)
    for _ in range(100):
        p = [rng.random() for _ in range(rng.randint(1, 15))]
        assert stats.benjamini_hochberg(p) == pytest.approx(list(scipy_stats.false_discovery_control(p)))
