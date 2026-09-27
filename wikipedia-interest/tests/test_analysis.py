import json

from cells import article, lang_data, series
from wikitrends import analysis, templates
from wikitrends import thresholds as T

WINDOW = ("2023-01", "2024-12")  # cells.FIRST = 2020-01 is its history start, window_start = 36
LABELS = {"Q1": "Астрономія", "Q2": "Зоря", "Q3": "Галактика", "Q4": "Планета"}


def make_spec(langs, topics=None, criteria=None):
    topics = topics or [{"id": "astronomy", "label": "астрономія", "seeds": ["Q1"], "items": ["Q1", "Q2", "Q3", "Q4"]}]
    options = {"include_redirects": True, "max_redirects": 10, "metric": "share"}
    if criteria:
        options["criteria"] = criteria
    return {"version": 1, "display_lang": "uk", "langs": langs, "options": options, "topics": topics,
            "period": {"relative": "24m", "start": WINDOW[0], "end": WINDOW[1]}}


def lang(code, qids, trend=0.0, level=5000.0, sigma=0.05, seed=0):
    articles = {q: article(q, series(level, seed + i, trend_pct_yr=trend, sigma=sigma)) for i, q in enumerate(qids)}
    return lang_data(articles, lang=code)


def three_langs():
    return {
        "pl": lang("pl", ["Q1", "Q2", "Q3", "Q4"], trend=30.0, seed=10),
        "cs": lang("cs", ["Q1", "Q2", "Q3", "Q4"], sigma=0.4, seed=20),
        "uk": lang("uk", ["Q1", "Q2", "Q3"], level=100.0, seed=30),  # no Q4 in uk
    }


def cells_by_lang(result, topic=0):
    return {c["lang"]: c for c in result["topics"][topic]["cells"]}


def test_evaluate_three_languages():
    result = analysis.evaluate(make_spec(["uk", "pl", "cs"]), WINDOW, three_langs(), {}, LABELS)
    topic = result["topics"][0]
    cells = cells_by_lang(result)
    assert cells["pl"]["group"] == "promising" and cells["pl"]["fmt"]["growth"].endswith("%/рік")
    assert cells["cs"]["group"] == "no_clear_trend"
    assert topic["common"] == ["Q1", "Q2", "Q3"]
    assert {"qid": "Q4", "label": "Планета", "lang": "uk", "code": "missing_in_lang"} in topic["excluded"]
    assert all(c["basket"] == "common" and c["n_articles"] == 3 for c in cells.values())
    # size uses local, so uk (3 small articles) is small and pl/cs are big
    assert cells["pl"]["monthly_views"] > cells["uk"]["monthly_views"]
    assert topic["matrix"] and cells["pl"]["quadrant"] == "big_growing" and cells["uk"]["size_big"] is False
    assert [(p["a"], p["b"]) for p in topic["pairs"]] and all("text" in p for p in topic["pairs"])
    assert [x["key"] for x in topic["recommendations"]["explore"]] == ["pl"]
    assert result["period"]["months"][0] == "2023-01" and len(cells["pl"]["series"]["index"]) == 24
    assert all(r["text"] for c in cells.values() for r in c["reasons"])
    assert topic["headline"].startswith("астрономія: ріст підтверджено — pl (")
    json.dumps(result)  # plain JSON


def test_two_languages_have_no_matrix():
    data = three_langs()
    del data["uk"]
    result = analysis.evaluate(make_spec(["pl", "cs"]), WINDOW, data, {}, LABELS)
    topic = result["topics"][0]
    assert not topic["matrix"]
    assert all(c["quadrant"] is None and c["size_big"] is None for c in topic["cells"])
    assert topic["ranking"] == ["pl", "cs"]


def test_empty_common_basket_skips_language_pairs():
    data = {"pl": lang("pl", ["Q1", "Q2"], trend=30.0, seed=1), "cs": lang("cs", ["Q3", "Q4"], seed=2)}
    result = analysis.evaluate(make_spec(["pl", "cs"]), WINDOW, data, {}, LABELS)
    topic = result["topics"][0]
    assert topic["common"] == [] and topic["pairs"] == [] and topic["pairs_skipped"] == "no_common_basket"
    assert all(c["basket"] == "local" for c in topic["cells"])
    assert all("no_common_basket" in [r["code"] for r in c["reasons"]] for c in topic["cells"])
    md = templates.answer_md({**result}, full=True)
    assert templates.SENTENCES["uk"]["no_pairs"] in md


