"""Step versus line on the prewhitened series (analysis §5, step_like; testing §1)."""
import math
import random

import pytest

from synthetic import ar1_noise
from wikitrends import stats
from wikitrends import thresholds as T


def test_rss2_exact_fit_and_singular():
    rows = [(1.0, float(t), 2.0 + 0.5 * t) for t in range(10)]
    assert stats._rss2(rows) < 1e-18
    assert stats._rss2([(1.0, 1.0, float(t)) for t in range(5)]) is None


def test_bic_penalises_parameters():
    assert stats.bic(1.0, 20, 3) - stats.bic(1.0, 20, 2) == pytest.approx(math.log(20))


def test_clean_step_beats_the_line_and_is_located():
    values = [0.0] * 12 + [0.5] * 12
    fit = stats.step_versus_line([v + 0.01 * (-1) ** t for t, v in enumerate(values)], 0.0, T.STEP_MIN_SEGMENT)
    assert fit.gain > T.BIC_STEP_MARGIN and fit.at == 12


def test_clean_line_is_not_a_step():
    rng = random.Random(1)
    values = [0.02 * t + rng.gauss(0, 0.01) for t in range(24)]
    assert stats.step_versus_line(values, 0.0, T.STEP_MIN_SEGMENT).gain < T.BIC_STEP_MARGIN


def test_gaps_and_short_series():
    values = [0.0] * 12 + [0.5] * 12
    values[3] = values[15] = None
    assert stats.step_versus_line(values, 0.3, T.STEP_MIN_SEGMENT).at == 12
    assert stats.step_versus_line([0.1] * 11, 0.0, T.STEP_MIN_SEGMENT) is None


def test_prewhitening_lowers_false_steps_on_ar1_lines():
    """Without prewhitening a persistent line looks like a step far more often; the rate on
    the full method (only significant trends are checked) is in test_assess_slice2."""
    for phi in (0.5, 0.7):
        white = raw = 0
        for seed in range(200):
            values = [0.015 * t + e for t, e in enumerate(ar1_noise(24, 0.15, phi, random.Random(seed)))]
            white += stats.step_versus_line(values, phi, T.STEP_MIN_SEGMENT).gain > T.BIC_STEP_MARGIN
            raw += stats.step_versus_line(values, 0.0, T.STEP_MIN_SEGMENT).gain > T.BIC_STEP_MARGIN
        assert white < raw * 0.6, (phi, white, raw)
