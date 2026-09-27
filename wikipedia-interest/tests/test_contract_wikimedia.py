"""Contract tests of the data layer on recorded live answers (tests/fixtures, no network)."""
from pathlib import Path

import pytest

from record_fixtures import END, FIXTURES, WIKIDATA_IDS, WINDOW_START
from wikitrends import months, pageviews, redirects, search, sitematrix, wikidata
from wikitrends.cache import Cache
from wikitrends.http import HttpClient, ReplayTransport


def replay(name, tmp_path):
    directory = FIXTURES / name
    if not directory.exists():
        pytest.fail(f"no fixtures for {name!r}: run tests/record_fixtures.py (plan 04, Task 9)")
    return HttpClient(ReplayTransport(directory), sleep=lambda s: None), Cache(tmp_path / "cache.sqlite")


def test_sitematrix_and_main_pages(tmp_path):  # П4
    http, cache = replay("sitematrix", tmp_path)
    m = sitematrix.SiteMatrix.load(http, cache)
    assert {"uk", "pl", "cs", "no", "be-tarask", "simple"} <= set(m.sites)
    assert m.resolve("nb").code == "no" and m.resolve("uk").dbname == "ukwiki"
    assert sitematrix.main_page(http, cache, m.resolve("uk"))


def test_wikidata_entities(tmp_path):  # П2
    http, cache = replay("wikidata", tmp_path)
    out = wikidata.get_entities(http, cache, WIKIDATA_IDS, languages=["uk", "pl", "cs"], claims=True)
    assert out["Q999999999999"].missing and out["Q6"].missing
    assert out["Q33428031"].canonical == "Q100000" and not out["Q33428031"].missing
    assert "plwiki" in out["Q28865"].sitelinks and out["Q28865"].label("uk")
    assert out["Q1899"].claims.get("P31")


def test_search_and_links(tmp_path):  # П5
    http, cache = replay("search", tmp_path)
    uk = sitematrix.SiteMatrix.load(http, cache).resolve("uk")
    hits = search.search_pages(http, cache, uk, "вивчення англійської мови")
    assert hits and all(h.title for h in hits)
    assert any(h.qid for h in search.search_pages(http, cache, uk, "мовою програмування Python"))
    assert search.morelike(http, cache, uk, "Python")
    assert len(search.outgoing_links(http, cache, uk, "Python")) > 50
    assert search.wbsearch(http, cache, "Python", "uk")


def test_redirects_resolution_and_ranking(tmp_path):  # П1, П6
    http, cache = replay("redirects", tmp_path)
    en = sitematrix.SiteMatrix.load(http, cache).resolve("en")
    res = redirects.resolve(http, cache, en, ["Kiev", "Kyiv", "AC/DC", "Python (programming language)"])
    assert res.target["Kiev"] == res.target["Kyiv"] == "Kyiv"
    assert "Kiev" in res.pages["Kyiv"].redirects
    many = res.pages[res.target["Python (programming language)"]].redirects
    assert len(many) > 10
    recent = redirects.recent_views(http, en, many[:60])
    assert set(recent) == set(many[:60]) and sum(recent.values()) > 0


def test_pageviews_series_and_boundary(tmp_path):  # П3
    http, cache = replay("pageviews", tmp_path)
    m = sitematrix.SiteMatrix.load(http, cache)
    en = m.resolve("en")
    mains = {m.resolve(c).project: sitematrix.main_page(http, cache, m.resolve(c)) for c in ("uk", "pl", "cs")}
    assert pageviews.latest_month(http, mains, "2026-01") == "2025-12"
    out = pageviews.fetch_series(http, cache, en.project, ["Kyiv", "Kiev", "AC/DC", "No such page 1234567"], "2015-07", END)
    assert not out.failed
    assert len(out.values["Kyiv"]) == months.diff(END, "2015-07") + 1
    assert out.values["Kyiv"][-1] > 0  # the last month is included
    assert sum(out.values["No such page 1234567"]) == 0  # 404 -> zeros
    assert sum(out.values["AC/DC"]) > 0  # '/' encoded
    aggregate, revised = pageviews.fetch_aggregate(http, cache, en.project, "2015-07", END)
    assert revised == [] and aggregate["2025-12"] > 10**8


def test_collect_two_languages(tmp_path):
    http, cache = replay("collect", tmp_path)
    m = sitematrix.SiteMatrix.load(http, cache)
    for code in ("pl", "cs"):
        site = m.resolve(code)
        entities = wikidata.get_entities(http, cache, ["Q28865", "Q2407", "Q15777"], languages=[code])
        sitelinks = {q: e.sitelinks[site.dbname] for q, e in entities.items() if site.dbname in e.sitelinks}
        plan = pageviews.plan_lang(http, cache, code, site, sitelinks, WINDOW_START, END)
        out = pageviews.fetch_lang(http, cache, plan)
        assert out.status == "ok"
        assert out.data.window_start == months.diff(WINDOW_START, out.data.first_month)
        assert all(d and d > 0 for d in out.data.denominator)
        assert out.data.articles
