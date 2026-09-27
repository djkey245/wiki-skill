import pytest

from wikifake import client, reply
from wikitrends import search
from wikitrends.cache import Cache
from wikitrends.sitematrix import Site

UK = Site("uk", "ukwiki", "Ukrainian")


@pytest.fixture
def cache(tmp_path):
    return Cache(tmp_path / "cache.sqlite")


def page(title, index=None, qid=None, **extra):
    p = {"pageid": abs(hash(title)) % 10**6, "ns": 0, "title": title}
    if index is not None:
        p["index"] = index
    if qid:
        p["pageprops"] = {"wikibase_item": qid}
    p.update(extra)
    return p


def test_search_pages_orders_by_index_and_reads_qids(cache):
    def handler(request):
        assert request.params["generator"] == "search" and request.params["gsrnamespace"] == "0"
        assert request.params["gsrsearch"] == "вивчення англійської"
        return reply({"query": {"pages": [page("IELTS", 2, "Q1"), page("Англійська мова", 1, "Q2"), page("Без елемента", 3)]}})

    http, transport = client(handler)
    hits = search.search_pages(http, cache, UK, "вивчення англійської")
    assert hits == [search.Hit("Англійська мова", "Q2"), search.Hit("IELTS", "Q1"), search.Hit("Без елемента", None)]
    search.search_pages(http, cache, UK, "вивчення англійської")
    assert transport.count() == 1


def test_morelike_excludes_the_seed(cache):
    def handler(request):
        assert request.params["gsrsearch"] == "morelike:IELTS"
        return reply({"query": {"pages": [page("IELTS", 1, "Q1"), page("TOEFL", 2, "Q3")]}})

    http, _ = client(handler)
    assert search.morelike(http, cache, UK, "IELTS") == [search.Hit("TOEFL", "Q3")]


def test_empty_search_result_has_no_query_key(cache):
    http, _ = client(lambda r: reply({"batchcomplete": True}))
    assert search.search_pages(http, cache, UK, "zzzz") == []


def test_wbsearch(cache):
    def handler(request):
        assert request.host == "www.wikidata.org" and request.params["action"] == "wbsearchentities"
        return reply({"search": [{"id": "Q1860", "label": "English", "description": "West Germanic language"}], "success": 1})

    http, _ = client(handler)
    assert search.wbsearch(http, cache, "English", "en") == [search.EntityHit("Q1860", "English", "West Germanic language")]


def test_outgoing_links_follow_continue_up_to_max_pages(cache):
    pages = {None: ([page("A", qid="Q1"), page("B")], {"gplcontinue": "1|B", "continue": "gplcontinue||"}),
             "1|B": ([page("C", qid="Q3"), page("B", qid="Q2")], {"gplcontinue": "1|D", "continue": "gplcontinue||"}),
             "1|D": ([page("D", qid="Q4")], None)}

    def handler(request):
        batch, cont = pages[request.params.get("gplcontinue")]
        data = {"query": {"pages": batch}}
        if cont:
            data["continue"] = cont
        return reply(data)

    http, transport = client(handler)
    hits = search.outgoing_links(http, cache, UK, "Seed", max_pages=2)
    assert hits == [search.Hit("A", "Q1"), search.Hit("B", "Q2"), search.Hit("C", "Q3")]
    assert transport.count() == 2
    assert transport.requests[1].params["continue"] == "gplcontinue||"


def test_qids_for_titles_follow_normalization_and_redirects():
    def handler(request):
        return reply({"query": {
            "normalized": [{"fromencoded": False, "from": "python", "to": "Python"}],
            "redirects": [{"from": "Python", "to": "Python (мова програмування)"}],
            "pages": [page("Python (мова програмування)", qid="Q28865"), page("Немає", missing=True)],
        }})

    http, _ = client(handler)
    assert search.qids_for_titles(http, UK, ["python", "Немає"]) == {"python": "Q28865", "Немає": None}
