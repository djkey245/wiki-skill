from wikitrends import confidence, templates


def test_numbers():
    assert templates.fmt_growth(12.4, "uk") == "+12 %/рік"
    assert templates.fmt_growth(-3.44, "en") == "-3.4 %/yr"
    assert templates.fmt_growth(3.44, "uk") == "+3,4 %/рік"
    assert templates.fmt_growth(0.02, "uk") == "0 %/рік"
    assert templates.fmt_ci([4.2, 21.0], "uk") == "[+4,2; +21]"
    assert templates.fmt_views(52300.4, "uk") == "52 300"
    assert templates.fmt_views(52300.4, "en") == "52,300"
    assert templates.fmt_ppm(12.34, "uk") == "12,3" and templates.fmt_ppm(1234.0, "en") == "1,234"
    assert templates.fmt_growth(None, "uk") is None


def test_text_lang_falls_back_to_english():
    assert templates.text_lang("uk") == "uk"
    assert templates.text_lang("pl") == "en"


def test_every_reason_has_a_sentence_in_every_language():
    details = {
        "low_volume": {"basket": "common", "monthly_views": 800, "zero_share": 0.0},
        "spike_driven": {"months": ["2025-03"]}, "unstable_basket": {"qids": ["Q1"]},
        "concentrated": {"qid": "Q1", "share": 0.72}, "short_window": {"months": 18},
        "short_history": {"history_months": 10}, "thin_basket": {"n": 3},
        "step_like": {"month": "2025-04"}, "seed_divergence": {"growth_pct_yr": -12.0},
        "data_break": {"months": ["2025-03"]}, "data_revised": {"months": ["2025-03", "2025-04"]},
        "trend_reversal": {"recent_yoy_pct": -14.2}, "recent_only": {"months": 3}, "redirect_heavy": {"share": 0.62},
        "metric_disagreement": {"other": "views", "raw_growth_pct_yr": -8.0}, "project_trend": {"project_trend_pct_yr": -9.5},
    }
    for lang in templates.TEXT_LANGS:
        for code in confidence.SEVERITY:
            text = templates.reason_text(code, details.get(code, {}), lang, {"Q1": "IELTS"})
            assert text.endswith(".") and "{" not in text
    text = templates.reason_text("low_volume", details["low_volume"], "uk", {})
    assert "спільному для всіх мов" in text and "800" in text  # names the basket and its own value
    assert "72 %" in templates.reason_text("concentrated", details["concentrated"], "en", {"Q1": "IELTS"})


def cell(lang, group, growth=None, ci=None):
    return {"lang": lang, "group": group,
            "fmt": {"growth": templates.fmt_growth(growth, "uk"), "ci": templates.fmt_ci(ci, "uk")}}


def test_headline_lists_groups_in_order():
    cells = [cell("uk", "no_clear_trend", 3.0), cell("pl", "promising", 12.0), cell("cs", "emerging", 30.0)]
    assert templates.headline("Астрономія", cells, "uk") == (
        "Астрономія: ріст підтверджено — pl (+12 %/рік); ріст не підтверджено — cs (+30 %/рік); тренд не виявлено — uk.")


def test_pair_sentences_for_non_significant_differences():
    pair = {"significant": False, "diff_pct_yr": 20.0, "fmt": {}}
    differ = templates.pair_text(pair, cell("pl", "promising", 30.0), cell("cs", "no_clear_trend", 8.0), "pl", "cs", "uk")
    assert differ == "Для pl — ріст підтверджено, для cs — тренд не виявлено, але різниця темпів між ними статистично не підтверджена."
    same = templates.pair_text(pair, cell("pl", "promising", 30.0), cell("cs", "promising", 8.0), "pl", "cs", "uk")
    assert "+30 %/рік" in same and "+8 %/рік" in same and "не підтверджена" in same
    significant = {"significant": True, "diff_pct_yr": -15.0, "ci": [-25.0, -5.0], "fmt": {}}
    text = templates.pair_text(significant, cell("pl", "steady"), cell("cs", "promising"), "pl", "cs", "en")
    assert text == "The share grows faster for cs than for pl: the difference is +15 %/yr [+5; +25]."


def test_quadrant_names_follow_the_axis():
    assert templates.quadrant_name("big_growing", "uk") == "великий обсяг, росте"
    assert templates.quadrant_name("big_growing", "uk", "level_ppm") == "висока частка, росте"
    assert templates.quadrant_name("growing_niche", "en") == "growing niche"
