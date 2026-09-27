import pytest

from wikifake import FakeWiki, client, not_found
from wikitrends import months, pageviews
from wikitrends.cache import Cache
from wikitrends.deadline import Deadline
from wikitrends.sitematrix import Site

UK = Site("uk", "ukwiki", "Ukrainian")


@pytest.fixture
def cache(tmp_path):
    return Cache(tmp_path / "cache.sqlite")


def flat(first, last, value):
    return {m: value for m in months.span(first, last)}


def test_urls_encode_titles_and_cover_whole_months():
    url = pageviews.per_article_url("uk.wikipedia", "AC/DC (гурт)", "2023-01", "2024-02")
    assert url == ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/uk.wikipedia/all-access/user/"
                   "AC%2FDC_%28%D0%B3%D1%83%D1%80%D1%82%29/monthly/2023010100/2024022900")
    assert pageviews.aggregate_url("pl.wikipedia", "2024-12", "2024-12").endswith("/monthly/2024120100/2024123100")


def test_history_start_counts_from_window_start_and_clips():
    assert pageviews.history_start("2024-01") == "2021-01"
    assert pageviews.history_start("2016-01") == "2015-07"


def test_fetch_series_zeros_404_failures_and_cache(cache):
    wiki = FakeWiki()
    wiki.views = {"A": {"2024-01": 10, "2024-03": 30}, "B": {}}
    wiki.fail = {"C"}
    http, transport = client(wiki.handle)
    out = pageviews.fetch_series(http, cache, "uk.wikipedia", ["A", "Missing", "C"], "2024-01", "2024-06")
    assert out.values["A"] == [10, 0, 30, 0, 0, 0]
    assert out.values["Missing"] == [0] * 6
    assert out.failed == {"C": "HTTP 400"}
    before = transport.count()
    again = pageviews.fetch_series(http, cache, "uk.wikipedia", ["A", "Missing"], "2024-01", "2024-04")
    assert again.values["A"] == [10, 0, 30, 0]
    assert transport.count() == before  # months older than PROVISIONAL_MONTHS come from the cache


def test_range_404_does_not_erase_cached_history(cache):
    wiki = FakeWiki()
    wiki.views = {"Old title": {"2024-01": 100}}

    def handler(request):
        if "/2024020100/" in request.raw_path:
            return not_found()
        return wiki.handle(request)

    http, _ = client(handler)
    project = "uk.wikipedia"
    pageviews.fetch_series(http, cache, project, ["Old title"], "2024-01", "2024-02")
    first = pageviews.fetch_series(http, cache, project, ["Old title"], "2024-01", "2024-04")
    again = pageviews.fetch_series(http, cache, project, ["Old title"], "2024-01", "2024-04")
    assert first.values["Old title"] == [100, 0, 0, 0]
    assert again.values["Old title"] == first.values["Old title"]


def test_range_404_does_not_hide_other_uncached_months(cache):
    wiki = FakeWiki()
    wiki.views = {"Old title": {"2024-01": 100}}
    http, transport = client(wiki.handle)
    pageviews.fetch_series(http, cache, "uk.wikipedia", ["Old title"], "2024-03", "2024-04")
    wiki.views.pop("Old title")
    pageviews.fetch_series(http, cache, "uk.wikipedia", ["Old title"], "2024-05", "2024-06")
    wiki.views["Old title"] = {"2024-01": 100}
    out = pageviews.fetch_series(http, cache, "uk.wikipedia", ["Old title"], "2024-01", "2024-06")
    assert out.values["Old title"][0] == 100
    assert transport.count() == 3


def test_fetch_series_after_deadline_reports_not_started(cache):
    wiki = FakeWiki()
    wiki.views = {"A": {"2024-01": 10}}
    http, _ = client(wiki.handle, deadline=Deadline(0))
    out = pageviews.fetch_series(http, cache, "uk.wikipedia", ["A"], "2024-01", "2024-06")
    assert out.not_started == ["A"] and out.values == {}


