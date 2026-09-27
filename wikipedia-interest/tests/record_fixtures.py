"""Record live Wikimedia answers into tests/fixtures/<scenario>/ (run by hand, needs network).

    WIKITRENDS_CONTACT=you@example.com uv run python tests/record_fixtures.py [scenario ...]
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from wikitrends import pageviews, redirects, search, sitematrix, wikidata  # noqa: E402
from wikitrends.cache import Cache  # noqa: E402
from wikitrends.http import HttpClient, RecordingTransport, UrllibTransport  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
END = "2025-12"  # fixed window end: replays must not depend on the recording date
WINDOW_START = "2024-01"
WIKIDATA_IDS = ["Q28865", "Q1860", "Q1899", "Q33428031", "Q6", "Q999999999999"]


def matrix(http, cache):
    return sitematrix.SiteMatrix.load(http, cache)


def scenario_sitematrix(http, cache):
    m = matrix(http, cache)
    for code in ("uk", "pl", "cs"):
        sitematrix.main_page(http, cache, m.resolve(code))


def scenario_wikidata(http, cache):
    # Python (programming language), English language, Kyiv, a redirect (Q33428031 -> Q100000),
    # a deleted item (Q6) and a never-issued id that fails the whole batch
    wikidata.get_entities(http, cache, WIKIDATA_IDS, languages=["uk", "pl", "cs"], claims=True)


def scenario_search(http, cache):
    uk = matrix(http, cache).resolve("uk")
    search.search_pages(http, cache, uk, "вивчення англійської мови")
    search.search_pages(http, cache, uk, "мовою програмування Python")  # word forms
    search.morelike(http, cache, uk, "Python")
    search.outgoing_links(http, cache, uk, "Python")
    search.wbsearch(http, cache, "Python", "uk")


def scenario_redirects(http, cache):
    en = matrix(http, cache).resolve("en")
    resolution = redirects.resolve(http, cache, en, ["Kiev", "Kyiv", "AC/DC", "Python (programming language)"])
    many = resolution.pages[resolution.target["Python (programming language)"]].redirects
    redirects.recent_views(http, en, many[:60])


def scenario_pageviews(http, cache):
    m = matrix(http, cache)
    en = m.resolve("en")
    mains = {}
    for code in ("uk", "pl", "cs"):
        site = m.resolve(code)
        mains[site.project] = sitematrix.main_page(http, cache, site)
    pageviews.latest_month(http, mains, "2026-01")
    pageviews.fetch_series(http, cache, en.project, ["Kyiv", "Kiev", "AC/DC", "No such page 1234567"], "2015-07", END)
    pageviews.fetch_aggregate(http, cache, en.project, "2015-07", END)


def scenario_collect(http, cache):
    m = matrix(http, cache)
    for code in ("pl", "cs"):
        site = m.resolve(code)
        entities = wikidata.get_entities(http, cache, ["Q28865", "Q2407", "Q15777"], languages=[code])
        sitelinks = {q: e.sitelinks[site.dbname] for q, e in entities.items() if site.dbname in e.sitelinks}
        plan = pageviews.plan_lang(http, cache, code, site, sitelinks, WINDOW_START, END)
        pageviews.fetch_lang(http, cache, plan)


from wikitrends import candidates, topics  # noqa: E402
from wikitrends import thresholds as T  # noqa: E402

FIND_QUERIES = [
    ("uk", "астрономія"),
    ("en", "Python"),
    ("uk", "Меркурій"),
    ("uk", "Ява"),
    ("uk", "інтервального голодування"),
    ("uk", "вивчення англійської мови"),
    ("pl", "astronomia"),
    ("cs", "astronomie"),
]


def scenario_find(http, cache):
    m = matrix(http, cache)
    for lang, query in FIND_QUERIES:
        topics.find(http, cache, m.resolve(lang), query, lang)


def scenario_suggest(http, cache):
    candidates.suggest(http, cache, matrix(http, cache), ["Q333"], ["uk", "pl", "cs"], "uk")


def scenario_types(http, cache):
    wikidata.get_entities(http, cache, list(T.EXCLUDED_TYPES), languages=["en"])


import io  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402

from wikitrends import cli  # noqa: E402

QUICK_TODAY = "2026-09"  # fixed current month: the window ends in 2026-08 whenever the fixtures are replayed
QUICK_RUNS = {
    # task example 1: compare two languages
    "quick_pl_cs": ["quick", "астрономія", "--lang", "uk", "--langs", "pl,cs", "--display-lang", "uk"],
    # task example 3: which audiences to explore next (matrix, recommendations, small editions)
    "quick_five": ["quick", "астрономія", "--lang", "uk", "--langs", "uk,pl,cs,sk,hr", "--display-lang", "uk"],
}


def run_quick(http, argv):
    """The whole CLI on live data; prints what a person checks by eye (task 7)."""
    with tempfile.TemporaryDirectory() as tmp:
        stdout = io.StringIO()
        started = time.monotonic()
        code = cli.run(argv, env={"WIKITRENDS_HOME": tmp, "WIKITRENDS_CACHE_DIR": tmp}, cwd=Path(tmp), home=Path(tmp),
                       stdout=stdout, http_factory=lambda deadline: http, today=QUICK_TODAY)
        elapsed = time.monotonic() - started
    out = json.loads(stdout.getvalue())
    print(f"exit {code}, {elapsed:.0f} s, {http.requests_made} requests, stdout {len(stdout.getvalue())} chars", file=sys.stderr)
    print(out.get("answer_md") or json.dumps(out, ensure_ascii=False, indent=1), file=sys.stderr)


def scenario_quick_pl_cs(http, cache):
    run_quick(http, QUICK_RUNS["quick_pl_cs"])


def scenario_quick_five(http, cache):
    run_quick(http, QUICK_RUNS["quick_five"])


SCENARIOS = {name[len("scenario_"):]: fn for name, fn in globals().items() if name.startswith("scenario_")}


def main(names):
    for name in names or SCENARIOS:
        target = FIXTURES / name
        shutil.rmtree(target, ignore_errors=True)
        with tempfile.TemporaryDirectory() as tmp:
            http = HttpClient(
                RecordingTransport(UrllibTransport(), target),
                contact=os.environ.get("WIKITRENDS_CONTACT"),
                token=os.environ.get("WIKIMEDIA_API_TOKEN"),
            )
            SCENARIOS[name](http, Cache(Path(tmp) / "cache.sqlite"))
        print(f"{name}: {http.requests_made} requests -> {target}")


if __name__ == "__main__":
    main(sys.argv[1:])
