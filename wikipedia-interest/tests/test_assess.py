"""Slice-1 synthetic scenarios of testing §1 for metrics + confidence (deterministic seeds)."""
import pytest

from cells import article, cell_of, lang_data, series
from wikitrends import confidence as C
from wikitrends import metrics


def assess(articles, seeds=(), **kwargs):
    data, baskets, cell = cell_of(articles, seeds, **kwargs)
    return cell, C.assess(data, baskets, cell, list(seeds))


def codes(result):
    return {r.code for r in result.reasons}


def basket(n, *, level=3000, seed0=0, **kwargs):
    return {f"Q{i}": article(f"Q{i}", series(level, seed0 + i, **kwargs)) for i in range(1, n + 1)}


def test_growth_with_seasonality_is_growing_with_high_confidence():
    cell, result = assess(basket(5, trend_pct_yr=20, seasonal_amp=0.2, sigma=0.05))
    low, high = cell.trend.growth_ci
    assert result.direction == "growing" and result.confidence == "high"
    assert low < 20 < high
    assert codes(result) == set()


def test_seasonality_alone_is_never_growing():
    for seed in range(20):
        _, result = assess(basket(4, seed0=10 * seed, seasonal_amp=0.3, sigma=0.1))
        assert result.direction != "growing"


def test_flat_low_noise_36_months_is_stable_high():
    _, result = assess(basket(4, n_window=36, sigma=0.01))
    assert (result.direction, result.confidence) == ("stable", "high")


def test_small_significant_growth_inside_corridor_is_stable_with_small_effect():
    cell, result = assess(basket(4, n_window=36, trend_pct_yr=3, sigma=0.01))
    assert cell.trend.p < 0.05
    assert (result.direction, result.confidence) == ("stable", "high")
    assert codes(result) == {"small_effect"}


def test_several_spikes_near_the_boundary_give_spike_driven():
    # four ×3 spikes in the second half of a +4.5 %/yr series push Theil–Sen over +5 %/yr
    hits = 0
    for seed in range(50):
        values = series(5000, seed, trend_pct_yr=4.5, sigma=0.01)
        for t in (13, 15, 17, 19):
            values[36 + t] *= 3
        _, result = assess({"Q1": article("Q1", values)})
        hits += "spike_driven" in codes(result)
    assert hits / 50 >= 0.6  # prototype: 76%


def test_spikes_do_not_move_a_noise_free_trend():
    values = series(5000, 0, trend_pct_yr=4.5, sigma=0.0)
    for t in (13, 15, 17, 19):
        values[36 + t] *= 3
    cell, result = assess({"Q1": article("Q1", values)})
    assert cell.growth_pct_yr == pytest.approx(4.5, abs=0.01)  # views are rounded to integers
    assert "spike_driven" not in codes(result)


def test_flat_series_with_a_viral_month_is_not_spike_driven():
    # +0.3 %/yr: removing the spike keeps the category flat; the relative rule does not apply to flat
    values = series(5000, 1, trend_pct_yr=0.3, sigma=0.03)
    values[36 + 16] *= 5
    _, result = assess({"Q1": article("Q1", values)})
    assert "spike_driven" not in codes(result)


def test_spike_driven_false_alarm_rate_on_clean_series():
    hits = sum(
        "spike_driven" in codes(assess({"Q1": article("Q1", series(5000, seed, sigma=0.15, phi=0.5))})[1])
        for seed in range(200)
    )
    assert hits / 200 <= 0.10  # prototype: 6%


def test_one_of_ten_articles_growing_is_concentrated_and_unstable():
    articles = basket(9, level=1000, sigma=0.03)
    articles["Q10"] = article("Q10", series(1000, 99, sigma=0.03, trend_pct_yr=100))
    cell, result = assess(articles)
    assert C.point_category(cell.growth_pct_yr) == "up"
    assert {"concentrated", "unstable_basket"} <= codes(result)
    assert next(r for r in result.reasons if r.code == "concentrated").detail["qid"] == "Q10"
    assert next(r for r in result.reasons if r.code == "unstable_basket").detail["qids"] == ["Q10"]
    assert result.confidence == "low"


def test_dominant_seed_with_the_same_category_is_not_concentrated():
    articles = basket(4, level=1000, sigma=0.03)
    articles["Q5"] = article("Q5", series(1000, 99, sigma=0.03, trend_pct_yr=100))
    _, as_seed = assess(articles, seeds=["Q5"])
    _, not_seed = assess(articles, seeds=["Q1"])
    assert "concentrated" not in codes(as_seed)
    assert "concentrated" in codes(not_seed)


def test_small_noisy_trend_is_not_unstable():
    # +1 %/yr with noise: the category is flat with and without every article
    for seed in range(10):
        _, result = assess(basket(10, seed0=20 * seed, trend_pct_yr=1, sigma=0.1))
        assert "unstable_basket" not in codes(result)


def test_seven_small_noisy_and_three_large_growing_is_not_concentrated():
    articles = basket(7, level=100, sigma=0.3)
    for i in (8, 9, 10):
        articles[f"Q{i}"] = article(f"Q{i}", series(5000, i, sigma=0.03, trend_pct_yr=20))
    _, result = assess(articles)
    assert "concentrated" not in codes(result)


