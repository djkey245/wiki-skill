import pytest

from wikifake import client, reply
from wikitrends import wikidata
from wikitrends.cache import Cache


@pytest.fixture
def cache(tmp_path):
    return Cache(tmp_path / "cache.sqlite")


def item(qid, labels=None, sitelinks=None, claims=None, **extra):
    data = {
        "type": "item", "id": qid,
        "labels": {lang: {"language": lang, "value": v} for lang, v in (labels or {}).items()},
        "descriptions": {},
        "sitelinks": {site: {"site": site, "title": t, "badges": []} for site, t in (sitelinks or {}).items()},
    }
    if claims is not None:
        data["claims"] = {
            prop: [{"mainsnak": {"snaktype": "value", "property": prop,
                                 "datavalue": {"value": {"entity-type": "item", "id": v}, "type": "wikibase-entityid"}},
                    "type": "statement", "rank": rank}
                   for v, rank in values]
            for prop, values in claims.items()
        }
    data.update(extra)
    return data


def entities_handler(entities):
    def handler(request):
        if request.params.get("action") != "wbgetentities":
            return None
        assert "maxlag" not in request.params
        ids = request.params["ids"].split("|")
        for i in ids:
            if not i[1:].isdigit() or int(i[1:]) > 10**9:  # malformed or never issued: the whole batch fails
                return reply({"error": {"code": "no-such-entity", "info": f"Could not find an entity with the ID \"{i}\".", "id": i}})
        return reply({"entities": {i: entities.get(i, {"id": i, "missing": ""}) for i in ids}, "success": 1})
    return handler


def test_sitelinks_labels_claims_and_missing(cache):
    entities = {
        "Q1": item("Q1", {"uk": "Пітон", "en": "Python"}, {"ukwiki": "Python", "plwiki": "Python (język programowania)"},
                   claims={"P31": [("Q9143", "normal"), ("Q5", "deprecated")], "P279": [("Q7397", "preferred")]}),
        "Q2": item("Q2", {"de": "Zwei"}),
    }
    http, transport = client(entities_handler(entities))
    out = wikidata.get_entities(http, cache, ["Q1", "Q2", "Q3"], languages=["uk", "pl"], claims=True)
    assert out["Q1"].sitelinks["plwiki"] == "Python (język programowania)"
    assert out["Q1"].claims == {"P31": ["Q9143"], "P279": ["Q7397"]}
    assert out["Q3"].missing and wikidata.title_in(out["Q3"], "ukwiki") is None
    params = transport.requests[0].params
    assert params["props"] == "labels|descriptions|sitelinks|claims"
    assert params["languages"] == "en|pl|uk"


def test_label_fallback_chain():
    e = wikidata.Entity("Q1", "Q1", labels={"en": "Python", "pl": "Pyton"})
    assert e.label("uk") == "Python"
    assert wikidata.Entity("Q1", "Q1", labels={"pl": "Pyton", "cs": "Krajta"}).label("uk", ["cs", "pl"]) == "Krajta"
    assert wikidata.Entity("Q1", "Q1", labels={"pl": "Pyton"}).label("uk") == "Pyton"
    assert wikidata.Entity("Q1", "Q1").label("uk") == "Q1"


def test_redirected_qid_is_found_by_origin():
    data = {"entities": {"Q10": item("Q20", {"en": "Target"}, redirects={"from": "Q10", "to": "Q20"})}}
    out = wikidata.parse_entities(data, ["Q10"])
    assert out["Q10"].canonical == "Q20" and out["Q10"].labels == {"en": "Target"}


def test_batches_of_50_and_cache(cache):
    qids = [f"Q{i}" for i in range(1, 121)]
    http, transport = client(entities_handler({q: item(q, {"en": q}) for q in qids}))
    wikidata.get_entities(http, cache, qids, languages=["uk"])
    assert transport.count() == 3
    again = wikidata.get_entities(http, cache, qids, languages=["uk"])
    assert transport.count() == 3
    assert again["Q120"].label("uk") == "Q120"


def test_invalid_id_does_not_fail_the_batch(cache):
    http, transport = client(entities_handler({"Q1": item("Q1", {"en": "One"})}))
    out = wikidata.get_entities(http, cache, ["Q1", "QX", "Q999999999999", "Q2"], languages=["uk"])
    assert out["Q1"].label("uk") == "One"
    assert out["QX"].missing and out["Q999999999999"].missing and out["Q2"].missing
    assert transport.count() == 3  # the batch is repeated without each id that breaks it
