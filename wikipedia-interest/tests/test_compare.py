import itertools

import pytest

from synthetic import make_share_series
from wikitrends import compare
from wikitrends import thresholds as T
from wikitrends.confidence import Reason

NS = Reason("not_significant", "blocking")
LV = Reason("low_volume", "blocking")
DIRECTIONS = ("growing", "stable", "small_effect", "declining", "unclear")
BLOCKING = {"none": [], "ns": [NS], "lv": [LV], "both": [NS, LV]}

EXPECTED = {  # recommendations §2; missing combinations are impossible
    ("growing", "high", "none"): "promising", ("growing", "medium", "none"): "promising",
    ("growing", "low", "none"): "emerging",
    ("stable", "high", "none"): "steady", ("stable", "medium", "none"): "steady",
    ("stable", "low", "none"): "no_clear_trend",
    ("small_effect", "high", "none"): "steady", ("small_effect", "medium", "none"): "steady",
    ("small_effect", "low", "none"): "no_clear_trend",
    ("declining", "high", "none"): "weakening", ("declining", "medium", "none"): "weakening",
    ("declining", "low", "none"): "no_clear_trend",
    ("unclear", "low", "ns"): "no_clear_trend",
    ("unclear", "low", "lv"): "insufficient_data", ("unclear", "low", "both"): "insufficient_data",
}


def test_group_table_every_combination():
    for direction, confidence, blocking in itertools.product(DIRECTIONS, ("high", "medium", "low"), BLOCKING):
        key = (direction, confidence, blocking)
        if blocking == "lv" or blocking == "both":  # low_volume decides whatever the rest says
            assert compare.group_of(direction, confidence, BLOCKING[blocking]) == "insufficient_data"
            continue
        if key in EXPECTED:
            assert compare.group_of(direction, confidence, BLOCKING[blocking]) == EXPECTED[key], key
        elif direction == "unclear" and blocking == "ns":
            assert compare.group_of(direction, confidence, BLOCKING[blocking]) == "no_clear_trend"
        else:
            with pytest.raises(ValueError):
                compare.group_of(direction, confidence, BLOCKING[blocking])


def test_size_flags_median_and_degenerate():
    assert compare.size_flags({"uk": 10.0, "pl": 20.0, "cs": 30.0}) == {"uk": False, "pl": True, "cs": True}
    assert compare.size_flags({"uk": 10.0, "pl": 20.0}) == {"uk": None, "pl": None}
    flags = compare.size_flags({"a": 5.0, "b": 5.0, "c": 1.0, "d": None})
    assert flags == {"a": True, "b": True, "c": False, "d": None}


def test_quadrant_table():
    seen = set()
    for group in compare.GROUPS:
        for big in (True, False):
            q = compare.quadrant(group, big)
            assert q in compare.QUADRANTS
            seen.add(q)
        assert compare.quadrant(group, None) is None
    assert seen == set(compare.QUADRANTS)
    assert compare.quadrant("promising", False) == "growing_niche"
    assert compare.quadrant("emerging", True) == compare.quadrant("emerging", False) == "unconfirmed"


def test_criteria_statuses():
    growth = {"min_growth_pct_yr": 15}
    assert compare.criteria_status(growth, "promising", 20.0, 1e5) == ({"min_growth_pct_yr": "met"}, True)
    assert compare.criteria_status(growth, "emerging", 20.0, 1e5) == ({"min_growth_pct_yr": "unconfirmed"}, False)
    assert compare.criteria_status(growth, "no_clear_trend", 20.0, 1e5) == ({"min_growth_pct_yr": "unconfirmed"}, False)
    assert compare.criteria_status(growth, "promising", 10.0, 1e5)[0] == {"min_growth_pct_yr": "not_met"}
    zero = {"min_growth_pct_yr": 0}
    assert compare.criteria_status(zero, "steady", 1.0, 1e5)[1] is True
    assert compare.criteria_status(zero, "steady", -3.0, 1e5)[0] == {"min_growth_pct_yr": "not_met"}
    views = {"min_monthly_views": 50000, "min_growth_pct_yr": 15}
    assert compare.criteria_status(views, "promising", 20.0, 40000.0) == (
        {"min_monthly_views": "not_met", "min_growth_pct_yr": "met"}, False)
    assert compare.criteria_status({}, "promising", 20.0, 1.0) == ({}, None)


def item(key, group, big=True, growth=0.0, criteria=None):
    return compare.Item(key, group, compare.quadrant(group, big), criteria or {}, growth)


def test_recommendation_order_quadrant_criteria_growth():
    items = [
        item("cs", "promising", big=False, growth=40.0),
        item("pl", "promising", big=True, growth=12.0),
        item("de", "emerging", growth=50.0),
        item("uk", "promising", big=True, growth=30.0, criteria={"min_monthly_views": "not_met"}),
        item("sk", "promising", big=True, growth=8.0),
        item("fr", "weakening", growth=-20.0),
        item("it", "no_clear_trend", growth=3.0),
    ]
    ranked = [i.key for i in compare.order(items)]
    assert ranked == ["pl", "sk", "uk", "cs", "de", "it", "fr"]
    rec = compare.recommend(items)
    assert [i.key for i in rec.explore] == ["pl", "sk", "cs"]
    assert [i.key for i in rec.postpone] == ["uk", "fr"]
    assert [i.key for i in rec.rest] == ["de", "it"]


def test_without_matrix_order_follows_groups():
    items = [item("pl", "steady", big=None), item("cs", "emerging", big=None, growth=9.0)]
    assert [i.key for i in compare.order(items)] == ["cs", "pl"]


def test_pairwise_detects_a_relative_trend():
    n_history, n_window = 36, 24
    series = {
        "uk": make_share_series(n_history=n_history, n_window=n_window, trend_pct_yr=30, sigma=0.05, seed=1),
        "pl": make_share_series(n_history=n_history, n_window=n_window, sigma=0.05, seed=2),
        "cs": make_share_series(n_history=n_history, n_window=n_window, sigma=0.05, seed=3),
    }
    pairs = compare.pairwise(series, "2020-01", n_history)
    assert [(p.a, p.b) for p in pairs] == [("uk", "pl"), ("uk", "cs"), ("pl", "cs")]
    uk_pl = compare.find_pair(pairs, "pl", "uk")
    assert uk_pl.significant and 20 < uk_pl.diff_pct_yr < 40
    assert not compare.find_pair(pairs, "pl", "cs").significant
    assert all(p.p_adj >= p.p for p in pairs)
    assert compare.neighbours(["uk", "pl", "cs"]) == [("uk", "pl"), ("pl", "cs")]


def test_pairwise_skips_pairs_without_points():
    empty = [None] * 60
    series = {"uk": [1.0] * 60, "pl": empty}
    assert compare.pairwise(series, "2020-01", 36) == []
