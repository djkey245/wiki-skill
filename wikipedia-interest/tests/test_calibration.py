"""Calibration level 0a tools (calibration §2, §5): corpus slicing, the semi-synthetic null, R1, R12."""
import gzip
import json
import random
import statistics

from cells import article, lang_data, series
from synthetic import make_share_series
from wikitrends import trend
from wtcal import corpus, r1, r12, semisynth

FIRST = "2020-01"


def test_null_has_no_trend_but_keeps_level_and_seasonality():
    """A series growing 30 %/yr: the null removes the trend, keeps the level and the season."""
    values = make_share_series(n_history=36, n_window=24, trend_pct_yr=30, seasonal_amp=0.3, sigma=0.05, seed=1, level=50)
    rng = random.Random(0)
    slopes = []
    for _ in range(200):
        null = semisynth.null_series(values, FIRST, 36, rng)
        slopes.append(trend.estimate(null, FIRST, 36).growth_pct_yr)
    assert abs(statistics.median(slopes)) < 5  # the 30 %/yr trend is gone
    assert statistics.pstdev(slopes) > 1  # blocks from the history move the window: not zero by construction
    null = semisynth.null_series(values, FIRST, 36, random.Random(1))
    assert 0.5 < statistics.median(null[36:]) / statistics.median(values[36:]) < 2
    january = [null[t] for t in range(0, 60, 12)]
    july = [null[t] for t in range(6, 60, 12)]
    assert statistics.median(january) != statistics.median(july)  # the profile survives


def test_block_shuffle_keeps_values_and_blocks():
    values = list(range(40))
    out = semisynth.block_shuffle(values, random.Random(3))
    assert sorted(out) == values and out != values
    runs = sum(1 for a, b in zip(out, out[1:]) if b != a + 1)
    assert runs <= 40 // semisynth.BLOCK_MIN


def test_known_changes():
    base = [1.0] * 24
    assert semisynth.with_trend(base, 20.0)[12] == pytest_approx(1.2)
    assert semisynth.with_step(base, 12, 0.3)[11:13] == [1.0, pytest_approx(1.3498588)]


def pytest_approx(x):
    import pytest

    return pytest.approx(x, rel=1e-6)


def test_window_slices_the_full_series_like_analyze():
    full = lang_data({"Q1": article("Q1", [100.0 + t for t in range(96)])}, window_start=0)  # 2020-01 .. 2027-12
    cut = corpus.window(full, "2025-12")
    assert cut.first_month == "2021-01" and cut.window_start == 36 and len(cut.denominator) == 60
    assert cut.articles["Q1"].views[0] == 112 and cut.articles["Q1"].views[-1] == 171
    assert corpus.window(full, "2028-06") is None
    raw = json.loads(json.dumps(corpus.dump(full)))
    assert corpus.restore("pl", raw).articles["Q1"].views == full.articles["Q1"].views


def fake_corpus(tmp_path, n_topics=2):
    topics = []
    for i in range(n_topics):
        tid = f"t{i + 1:02d}"
        langs = {}
        for j, lang in enumerate(("pl", "cs")):
            arts = {q: article(q, series(3000, 10 * i + 3 * j + k, n_history=60, n_window=36, sigma=0.1, phi=0.3))
                    for k, q in enumerate(("Q1", "Q2"))}
            langs[lang] = corpus.dump(lang_data(arts, window_start=0, lang=lang))
        with gzip.open(tmp_path / f"{tid}.json.gz", "wt") as f:
            json.dump({"end": "2027-12", "langs": langs}, f)
        topics.append({"id": tid, "qid": "Q1", "label": tid, "field": "x", "random": True, "items": ["Q1", "Q2"]})
    return {"langs": ["pl", "cs"], "topics": topics}


def test_r1_on_a_small_corpus(tmp_path):
    data = fake_corpus(tmp_path)
    got = list(r1.corpus_series(data, lambda t: corpus.read(t, tmp_path), corpus.window, "2027-12"))
    assert [k for k, *_ in got] == ["t01:pl", "t01:cs", "t02:pl", "t02:cs"]
    report = r1.run(got, shuffles=5)
    large = report["sizes"]["large"]
    assert large["series"] == 20 and 0 <= large["false_trend"] <= 0.5 and large["power_20"] > large["false_trend"]
    assert report["test_ci_agreement"] >= 0.9
    verdicts = r1.verdicts(report)
    assert verdicts["small"]["false_trend"] is None and isinstance(verdicts["large"]["power_20"], bool)


def test_assess_series_runs_the_full_pipeline():
    values = make_share_series(n_history=36, n_window=24, trend_pct_yr=40, sigma=0.03, seed=2, level=50)
    a = r1.assess_series(values, FIRST, 36)
    assert a.direction == "growing" and "single_article" in {r.code for r in a.reasons}
    assert r1.assess_series([None] * 60, FIRST, 36) is None


def test_r12_follows_next_steps_until_done():
    outputs = iter([
        {"ok": True, "partial": True, "next_steps": ["python3 /x/wikitrends.py analyze /w/spec.json"],
         "requests": {"network": 280, "throttled": 1}, "_seconds": 91.0},
        {"ok": True, "requests": {"network": 40, "throttled": 0}, "_seconds": 20.5},
    ])
    seen = []

    def run(argv, env):
        seen.append(list(argv))
        assert "WIKITRENDS_HOME" in env and "WIKITRENDS_CACHE_DIR" in env
        return next(outputs)

    got = r12.measure(["quick", "астрономія"], run)
    assert seen[1] == ["analyze", "/w/spec.json"]
    assert got["n_calls"] == 2 and got["network"] == 320 and got["throttled"] == 1 and got["finished"]
    verdict = r12.verdicts({"example_1": got})["example_1"]
    assert verdict == {"calls": True, "seconds": True, "no_429": False, "one_call": False}
