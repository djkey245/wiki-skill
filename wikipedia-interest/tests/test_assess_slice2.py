"""Slice-2 synthetic scenarios of testing §1: the remaining reasons of analysis §5 and options.metric."""
import math
import random

from cells import article, cell_of, series
from synthetic import ar1_noise
from wikitrends import breaks, metrics
from wikitrends import confidence as C


def assess(articles, seeds=(), metric="share", **kwargs):
    data, baskets, _ = cell_of(articles, seeds, **kwargs)
    cell = metrics.compute_cell(data, baskets, metric)
    return cell, C.assess(data, baskets, cell, list(seeds))


def codes(result):
    return {r.code for r in result.reasons}


def stepped(level, seed, *, step, at=48, sigma=0.15, phi=0.0, trend_pct_yr=0.0, n=60):
    """exp(log level + trend + step from `at` + seasonality + AR(1) noise): 36 months of history, 24 of window."""
    noise = ar1_noise(n, sigma, phi, random.Random(seed))
    slope = math.log(1 + trend_pct_yr / 100) / 12
    return [level * math.exp(slope * t + (step if t >= at else 0.0) + 0.2 * math.sin(2 * math.pi * t / 12) + noise[t])
            for t in range(n)]


# --- step_like ---------------------------------------------------------------------------


def test_a_step_between_the_years_is_step_like_not_growing_with_high():
    _, result = assess({"Q1": article("Q1", stepped(5000, 0, step=0.4, sigma=0.08))})
    assert "step_like" in codes(result)
    assert not (result.direction == "growing" and result.confidence == "high")
    detail = next(r.detail for r in result.reasons if r.code == "step_like")
    assert detail["month"] == "2024-01"  # the 13th month of the window 2023-01 .. 2024-12


def test_a_step_with_realistic_noise_is_mostly_step_like():
    hits = sum("step_like" in codes(assess({"Q1": article("Q1", stepped(5000, s, step=0.6))})[1]) for s in range(60))
    assert hits / 60 >= 0.6


def test_linear_trends_rarely_look_like_steps():
    """sigma = 0.15 with and without AR(1) phi = 0.5: false step_like <= 10 % (testing §1)."""
    for phi in (0.0, 0.5):
        hits = sum("step_like" in codes(assess({"Q1": article("Q1", stepped(5000, s, step=0.0, phi=phi, trend_pct_yr=20))})[1])
                   for s in range(100))
        assert hits / 100 <= 0.10, (phi, hits)


# --- recent_only and trend_reversal -----------------------------------------------------------


def test_a_lasting_shift_at_the_end_is_recent_only_not_spike_driven():
    values = stepped(5000, 1, step=0.5, at=57, sigma=0.03, trend_pct_yr=4)
    _, result = assess({"Q1": article("Q1", values)})
    assert "recent_only" in codes(result) and "spike_driven" not in codes(result)


def test_an_isolated_spike_near_the_end_is_not_recent_only():
    values = stepped(5000, 4, step=0.0, sigma=0.02, trend_pct_yr=8)
    values[57] *= 4  # the third month from the end, back to the level afterwards
    _, result = assess({"Q1": article("Q1", values)})
    assert "recent_only" not in codes(result)


def test_an_even_trend_is_rarely_recent_only():
    hits = total = 0
    for s in range(100):
        _, result = assess({"Q1": article("Q1", stepped(5000, s, step=0.0, trend_pct_yr=25, sigma=0.12))})
        if result.direction == "growing":
            total += 1
            hits += "recent_only" in codes(result)
    assert total >= 50 and hits / total <= 0.15, (hits, total)


def test_growth_turning_into_a_fall_is_trend_reversal():
    values = stepped(5000, 5, step=0.0, sigma=0.02, trend_pct_yr=30)
    values[54:] = [v * math.exp(-0.15 * (t + 1)) for t, v in enumerate(values[54:])]
    _, result = assess({"Q1": article("Q1", values)})
    assert result.category == "up" and "trend_reversal" in codes(result)


