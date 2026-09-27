import pytest

from wikifake import FakeWiki, client, reply
from wikitrends import redirects
from wikitrends.cache import Cache
from wikitrends.sitematrix import Site
from wikitrends.errors import WikitrendsError

UK = Site("uk", "ukwiki", "Ukrainian")


@pytest.fixture
def cache(tmp_path):
    return Cache(tmp_path / "cache.sqlite")


def rd(title, fragment=None):
    r = {"pageid": 1, "ns": 0, "title": title}
    if fragment:
        r["fragment"] = fragment
    return r


def test_resolve_sitelink_redirect_and_split_section_redirects(cache):
    def handler(request):
        assert request.params["rdprop"] == "title|fragment" and request.params["redirects"] == "1"
        return reply({"query": {
            "redirects": [{"from": "ESL", "to": "Англійська мова"}],
            "pages": [
                {"pageid": 1, "ns": 0, "title": "Англійська мова",
                 "redirects": [rd("Англ. мова"), rd("English"), rd("Англійська мова в Україні", "В Україні")]},
                {"pageid": 2, "ns": 0, "title": "IELTS"},
                {"ns": 0, "title": "Немає", "missing": True},
            ],
        }})

    http, transport = client(handler)
    out = redirects.resolve(http, cache, UK, ["ESL", "IELTS", "Немає"])
    assert out.target == {"ESL": "Англійська мова", "IELTS": "IELTS", "Немає": None}
    assert out.pages["Англійська мова"].redirects == ["Англ. мова", "English"]
    assert out.pages["Англійська мова"].section == ["Англійська мова в Україні"]
    again = redirects.resolve(http, cache, UK, ["ESL", "IELTS", "Немає"])
    assert again == out and transport.count() == 1


def test_api_error_is_not_cached_as_missing_page(cache):
    replies = [reply({"error": {"code": "badvalue", "info": "API error"}}),
               reply({"query": {"pages": [{"pageid": 1, "ns": 0, "title": "A"}]}})]
    http, transport = client(lambda request: replies.pop(0))
    with pytest.raises(WikitrendsError):
        redirects.resolve(http, cache, UK, ["A"])
    assert redirects.resolve(http, cache, UK, ["A"]).target["A"] == "A"
    assert transport.count() == 2


def test_resolve_follows_rdcontinue_and_batches(cache):
    titles = [f"T{i}" for i in range(60)]

    def handler(request):
        batch = request.titles()
        if request.params.get("rdcontinue"):
            return reply({"query": {"pages": [{"pageid": 1, "ns": 0, "title": batch[0], "redirects": [rd("R2")]}]}})
        data = {"query": {"pages": [{"pageid": i, "ns": 0, "title": t} for i, t in enumerate(batch)]}}
        data["query"]["pages"][0]["redirects"] = [rd("R1")]
        if batch[0] == "T0":
            data["continue"] = {"rdcontinue": "5|R2", "continue": "||"}
        return reply(data)

    http, transport = client(handler)
    out = redirects.resolve(http, cache, UK, titles)
    assert transport.count() == 3  # 50 + continue + 10
    assert out.pages["T0"].redirects == ["R1", "R2"]
    assert out.pages["T50"].redirects == ["R1"]


def test_recent_views_sums_days_and_ignores_nulls():
    def handler(request):
        assert request.params["prop"] == "pageviews" and request.params["pvipdays"] == "60"
        return reply({"query": {"pages": [
            {"pageid": 1, "ns": 0, "title": "A", "pageviews": {"2026-08-01": 5, "2026-08-02": None, "2026-08-03": 7}},
            {"pageid": 2, "ns": 0, "title": "B", "pageviews": {"2026-08-01": None}},
        ]}})

    http, _ = client(handler)
    assert redirects.recent_views(http, UK, ["A", "B"]) == {"A": 12, "B": 0}


def test_select_top_by_recent_views_or_all():
    rds = ["a", "b", "c", "d"]
    recent = {"a": 1, "b": 50, "c": 30, "d": 19}
    sel = redirects.select(rds, recent, 2)
    assert sel.chosen == ["b", "c"] and sel.dropped == 2 and sel.dropped_recent_share == pytest.approx(0.2)
    assert redirects.select(rds, None, 2).chosen == rds  # limit lifted
    assert redirects.select(rds[:2], recent, 2).chosen == ["a", "b"]


def test_recent_views_follow_pvipcontinue(cache):
    wiki = FakeWiki()
    titles = [f"R{i}" for i in range(12)]
    wiki.recent = {t: i for i, t in enumerate(titles)}
    http, transport = client(wiki.handle)
    assert redirects.recent_views(http, UK, titles) == wiki.recent
    assert transport.count() == 3  # 5 titles with data per request
