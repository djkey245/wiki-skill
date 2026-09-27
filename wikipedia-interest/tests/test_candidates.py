import pytest

from webfake import FakeWeb
from wikitrends import candidates, sitematrix
from wikitrends import thresholds as T
from wikitrends.cache import Cache


@pytest.fixture
def cache(tmp_path):
    return Cache(tmp_path / "cache.sqlite")


def english_exams():
    """Seed IELTS (Q1); candidates of different quality in uk, pl and en."""
    web = FakeWeb()
    web.entity("Q1", {"uk": "IELTS", "en": "IELTS"}, {"uk": "IELTS", "pl": "IELTS", "en": "IELTS"})
    web.entity("Q2", {"uk": "TOEFL", "en": "TOEFL"}, {"uk": "TOEFL", "pl": "TOEFL", "en": "TOEFL"})
    web.entity("Q3", {"uk": "англійська мова"}, {"uk": "Англійська мова", "pl": "Język angielski", "en": "English language"},
               claims={"P31": ["Q1288568"]})
    web.entity("Q4", {"en": "Cambridge"}, {"uk": "Кембридж", "pl": "Cambridge", "en": "Cambridge"}, claims={"P31": ["Q515"]})
    web.entity("Q5", {"en": "Cambridge English"}, {"uk": "Кембриджські іспити", "en": "Cambridge English"})
    web.entity("Q6", {"uk": "мовний тест"}, {"uk": "Мовний тест", "pl": "Test językowy", "en": "Language test"},
               claims={"P279": ["Q1"]})
    web.entity("Q7", {"uk": "Особа"}, {"uk": "Особа", "pl": "Osoba", "en": "Person"}, claims={"P31": ["Q5"]})
    web.morelike = {
        ("uk", "IELTS"): ["TOEFL", "Кембриджські іспити"],
        ("pl", "IELTS"): ["TOEFL"],
        ("en", "IELTS"): ["TOEFL", "Cambridge English", "Language test"],
    }
    web.links = {
        ("uk", "IELTS"): ["TOEFL", "Англійська мова", "Кембридж", "Особа", "Мовний тест"],
        ("pl", "IELTS"): ["Język angielski", "Cambridge", "Osoba"],
        ("en", "IELTS"): ["English language", "Cambridge", "Person"],
        ("uk", "TOEFL"): ["IELTS"],  # links back to the seed
    }
    return web


def run(web, cache, **kwargs):
    http, transport = web.client()
    matrix = sitematrix.SiteMatrix.load(http, cache)
    return candidates.suggest(http, cache, matrix, ["Q1"], ["uk", "pl"], "uk", **kwargs), transport


def test_ranking_filter_coverage_and_draft(cache):
    result, _ = run(english_exams(), cache)
    ranked = {c.qid: c for c in result.ranked}
    assert list(ranked) == ["Q2", "Q6", "Q5"]
    toefl = ranked["Q2"]
    assert toefl.morelike == {"uk", "pl", "en"} and toefl.links == {"uk"} and toefl.backlinks == {"uk"}
    assert toefl.score == pytest.approx(3 + 1 + 0.5)
    assert ranked["Q6"].wikidata and ranked["Q6"].score == pytest.approx(1 + 1 + 1)
    assert ranked["Q5"].coverage == {"uk": "Кембриджські іспити", "pl": None}
    assert result.excluded_types == {"modern language": 1, "city": 1, "human": 1}
    assert result.draft == ["Q1", "Q2", "Q6"]  # Q5 is missing in pl
    assert result.seeds[0].coverage == {"uk": "IELTS", "pl": "IELTS"} and result.seeds[0].label == "IELTS"


def test_only_top_candidates_get_metadata(cache, monkeypatch):
    monkeypatch.setattr(T, "CANDIDATES_FOR_METADATA", 2)
    result, transport = run(english_exams(), cache)
    asked = [r.params["ids"] for r in transport.requests if r.params.get("action") == "wbgetentities"]
    assert asked == ["Q1", "Q2|Q3"]  # seeds, then the two best by the cheap score
    assert [c.qid for c in result.ranked] == ["Q2"]


def test_seed_missing_in_a_language_is_a_gap(cache):
    web = english_exams()
    del web.entities["Q1"]["sitelinks"]["plwiki"]
    result, transport = run(web, cache)
    assert result.seeds[0].coverage["pl"] is None
    assert not any(r.host == "pl.wikipedia.org" and r.params.get("titles") == "IELTS" for r in transport.requests)
    assert result.ranked[0].qid == "Q2"


def test_limit_and_estimate(cache):
    result, transport = run(english_exams(), cache, limit=1)
    assert [c.qid for c in result.ranked] == ["Q2"] and result.draft == ["Q1", "Q2", "Q6"]
    assert transport.count() <= candidates.estimate_requests(1, 2)
    assert candidates.estimate_requests(5, 10) <= T.MAX_SUGGEST_REQUESTS