def test_revised_aggregate_invalidates_article_cache(cache):
    wiki = FakeWiki()
    wiki.aggregate = flat("2024-01", "2024-12", 1000)
    wiki.views = {"A": flat("2024-01", "2024-12", 10)}
    http, transport = client(wiki.handle)
    pageviews.fetch_aggregate(http, cache, "uk.wikipedia", "2024-01", "2024-12")
    pageviews.fetch_series(http, cache, "uk.wikipedia", ["A"], "2024-01", "2024-12")
    wiki.aggregate["2024-05"] = 900
    wiki.views["A"]["2024-05"] = 7
    _, revised = pageviews.fetch_aggregate(http, cache, "uk.wikipedia", "2024-01", "2024-12")
    assert revised == ["2024-05"]
    out = pageviews.fetch_series(http, cache, "uk.wikipedia", ["A"], "2024-01", "2024-12")
    assert out.values["A"][4] == 7


def test_latest_month_needs_aggregate_and_per_article_everywhere():
    uk, pl = FakeWiki("uk"), FakeWiki("pl", "Strona główna")
    uk.aggregate = flat("2026-06", "2026-08", 1)
    uk.views = {uk.main_page: flat("2026-06", "2026-08", 1)}
    pl.aggregate = flat("2026-06", "2026-08", 1)
    pl.views = {pl.main_page: flat("2026-06", "2026-07", 1)}  # per-article lags a month

    def handler(request):
        if "/pl.wikipedia/" in request.raw_path:
            return pl.handle(request)
        return uk.handle(request)

    http, _ = client(handler)
    mains = {"uk.wikipedia": uk.main_page, "pl.wikipedia": pl.main_page}
    assert pageviews.latest_month(http, mains, "2026-09") == "2026-07"
    pl.views = {}
    assert pageviews.latest_month(http, mains, "2026-09") is None


def english_wiki():
    wiki = FakeWiki()
    first, last = "2021-01", "2024-12"
    wiki.aggregate = flat(first, last, 1_000_000)
    wiki.views = {
        wiki.main_page: flat(first, last, 100_000),
        "Англійська мова": flat(first, last, 500),
        "IELTS": flat(first, last, 200),
        "Англ. мова": flat(first, last, 50),
        "TOEFL": {m: 80 for m in months.span("2022-06", last)},  # renamed mid-history
    }
    wiki.sitelink_redirects = {"ESL": "Англійська мова"}
    wiki.redirects = {
        "Англійська мова": [("Англ. мова", None), ("IELTS", None), ("Англійська в Україні", "В Україні")],
        "TOEFL": [(f"T{i}", None) for i in range(12)],
    }
    for i in range(12):
        wiki.views[f"T{i}"] = {m: 10 for m in months.span(first, "2022-05")}
    return wiki


def test_plan_lang_resolves_and_deduplicates(cache):
    wiki = english_wiki()
    http, _ = client(wiki.handle)
    sitelinks = {"Q1": "Англійська мова", "Q2": "ESL", "Q3": "IELTS", "Q4": "Немає"}
    plan = pageviews.plan_lang(http, cache, "uk", UK, sitelinks, "2024-01", "2024-12")
    assert plan.first_month == "2021-01" and plan.main_page == "Головна сторінка"
    assert plan.articles == {"Q1": "Англійська мова", "Q3": "IELTS"}
    assert plan.exclusions["Q2"].code == "duplicate_of" and plan.exclusions["Q2"].detail == "Q1"