def test_trend_reversal_rules():
    assert C.check_trend_reversal("up", -15.0).code == "trend_reversal"
    assert C.check_trend_reversal("down", 12.0).code == "trend_reversal"
    assert C.check_trend_reversal("up", -8.0) is None
    assert C.check_trend_reversal("flat", -30.0) is None  # the sign of a slope near zero means nothing
    assert C.check_trend_reversal("up", None) is None


# --- redirect_heavy, seed_divergence, data_break, data_revised ---------------------------------


def test_redirect_heavy_is_low_and_leaves_the_confidence():
    _, plain = assess({f"Q{i}": article(f"Q{i}", series(3000, i, trend_pct_yr=20, sigma=0.05)) for i in range(1, 5)})
    _, heavy = assess({f"Q{i}": article(f"Q{i}", series(3000, i, trend_pct_yr=20, sigma=0.05), redirect_fraction=0.6)
                       for i in range(1, 5)})
    assert "redirect_heavy" in codes(heavy) and "redirect_heavy" not in codes(plain)
    assert heavy.confidence == plain.confidence


def test_seed_going_the_other_way_is_seed_divergence():
    articles = {f"Q{i}": article(f"Q{i}", series(3000, i, trend_pct_yr=30, sigma=0.03)) for i in range(1, 5)}
    articles["Q9"] = article("Q9", series(2000, 9, trend_pct_yr=-30, sigma=0.03))
    _, result = assess(articles, seeds=["Q9"])
    assert result.category == "up" and "seed_divergence" in codes(result) and result.seed_divergence == "diverges"
    _, agrees = assess(articles, seeds=["Q1"])
    assert "seed_divergence" not in codes(agrees) and agrees.seed_divergence == "agrees"
    _, absent = assess(articles)  # no seed in this language
    assert absent.seed_divergence == "not_checked"


def test_data_break_and_data_revised_reasons():
    data, baskets, cell = cell_of({"Q1": article("Q1", stepped(5000, 7, step=0.4, at=48, sigma=0.03))})
    known = (breaks.Break("b", "2024-01", "https://example.org"),)
    result = C.assess(data, baskets, cell, [], known_breaks=known, revised=["2023-05"])
    assert "data_break" in codes(result)
    assert result.breaks[0]["status"] == "significant"
    assert next(r for r in result.reasons if r.code == "data_revised").severity == "info"
    elsewhere = C.assess(data, baskets, cell, [], known_breaks=(breaks.Break("b", "2023-07", "https://example.org"),))
    assert "data_break" not in codes(elsewhere) and elsewhere.breaks[0]["status"] == "not_significant"


# --- metrics: share against views ------------------------------------------------------------


def falling_project(n=60, pct_yr=-25.0):
    return [int(100_000_000 * (1 + pct_yr / 100) ** (t / 12)) for t in range(n)]


def test_share_up_views_down_is_metric_disagreement_with_project_trend():
    articles = {f"Q{i}": article(f"Q{i}", series(3000, i, trend_pct_yr=-8, sigma=0.02)) for i in range(1, 4)}
    cell, result = assess(articles, denominator=falling_project())
    assert cell.growth_pct_yr > 5 and cell.raw_growth_pct_yr < -5
    assert {"metric_disagreement", "project_trend"} <= codes(result)
    assert result.confidence == "high"  # info reasons never lower it


def test_stable_views_on_a_falling_edition_give_only_project_trend():
    articles = {f"Q{i}": article(f"Q{i}", series(3000, i, trend_pct_yr=0, sigma=0.02)) for i in range(1, 4)}
    _, result = assess(articles, denominator=falling_project())
    assert "project_trend" in codes(result) and "metric_disagreement" not in codes(result)


def test_metric_views_swaps_the_trend_and_its_control():
    articles = {f"Q{i}": article(f"Q{i}", series(3000, i, trend_pct_yr=-8, sigma=0.02)) for i in range(1, 4)}
    share, _ = assess(articles, denominator=falling_project())
    views, result = assess(articles, metric="views", denominator=falling_project())
    assert views.metric == "views" and views.growth_pct_yr == share.raw_growth_pct_yr
    assert views.raw_growth_pct_yr == share.growth_pct_yr
    assert views.index[0] is not None and views.values[0] == float(views.views[0])
    assert result.direction == "declining" and "metric_disagreement" in codes(result)
