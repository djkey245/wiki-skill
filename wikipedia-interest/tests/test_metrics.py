import math

import pytest

from cells import FIRST, article, cell_of, lang_data, series
from wikitrends import metrics


def flat(value, n=60):
    return [value] * n


# --- baskets --------------------------------------------------------------------------


def test_baskets_common_local_seed_and_exclusions():
    pl = lang_data(
        {
            "Q1": article("Q1", flat(500)),
            "Q2": article("Q2", flat(500)),
            "Q3": article("Q3", [0] * 40 + [500] * 20),  # new in pl
            "Q4": article("Q4", flat(500)),
        },
        lang="pl",
        exclusions={"Q4": metrics.Exclusion("Q4", "duplicate_of", "pl", "Q1")},
    )
    cs = lang_data({"Q1": article("Q1", flat(500)), "Q3": article("Q3", flat(500)), "Q4": article("Q4", flat(500))}, lang="cs")
    out = metrics.build_baskets(["Q1", "Q2", "Q3", "Q4"], ["Q1", "Q2"], {"pl": pl, "cs": cs})

    assert out["pl"].common == out["cs"].common == ["Q1"]
    assert out["pl"].local == ["Q1", "Q2"]
    assert out["cs"].local == ["Q1", "Q3", "Q4"]
    assert out["pl"].seed == ["Q1", "Q2"] and out["cs"].seed == ["Q1"]
    assert out["pl"].primary_name == "common"
    assert [(e.qid, e.code, e.lang, e.detail) for e in out["pl"].excluded] == [
        ("Q2", "missing_in_lang", "cs", None),
        ("Q3", "late_start", "pl", "2023-05"),
        ("Q4", "duplicate_of", "pl", "Q1"),
    ]
    assert [(e.qid, e.code, e.lang) for e in out["cs"].excluded] == [
        ("Q2", "missing_in_lang", "cs"),
        ("Q3", "late_start", "pl"),
        ("Q4", "duplicate_of", "pl"),
    ]


def test_empty_common_falls_back_to_local():
    pl = lang_data({"Q1": article("Q1", flat(500))}, lang="pl")
    sk = lang_data({"Q2": article("Q2", flat(500))}, lang="sk")
    out = metrics.build_baskets(["Q1", "Q2"], [], {"pl": pl, "sk": sk})
    assert out["pl"].common == []
    assert out["pl"].primary_name == "local"
    assert out["pl"].primary == ["Q1"]


def test_single_language_common_equals_local():
    data = lang_data({"Q1": article("Q1", flat(500)), "Q2": article("Q2", flat(10))})
    b = metrics.build_baskets(["Q1", "Q2"], [], {"pl": data})["pl"]
    assert b.common == b.local == ["Q1"]
    assert [(e.qid, e.code) for e in b.excluded] == [("Q2", "below_alive_threshold")]


# --- series metrics -------------------------------------------------------------------


def test_aggregate_and_share_ppm():
    arts = {"Q1": article("Q1", [100, 200]), "Q2": article("Q2", [300, 0])}
    views = metrics.aggregate(arts, ["Q1", "Q2"])
    assert views == [400, 200]
    assert metrics.share_ppm(views, [1_000_000, None]) == [400.0, None]


def test_level_and_monthly_views_use_the_last_12_months():
    share = [1.0] * 48 + [2.0] * 12
    assert metrics.level_ppm(share) == 2.0
    views = [100] * 48 + [300] * 12
    assert metrics.monthly_views(views, 36) == 300.0


def test_index_base_is_the_first_12_months_of_the_window():
    share = [9.0] * 36 + [2.0] * 12 + [3.0] * 12
    index = metrics.index_series(share, 36)
    assert len(index) == 24
    assert index[0] == 100.0 and index[-1] == 150.0