def test_failed_language_and_no_articles():
    data = three_langs()
    del data["cs"]
    data["uk"] = lang("uk", ["Q9"], seed=3)  # none of the topic's items
    result = analysis.evaluate(make_spec(["uk", "pl", "cs"]), WINDOW, data, {"cs": "HTTP 500"}, LABELS)
    cells = cells_by_lang(result)
    assert cells["cs"]["status"] == "failed" and cells["cs"]["group"] == "insufficient_data"
    assert cells["uk"]["status"] == "no_articles"
    assert result["langs"][2] == {"lang": "cs", "status": "failed", "error": "HTTP 500"}
    assert analysis.analysed_cells(result) == 1
    templates.answer_md(result, full=False)  # renders failed rows


def test_criteria_do_not_change_groups():
    plain = analysis.evaluate(make_spec(["uk", "pl", "cs"]), WINDOW, three_langs(), {}, LABELS)
    criteria = {"min_monthly_views": 500000, "min_growth_pct_yr": 15}
    judged = analysis.evaluate(make_spec(["uk", "pl", "cs"], criteria=criteria), WINDOW, three_langs(), {}, LABELS)
    for before, after in zip(plain["topics"][0]["cells"], judged["topics"][0]["cells"]):
        assert (before["group"], before["confidence"]) == (after["group"], after["confidence"])
    pl = cells_by_lang(judged)["pl"]
    assert pl["criteria"] == {"min_monthly_views": "not_met", "min_growth_pct_yr": "met"} and pl["criteria_met"] is False
    md = templates.answer_md(judged, full=True)
    assert "ріст частки ≥ 15 %/рік" in md and "не виконано" in md


def test_answer_md_numbers_match_the_result():
    result = analysis.evaluate(make_spec(["uk", "pl", "cs"]), WINDOW, three_langs(), {}, LABELS)
    md = templates.answer_md(result, full=True)
    for c in result["topics"][0]["cells"]:
        assert f"| {c['lang']} | " in md
        for key in ("growth", "ci", "level_ppm", "monthly_views"):
            assert c["fmt"][key] in md
        for r in c["reasons"]:
            assert r["text"] in md
    for p in result["topics"][0]["pairs"]:
        assert p["text"] in md
    assert templates.SENTENCES["uk"]["market"] in md and templates.SENTENCES["uk"]["no_forecast"] in md


def many_cells():
    langs = [f"l{i}" for i in range(10)]
    qids = ["Q1", "Q2", "Q3", "Q4"]
    topics = [{"id": f"t{k}", "label": f"тема {k}", "seeds": ["Q1"], "items": qids} for k in range(5)]
    data = {l: lang(l, qids, trend=30.0 if i == 0 else 0.0, sigma=0.4 if i else 0.05, seed=i * 7)
            for i, l in enumerate(langs)}
    return make_spec(langs, topics), data


def test_answer_md_truncation_for_many_cells():
    spec_data, data = many_cells()
    result = analysis.evaluate(spec_data, WINDOW, data, {}, LABELS)
    full = templates.answer_md(result, full=True)
    short = templates.answer_md(result, full=False)
    assert len(short) < len(full)
    assert "Зведення за групами" in short and "див. result.json" in short
    for t in result["topics"]:  # key cells stay
        for c in t["cells"]:
            if c["group"] in ("promising", "emerging"):
                assert f"| {c['lang']} | {t['label']} |" in short
    out = analysis.compact(result)
    assert all(len(c["reasons"]) <= T.MAX_REASONS_SHOWN or c["group"] == "emerging"
               for t in out["topics"] for c in t["cells"])
    assert "groups" in out["topics"][0]
    # R13: stdout with answer_md stays within ~2k tokens after truncation (measured in characters here)
    assert len(json.dumps({**out, "answer_md": short}, ensure_ascii=False)) < 7000
    assert len(result["by_lang"]) == 10 and result["by_lang"][0]["recommendations"] is not None


def late(values, months_missing):
    """The same series with no views in the first months of history (short_history)."""
    return [0.0] * months_missing + list(values[months_missing:])