def test_unstable_basket_ignores_p_value_shifts():
    # testing §1: removing Q2 moves p from 0.035 to 0.06, the category stays "up" -> not a reason
    data, _, cell = cell_of(basket(5, seed0=540, trend_pct_yr=10, sigma=0.1))
    assert cell.trend.p < 0.05 and C.point_category(cell.growth_pct_yr) == "up"
    rest = [q for q in cell.qids if q != "Q2"]
    share = metrics.share_ppm(metrics.aggregate(data.articles, rest), data.denominator)
    without_q2 = metrics.estimate_or_none(share, data.first_month, data.window_start)
    assert without_q2.p >= 0.05 and C.point_category(without_q2.growth_pct_yr) == "up"
    assert "Q2" not in C.leave_one_out(data, cell.qids, "up")


def test_single_article_skips_basket_checks():
    values = series(1000, 5, sigma=0.03, trend_pct_yr=100)
    _, result = assess({"Q1": article("Q1", values)})
    assert "single_article" in codes(result)
    assert not codes(result) & {"unstable_basket", "concentrated", "thin_basket"}


def test_thin_basket():
    _, result = assess(basket(3, trend_pct_yr=20, sigma=0.05))
    assert "thin_basket" in codes(result)
    assert result.confidence == "high"  # one medium reason


def test_short_window():
    _, result = assess(basket(4, n_window=18, trend_pct_yr=30, sigma=0.03))
    assert "short_window" in codes(result)
    assert result.confidence == "medium"


def test_short_history_caps_confidence_at_low():
    _, result = assess(basket(4, n_history=12, trend_pct_yr=30, sigma=0.03), window_start=12)
    assert "short_history" in codes(result)
    assert result.confidence == "low"


def test_autocorr_capped_is_a_high_reason():
    hits = 0
    for seed in range(20):
        _, result = assess({"Q1": article("Q1", series(5000, seed, sigma=0.15, phi=0.95))})
        if "autocorr_capped" in codes(result):
            hits += 1
            assert next(r for r in result.reasons if r.code == "autocorr_capped").severity == "high"
            assert result.confidence != "high"
    assert hits >= 10


def test_low_volume_names_the_primary_basket():
    _, result = assess(basket(4, level=200, sigma=0.03))
    r = next(r for r in result.reasons if r.code == "low_volume")
    assert r.severity == "high" and r.detail == {"basket": "common", "monthly_views": pytest.approx(800, rel=0.1), "zero_share": 0.0}
    assert result.confidence == "medium" or result.direction == "unclear"


def test_blocking_low_volume_makes_direction_unclear():
    cell, result = assess(basket(2, level=30, sigma=0.03, trend_pct_yr=30, history_trend_pct_yr=0))
    assert cell.primary_monthly_views < 100
    assert C.direction_of(cell.trend)[0] == "growing"  # overridden by the blocking reason
    assert (result.direction, result.confidence) == ("unclear", "low")


def test_too_few_positive_months_is_blocking_low_volume():
    values = [500.0] * 36 + [0.0] * 22 + [500.0, 500.0]
    data = lang_data({"Q1": article("Q1", values)})
    baskets = metrics.Baskets(["Q1"], ["Q1"], [], [])
    cell = metrics.compute_cell(data, baskets)
    result = C.assess(data, baskets, cell, [])
    assert (result.direction, result.confidence, result.category) == ("unclear", "low", None)
    assert {"low_volume"} <= codes(result)


def test_no_common_basket():
    pl = lang_data({"Q1": article("Q1", series(3000, 1, sigma=0.05))}, lang="pl")
    sk = lang_data({"Q2": article("Q2", series(3000, 2, sigma=0.05))}, lang="sk")
    baskets = metrics.build_baskets(["Q1", "Q2"], [], {"pl": pl, "sk": sk})["pl"]
    cell = metrics.compute_cell(pl, baskets)
    result = C.assess(pl, baskets, cell, [])
    assert cell.basket == "local"
    assert "no_common_basket" in codes(result)


def test_clean_baskets_rarely_get_a_high_reason():
    # calibration R4 budget: <= 20% of clean series with any high-severity reason
    flagged = 0
    for seed in range(60):
        _, result = assess(basket(6, seed0=10 * seed, trend_pct_yr=(seed % 5 - 2) * 10, sigma=0.15, phi=0.3))
        flagged += any(r.severity == "high" for r in result.reasons)
    assert flagged / 60 <= 0.20


def test_concentrated_seed_uses_full_local_seed_basket_when_common_is_narrower():
    pl = lang_data({
        'Q1': article('Q1', series(1000, 1, trend_pct_yr=100, sigma=0)),
        'Q2': article('Q2', [1000] * 60),
        'Q3': article('Q3', [1000] * 60),
        'Q4': article('Q4', series(1_000_000, 4, trend_pct_yr=-60, sigma=0)),
    }, lang='pl')
    cs = lang_data({q: a for q, a in pl.articles.items() if q != 'Q4'}, lang='cs')
    baskets = metrics.build_baskets(['Q1', 'Q2', 'Q3', 'Q4'], ['Q1', 'Q4'], {'pl': pl, 'cs': cs})['pl']
    cell = metrics.compute_cell(pl, baskets)
    seed_share = metrics.share_ppm(metrics.aggregate(pl.articles, baskets.seed), pl.denominator)
    seed_trend = metrics.estimate_or_none(seed_share, pl.first_month, pl.window_start)
    assert baskets.common == ['Q1', 'Q2', 'Q3'] and baskets.seed == ['Q1', 'Q4']
    assert C.point_category(cell.growth_pct_yr) == 'up'
    assert C.point_category(seed_trend.growth_pct_yr) == 'down'
    assert cell.contributors[0].qid == 'Q1' and cell.contributors[0].share > 0.6
    result = C.assess(pl, baskets, cell, ['Q1', 'Q4'])
    assert 'concentrated' in codes(result)