def test_index_of_18_month_seasonal_window_has_no_seasonal_base_shift():
    # testing §1: strong seasonality, no trend, 18-month window -> the base covers a full year
    values = [100 * math.exp(0.5 * math.sin(2 * math.pi * t / 12)) for t in range(36 + 18)]
    index = metrics.index_series(values, 36)
    same_month_a_year_later = [index[t + 12] / index[t] for t in range(6)]
    assert all(r == pytest.approx(1.0) for r in same_month_a_year_later)
    assert sum(index[:12]) / 12 == pytest.approx(100.0)


def test_recent_yoy_needs_18_months():
    share = [1.0] * 36 + [1.0] * 12 + [1.5] * 6
    assert metrics.recent_yoy_pct(share, 36) == pytest.approx(50.0)
    assert metrics.recent_yoy_pct([1.0] * 53, 36) is None


def test_contributors_shares_of_absolute_contributions():
    arts = {
        "Q1": article("Q1", [100] * 36 + [100] * 12 + [400] * 12),  # +300
        "Q2": article("Q2", [100] * 36 + [200] * 12 + [100] * 12),  # -100
        "Q3": article("Q3", [100] * 60),  # 0
    }
    out = metrics.contributors(arts, ["Q1", "Q2", "Q3"], 36)
    assert [(c.qid, c.delta, c.share) for c in out] == [("Q1", 300.0, 0.75), ("Q2", -100.0, 0.25), ("Q3", 0.0, 0.0)]


def test_contributors_use_halves_of_a_short_window():
    arts = {"Q1": article("Q1", [100] * 36 + [100] * 9 + [200] * 9)}
    assert metrics.contributors(arts, ["Q1"], 36)[0].delta == 100.0


def test_redirect_share_and_zero_share():
    arts = {"Q1": article("Q1", flat(100), redirect_fraction=0.6)}
    assert metrics.redirect_share(arts, ["Q1"], 36) == pytest.approx(0.6)
    assert metrics.zero_share([1] * 36 + [0] * 6 + [5] * 18, 36) == 0.25


# --- the cell -------------------------------------------------------------------------


def test_compute_cell_trend_on_share_raw_on_views_and_project_trend():
    n = 60
    # project traffic falls 20 %/yr, basket views are flat -> share grows ~25 %/yr
    denominator = [int(1e8 * 0.8 ** (t / 12)) for t in range(n)]
    arts = {"Q1": article("Q1", series(5000, 1, sigma=0.02)), "Q2": article("Q2", series(3000, 2, sigma=0.02))}
    data, baskets, cell = cell_of(arts, denominator=denominator)
    assert cell.basket == "common" and cell.qids == ["Q1", "Q2"]
    assert cell.growth_pct_yr == pytest.approx(25, abs=3)
    assert cell.raw_growth_pct_yr == pytest.approx(0, abs=3)
    assert cell.project_trend_pct_yr == pytest.approx(-20, abs=0.5)
    assert cell.monthly_views == cell.primary_monthly_views
    assert len(cell.index) == 24


def test_compute_cell_size_on_local_trend_on_common():
    pl = lang_data({"Q1": article("Q1", flat(1000)), "Q2": article("Q2", flat(4000))}, lang="pl")
    cs = lang_data({"Q1": article("Q1", flat(1000))}, lang="cs")
    b = metrics.build_baskets(["Q1", "Q2"], [], {"pl": pl, "cs": cs})
    cell = metrics.compute_cell(pl, b["pl"])
    assert cell.qids == ["Q1"]
    assert cell.primary_monthly_views == 1000.0
    assert cell.monthly_views == 5000.0


def test_compute_cell_without_enough_positive_months_has_no_trend():
    arts = {"Q1": article("Q1", [500] * 36 + [0] * 22 + [500, 500])}
    data = lang_data(arts)
    b = metrics.Baskets(["Q1"], ["Q1"], [], [])
    cell = metrics.compute_cell(data, b)
    assert cell.trend is None and cell.growth_pct_yr is None
    assert cell.zero_share > 0.2