def test_recommendation_block_texts():
    qids = ["Q1", "Q2", "Q3", "Q4"]
    data = {
        "pl": lang("pl", qids, trend=30.0, seed=40),
        "de": lang("de", qids, trend=-30.0, seed=50),
        "cs": lang_data({q: article(q, late(series(5000.0, 60 + i, trend_pct_yr=30.0, sigma=0.05), 20))
                         for i, q in enumerate(qids)}, lang="cs"),
    }
    result = analysis.evaluate(make_spec(["pl", "cs", "de"]), WINDOW, data, {}, LABELS)
    cells = cells_by_lang(result)
    assert (cells["pl"]["group"], cells["cs"]["group"], cells["de"]["group"]) == ("promising", "emerging", "weakening")
    rec = result["topics"][0]["recommendations"]
    assert [x["key"] for x in rec["explore"]] == ["pl", "cs"] and [x["key"] for x in rec["postpone"]] == ["de"]
    cs_text = rec["explore"][1]["text"]
    assert "ріст не підтверджено" in cs_text and "історії до початку вікна" in cs_text.lower()
    md = templates.answer_md(result, full=False)
    assert templates.SENTENCES["uk"]["weakening_caveat"] in md


def test_no_growth_says_so():
    data = {l: lang(l, ["Q1", "Q2", "Q3", "Q4"], sigma=0.4, seed=s) for l, s in (("pl", 70), ("cs", 80))}
    result = analysis.evaluate(make_spec(["pl", "cs"]), WINDOW, data, {}, LABELS)
    rec = result["topics"][0]["recommendations"]
    assert rec["explore"] == [] and "немає мови з виявленим ростом" in rec["none"]
    assert rec["none"] in templates.answer_md(result, full=False)


def test_raw_growth_below_the_criterion_is_mentioned():
    n = 60
    falling = [int(100_000_000 * 0.8 ** (t / 12)) for t in range(n)]  # the edition's traffic falls 20 %/yr
    articles = {q: article(q, series(5000.0, 90 + i, sigma=0.02)) for i, q in enumerate(["Q1", "Q2", "Q3", "Q4"])}
    data = {"pl": lang_data(articles, lang="pl", denominator=falling)}
    result = analysis.evaluate(make_spec(["pl"], criteria={"min_growth_pct_yr": 15}), WINDOW, data, {}, LABELS)
    cell = cells_by_lang(result)["pl"]
    assert cell["criteria"] == {"min_growth_pct_yr": "met"} and cell["raw_growth_pct_yr"] < 15
    assert "ріст сирих переглядів" in templates.answer_md(result, full=True)
    assert result["topics"][0]["recommendations"] is None  # a single item: no block


def test_changes_and_versioned_results(tmp_path):
    spec_path = tmp_path / "astronomy" / "spec.json"
    spec_path.parent.mkdir()
    two = {k: v for k, v in three_langs().items() if k != "uk"}
    two["pl"] = lang("pl", ["Q1", "Q2", "Q3", "Q4"], trend=30.0, seed=10)
    first = analysis.evaluate(make_spec(["pl", "cs"]), WINDOW, two, {}, LABELS)
    assert analysis.changes(None, first) == []
    path1 = analysis.save_result(spec_path, first)
    second = analysis.evaluate(make_spec(["uk", "pl", "cs"]), WINDOW, three_langs(), {}, LABELS)
    previous = analysis.load_previous(spec_path)
    found = analysis.changes(previous, second)
    kinds = [c["kind"] for c in found]
    assert {"kind": "langs", "added": ["uk"], "removed": []} in found
    narrowed = next(c for c in found if c["kind"] == "common_narrowed")
    assert narrowed["removed"] == [{"qid": "Q4", "label": "Планета"}] and narrowed["because_langs"] == ["uk"]
    assert "period" not in kinds
    path2 = analysis.save_result(spec_path, second)
    assert path1 != path2 and path1.exists() and path2.exists()
    assert json.loads((spec_path.parent / "result.json").read_text())["result"] == str(path2)


def test_answer_sections_make_the_answer():
    result = analysis.evaluate(make_spec(["uk", "pl", "cs"], criteria={"min_growth_pct_yr": 15}), WINDOW, three_langs(), {}, LABELS)
    for full in (True, False):
        sections = templates.answer_sections(result, full=full)
        assert [name for name, _ in sections] == ["headlines", "table", "pairs", "reasons", "recommendations",
                                                  "assumptions", "next"]
        assert templates.answer_md(result, full=full) == "\n\n".join("\n".join(lines) for _, lines in sections)
    topic, cell = result["topics"][0], result["topics"][0]["cells"][1]
    row = templates.table_row(topic, cell, "uk", False, True)
    assert len(row) == len(templates.table_header("uk", False, True)) and row[0] == "pl"
