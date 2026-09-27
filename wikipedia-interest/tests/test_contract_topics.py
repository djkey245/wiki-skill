"""Contract tests of find / suggest on recorded live answers (tests/fixtures, no network)."""
import pytest

from record_fixtures import FIND_QUERIES
from test_contract_wikimedia import replay
from wikitrends import candidates, sitematrix, topics, wikidata
from wikitrends import thresholds as T


def test_excluded_types_are_what_the_comments_say(tmp_path):  # П8
    http, cache = replay("types", tmp_path)
    entities = wikidata.get_entities(http, cache, list(T.EXCLUDED_TYPES), languages=["en"])
    for qid, name in T.EXCLUDED_TYPES.items():
        assert entities[qid].label("en").lower() == name.lower(), qid


def test_find_on_live_search(tmp_path):  # П9
    http, cache = replay("find", tmp_path)
    m = sitematrix.SiteMatrix.load(http, cache)
    results = {(lang, q): topics.find(http, cache, m.resolve(lang), q, lang) for lang, q in FIND_QUERIES}
    assert results[("uk", "астрономія")].leader == "Q333"
    assert results[("pl", "astronomia")].leader == "Q333"
    assert results[("cs", "astronomie")].leader == "Q333"
    assert results[("en", "Python")].leader is None
    assert results[("uk", "Меркурій")].leader is None
    assert results[("uk", "Ява")].leader is None
    for result in results.values():
        assert 1 <= len(result.candidates) <= T.FIND_CANDIDATES
        assert all(c.label for c in result.candidates)


def test_suggest_on_live_data(tmp_path):  # П7
    http, cache = replay("suggest", tmp_path)
    m = sitematrix.SiteMatrix.load(http, cache)
    result = candidates.suggest(http, cache, m, ["Q333"], ["uk", "pl", "cs"], "uk")
    assert result.draft[0] == "Q333" and len(result.draft) >= 4
    assert result.ranked and all(c.coverage for c in result.ranked)
    assert any(c.backlinks for c in result.ranked)  # pltitles works on a batch
    assert any(c.wikidata for c in result.ranked) or result.excluded_types
    assert http.requests_made <= candidates.estimate_requests(1, 3)
