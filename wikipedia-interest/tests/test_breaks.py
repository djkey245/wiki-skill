"""Known data breaks and the level-shift check (data §6, testing §1)."""
import math
import random

from synthetic import ar1_noise
from wikitrends import breaks, trend


def test_level_shift_and_typical_shift():
    series = [0.0] * 10 + [1.0] * 10
    assert breaks.level_shift(series, 10) == 1.0
    assert breaks.level_shift(series, 3) is None  # fewer than 6 months before
    assert breaks.level_shift([None] * 4 + [0.0] * 8, 6) is None  # most months of a side missing
    assert breaks.typical_shift([0.0, 1.0] * 10) == 0.0
    assert breaks.typical_shift([0.0] * 11) is None


def test_in_window():
    assert [b.id for b in breaks.in_window("2024-01", "2025-12")] == ["bot_detection_2025"]
    assert breaks.in_window("2025-03", "2026-08") == []  # the first month of the window is not a break inside it
    assert [b.id for b in breaks.in_window("2019-01", "2025-03")] == ["automated_agent", "bot_detection_2025"]


def deseasonalized(step_at, step=0.3, seed=0, sigma=0.15):
    rng = random.Random(seed)
    noise = ar1_noise(60, sigma, 0.0, rng)
    values = [math.exp(4.6 + (step if t >= step_at else 0.0) + 0.2 * math.sin(2 * math.pi * t / 12) + noise[t])
              for t in range(60)]
    est = trend.estimate(values, "2021-03", 36)
    return est.deseasonalized, est.history


def test_a_step_at_the_break_is_significant_and_elsewhere_it_is_not():
    """36 months of history from 2021-03, window 2024-03 .. 2026-02, known break 2025-03 (t = 48)."""
    known = (breaks.Break("b", "2025-03", "https://example.org"),)
    hits = away = 0
    for seed in range(100):
        window, history = deseasonalized(48, seed=seed)
        hits += breaks.check(window, history, "2024-03", known)[0]["status"] == "significant"
        window, history = deseasonalized(40, seed=seed)  # the same step 8 months earlier
        away += breaks.check(window, history, "2024-03", known)[0]["status"] == "significant"
    assert hits >= 75 and away <= 10, (hits, away)


def test_clean_series_rarely_flag_a_break():
    known = (breaks.Break("b", "2025-03", "https://example.org"),)
    flagged = sum(breaks.check(*deseasonalized(0, step=0.0, seed=s), "2024-03", known)[0]["status"] == "significant"
                  for s in range(200))
    assert flagged / 200 <= 0.10


def test_not_checked_near_the_window_end_or_without_history():
    known = (breaks.Break("b", "2026-01", "https://example.org"),)
    window, history = deseasonalized(0, step=0.0)
    assert breaks.check(window, history, "2024-03", known) == [{"id": "b", "month": "2026-01", "status": "not_checked"}]
    known = (breaks.Break("b", "2025-03", "https://example.org"),)
    assert breaks.check(window, history[-8:], "2024-03", known)[0]["status"] == "not_checked"