def test_fetch_lang_builds_lang_data(cache):
    wiki = english_wiki()
    http, _ = client(wiki.handle)
    sitelinks = {"Q1": "Англійська мова", "Q3": "IELTS", "Q5": "TOEFL"}
    plan = pageviews.plan_lang(http, cache, "uk", UK, sitelinks, "2024-01", "2024-12")
    assert pageviews.estimate_requests(cache, plan, max_redirects=10) == 1 + 5 + 12 + 3  # all redirects may be needed if the limit lifts
    out = pageviews.fetch_lang(http, cache, plan, max_redirects=10)
    assert out.status == "ok"
    data = out.data
    assert data.first_month == "2021-01" and data.window_start == 36 and len(data.denominator) == 48
    assert data.denominator[0] == 900_000
    # IELTS is another QID's article: not a redirect of Q1; the section redirect is excluded
    assert out.redirect_info["Q1"].chosen == ["Англ. мова"]
    assert out.redirect_info["Q1"].section == ["Англійська в Україні"]
    assert data.articles["Q1"].views[0] == 550 and data.articles["Q1"].redirect_views[0] == 50
    # TOEFL starts mid-history: the redirect limit is lifted and old titles fill the gap
    assert out.redirect_info["Q5"].limit_lifted and len(out.redirect_info["Q5"].chosen) == 12
    assert data.articles["Q5"].views[0] == 120 and data.articles["Q5"].views[-1] == 80
    # cached now, except the aggregate (always re-requested) and titles with zeros in provisional months
    known = pageviews.plan_lang(http, cache, "uk", UK, {"Q1": "Англійська мова", "Q3": "IELTS"}, "2024-01", "2024-12")
    assert pageviews.estimate_requests(cache, known, max_redirects=10) == 1


def test_request_estimate_covers_lifted_redirect_limit(cache):
    wiki = english_wiki()
    http, transport = client(wiki.handle)
    plan = pageviews.plan_lang(http, cache, "uk", UK, {"Q5": "TOEFL"}, "2024-01", "2024-12")
    estimate = pageviews.estimate_requests(cache, plan, max_redirects=2)
    before = transport.count()
    out = pageviews.fetch_lang(http, cache, plan, max_redirects=2)
    assert out.redirect_info["Q5"].limit_lifted
    assert estimate >= transport.count() - before


def test_fetch_lang_ranks_redirects_over_the_limit(cache):
    wiki = english_wiki()
    wiki.redirects["IELTS"] = [(f"I{i}", None) for i in range(5)]
    for i in range(5):
        wiki.views[f"I{i}"] = flat("2021-01", "2024-12", i)
        wiki.recent[f"I{i}"] = 10 * i
    http, transport = client(wiki.handle)
    plan = pageviews.plan_lang(http, cache, "uk", UK, {"Q3": "IELTS"}, "2024-01", "2024-12")
    out = pageviews.fetch_lang(http, cache, plan, max_redirects=2)
    info = out.redirect_info["Q3"]
    assert info.chosen == ["I4", "I3"] and info.dropped == 3
    assert info.dropped_recent_share == pytest.approx(30 / 100)
    assert transport.count(lambda r: r.params.get("prop") == "pageviews") == 1


def test_failed_article_is_fetch_failed(cache):
    wiki = english_wiki()
    wiki.fail = {"IELTS"}
    http, _ = client(wiki.handle)
    plan = pageviews.plan_lang(http, cache, "uk", UK, {"Q1": "Англійська мова", "Q3": "IELTS"}, "2024-01", "2024-12")
    out = pageviews.fetch_lang(http, cache, plan)
    assert out.status == "ok" and out.data.exclusions["Q3"].code == "fetch_failed"
    assert list(out.data.articles) == ["Q1"]


def test_failed_main_page_fails_the_language(cache):
    wiki = english_wiki()
    wiki.fail = {wiki.main_page}
    http, _ = client(wiki.handle)
    plan = pageviews.plan_lang(http, cache, "uk", UK, {"Q1": "Англійська мова"}, "2024-01", "2024-12")
    out = pageviews.fetch_lang(http, cache, plan)
    assert out.status == "failed" and "main page" in out.error


def test_fetch_lang_after_deadline_is_partial(cache):
    wiki = english_wiki()
    http, _ = client(wiki.handle)
    plan = pageviews.plan_lang(http, cache, "uk", UK, {"Q1": "Англійська мова"}, "2024-01", "2024-12")
    late, _ = client(wiki.handle, deadline=Deadline(0))
    assert pageviews.fetch_lang(late, cache, plan).status == "partial"
