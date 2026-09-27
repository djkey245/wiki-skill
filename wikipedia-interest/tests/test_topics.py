import pytest

from webfake import FakeWeb
from wikitrends import sitematrix, topics
from wikitrends.cache import Cache

DISAMBIGUATION = {"P31": ["Q4167410"]}


@pytest.fixture
def cache(tmp_path):
    return Cache(tmp_path / "cache.sqlite")


def run(web, cache, query, lang):
    http, transport = web.client()
    site = sitematrix.SiteMatrix.load(http, cache).resolve(lang)
    return topics.find(http, cache, site, query, lang), transport


def test_exact_title_without_rivals_is_a_leader(cache):
    web = FakeWeb()
    web.entity("Q333", {"uk": "астрономія"}, {"uk": "Астрономія"}, descriptions={"uk": "наука"})
    web.entity("Q37547", {"uk": "астрофізика"}, {"uk": "Астрофізика"})
    web.search[("uk", "астрономія")] = ["Астрономія", "Астрофізика"]
    result, _ = run(web, cache, "астрономія", "uk")
    assert result.leader == "Q333"
    assert result.candidates[0] == topics.Candidate("Q333", "астрономія", "наука", "Астрономія")


def test_same_name_with_a_qualifier_is_ambiguous(cache):
    web = FakeWeb()
    web.entity("Q28865", {"en": "Python"}, {"en": "Python (programming language)"})
    web.entity("Q271218", {"en": "Python"}, {"en": "Python (mythology)"})
    web.entity("Q2407", {"en": "C++"}, {"en": "C++"})
    web.search[("en", "Python")] = ["Python (programming language)", "C++", "Python (mythology)"]
    result, _ = run(web, cache, "Python", "en")
    assert result.leader is None
    assert [c.qid for c in result.candidates] == ["Q28865", "Q2407", "Q271218"]


def test_disambiguation_page_with_the_name_makes_it_ambiguous(cache):
    web = FakeWeb()
    web.entity("Q308", {"uk": "Меркурій"}, {"uk": "Меркурій"})
    web.entity("Q999", {"uk": "Меркурій"}, {"uk": "Меркурій (значення)"}, claims=DISAMBIGUATION)
    web.search[("uk", "Меркурій")] = ["Меркурій", "Меркурій (значення)"]
    result, _ = run(web, cache, "Меркурій", "uk")
    assert result.leader is None
    assert [c.qid for c in result.candidates] == ["Q308"]  # the disambiguation page is not a candidate


def test_single_candidate_is_a_leader_and_word_forms_come_from_search(cache):
    web = FakeWeb()
    web.entity("Q11019", {"uk": "інтервальне голодування"}, {"uk": "Інтервальне голодування"})
    web.search[("uk", "інтервального голодування")] = ["Інтервальне голодування"]
    result, _ = run(web, cache, "інтервального голодування", "uk")
    assert result.leader == "Q11019" and result.source == "search"


def test_no_exact_match_among_several_is_ambiguous(cache):
    web = FakeWeb()
    web.entity("Q1", {"uk": "IELTS"}, {"uk": "IELTS"})
    web.entity("Q2", {"uk": "TOEFL"}, {"uk": "TOEFL"})
    web.search[("uk", "іспит з англійської")] = ["IELTS", "TOEFL"]
    assert run(web, cache, "іспит з англійської", "uk")[0].leader is None


def test_wbsearchentities_fallback(cache):
    web = FakeWeb()
    web.wbsearch[("pl", "Kosmonautyka")] = [("Q5916", "kosmonautyka", "loty kosmiczne")]
    result, transport = run(web, cache, "Kosmonautyka", "pl")
    assert result.source == "wbsearch" and result.leader == "Q5916"
    assert result.candidates[0].title is None
    web.wbsearch[("pl", "Merkury")] = [("Q308", "Merkury", "planeta"), ("Q47652", "Merkury", "bóg")]
    assert run(web, cache, "Merkury", "pl")[0].leader is None
    web.wbsearch[("pl", "Wenus")] = [("Q313", "Wenus", "planeta"), ("Q47673", "Wenus (bogini)", "bogini")]
    assert run(web, cache, "Wenus", "pl")[0].leader == "Q313"


def test_nothing_found(cache):
    result, _ = run(FakeWeb(), cache, "zzzz", "uk")
    assert result.candidates == [] and result.leader is None
