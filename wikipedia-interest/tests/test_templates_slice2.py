"""Slice-2 texts: new reasons and the main metric in answer_md (agent contract §3, recommendations §4)."""
from wikitrends import templates


def test_new_reason_sentences():
    uk = templates.reason_text("metric_disagreement", {"other": "views", "raw_growth_pct_yr": -8.0}, "uk", {})
    assert uk.startswith("Сирі перегляди: зміна в протилежний бік (-8 %/рік)")
    en = templates.reason_text("metric_disagreement", {"other": "share", "raw_growth_pct_yr": 12.0}, "en", {})
    assert en.startswith("The share: the change goes the other way (+12 %/yr)")
    assert "2025-04" in templates.reason_text("step_like", {"month": "2025-04"}, "uk", {})
    assert "-14 %" in templates.reason_text("trend_reversal", {"recent_yoy_pct": -14.2}, "en", {})
    assert "62 %" in templates.reason_text("redirect_heavy", {"share": 0.62}, "uk", {})


def test_growth_criterion_names_the_main_metric():
    assert templates.criterion_name("min_growth_pct_yr", 15, "uk") == "ріст частки ≥ 15 %/рік"
    assert templates.criterion_name("min_growth_pct_yr", 15, "uk", "views") == "ріст переглядів ≥ 15 %/рік"
    assert templates.criterion_name("min_growth_pct_yr", 15, "en", "views") == "views growth ≥ 15 %/yr"


def test_significant_pair_names_the_metric():
    pair = {"significant": True, "diff_pct_yr": 12.0, "ci": [4.0, 20.0], "fmt": {"diff": "+12 %/рік", "ci": "[+4; +20]"}}
    cell = {"group": "promising", "fmt": {}}
    assert templates.pair_text(pair, cell, cell, "pl", "cs", "uk").startswith("Частка росте швидше для pl")
    assert templates.pair_text(pair, cell, cell, "pl", "cs", "uk", "views").startswith("Перегляди ростуть швидше для pl")
