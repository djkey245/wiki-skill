"""A fake Wikimedia: routes requests by host, path and query parameters (tests only)."""
import json
import threading
import urllib.parse

from wikitrends import thresholds as T
from wikitrends.http import HttpClient, Response


def reply(data, status=200):
    return Response(status, {"Content-Type": "application/json"}, json.dumps(data).encode("utf-8"))


def not_found():
    return reply({"type": "about:blank", "title": "Not Found", "method": "get", "status": 404,
                  "detail": "The date(s) you used are valid, but we either do not have data for those date(s), "
                            "or the project you asked for is not loaded yet."}, 404)


class Request:
    def __init__(self, url):
        parsed = urllib.parse.urlsplit(url)
        self.url = url
        self.host = parsed.netloc
        self.path = urllib.parse.unquote(parsed.path)
        self.raw_path = parsed.path
        self.params = dict(urllib.parse.parse_qsl(parsed.query))

    def titles(self, key="titles"):
        return self.params[key].split("|")


class RoutingTransport:
    """handler(Request) -> Response; every request is kept in `requests`."""

    def __init__(self, handler):
        self.handler = handler
        self.requests = []
        self._lock = threading.Lock()

    def fetch(self, url, headers, timeout):
        request = Request(url)
        with self._lock:
            self.requests.append(request)
        response = self.handler(request)
        if response is None:
            raise AssertionError(f"unexpected request: {url}")
        return response

    def count(self, predicate=lambda r: True):
        return sum(1 for r in self.requests if predicate(r))


def client(handler, **kwargs):
    transport = RoutingTransport(handler)
    return HttpClient(transport, sleep=lambda s: None, **kwargs), transport


def aqs_items(project, values, article=None):
    """AQS monthly items for {"2024-01": 10, ...}."""
    items = []
    for month, views in sorted(values.items()):
        item = {"project": project, "granularity": "monthly", "timestamp": month.replace("-", "") + "0100",
                "access": "all-access", "agent": "user", "views": views}
        if article is not None:
            item["article"] = article
        items.append(item)
    return reply({"items": items})


SITEMATRIX = {  # the shape of the live answer (en.wikipedia.org, 2026-09)
    "sitematrix": {
        "count": 9,
        "0": {"code": "uk", "name": "українська", "localname": "Ukrainian",
              "site": [{"url": "https://uk.wikipedia.org", "dbname": "ukwiki", "code": "wiki"},
                       {"url": "https://uk.wiktionary.org", "dbname": "ukwiktionary", "code": "wiktionary"}]},
        "1": {"code": "pl", "name": "polski", "localname": "Polish",
              "site": [{"url": "https://pl.wikipedia.org", "dbname": "plwiki", "code": "wiki"}]},
        "2": {"code": "no", "name": "norsk", "localname": "Norwegian",
              "site": [{"url": "https://no.wikipedia.org", "dbname": "nowiki", "code": "wiki"}]},
        "3": {"code": "be-tarask", "name": "беларуская (тарашкевіца)", "localname": "Belarusian (Taraškievica)",
              "site": [{"url": "https://be-tarask.wikipedia.org", "dbname": "be_x_oldwiki", "code": "wiki"}]},
        "4": {"code": "aa", "name": "Qafár af", "localname": "Afar",
              "site": [{"url": "https://aa.wikipedia.org", "dbname": "aawiki", "code": "wiki", "closed": True}]},
        "5": {"code": "yue", "name": "粵語", "localname": "Cantonese",
              "site": [{"url": "https://zh-yue.wikipedia.org", "dbname": "zh_yuewiki", "code": "wiki"},
                       {"url": "https://yue.wiktionary.org", "dbname": "yuewiktionary", "code": "wiktionary"}]},
        "6": {"code": "zh-yue", "name": "粵語", "localname": "Cantonese", "site": []},
        "7": {"code": "be-x-old", "name": "беларуская (тарашкевіца)", "localname": "Belarusian (Taraškievica orthography)",
              "site": []},
        "8": {"code": "simple", "name": "Simple English", "localname": "Simple English",
              "site": [{"url": "https://simple.wikipedia.org", "dbname": "simplewiki", "code": "wiki"}]},
        "specials": [
            {"url": "https://meta.wikimedia.org", "dbname": "metawiki", "code": "meta", "lang": "meta", "sitename": "Meta"},
            {"url": "https://test.wikipedia.org", "dbname": "testwiki", "code": "test", "lang": "en", "sitename": "Wikipedia"},
            {"url": "https://arbcom-en.wikipedia.org", "dbname": "arbcom_enwiki", "code": "arbcom-en", "lang": "en",
             "sitename": "Arbitration Committee", "private": True},
        ],
    }
}


class FakeWiki:
    """Monthly pageviews and page metadata of one Wikipedia, served like AQS and the Action API."""

    def __init__(self, code="uk", main_page="Головна сторінка"):
        self.code = code
        self.main_page = main_page
        self.views = {}  # title -> {month: views}
        self.aggregate = {}  # month -> views
        self.redirects = {}  # article -> list of (title, fragment or None)
        self.sitelink_redirects = {}  # redirect title -> article
        self.recent = {}  # title -> views over the last 60 days
        self.fail = set()  # titles answered with HTTP 400

    def handle(self, request):
        if request.host == "wikimedia.org":
            return self._aqs(request)
        if request.host == f"{self.code}.wikipedia.org":
            return self._action(request)
        return None

    def _aqs(self, request):
        parts = [urllib.parse.unquote(p) for p in request.raw_path.split("/")]
        kind = parts[5]
        start, end = parts[-2], parts[-1]
        first, last = f"{start[:4]}-{start[4:6]}", f"{end[:4]}-{end[4:6]}"
        inside = lambda values: {m: v for m, v in values.items() if first <= m <= last}  # noqa: E731
        if kind == "aggregate":
            return aqs_items(parts[6], inside(self.aggregate))
        title = parts[9].replace("_", " ")
        if title in self.fail:
            return reply({"title": "Bad request"}, 400)
        if title not in self.views:
            return not_found()
        return aqs_items(parts[6], {m: v for m, v in inside(self.views[title]).items() if v}, article=title)

    def _action(self, request):
        p = request.params
        if p.get("meta") == "siteinfo":
            return reply({"query": {"general": {"mainpage": self.main_page}}})
        if p.get("prop") == "pageviews":
            # like PageViewInfo: every page listed, daily data for PAGEVIEWS_TITLES_PER_REQUEST of them
            titles = request.titles()
            start = titles.index(p["pvipcontinue"]) if "pvipcontinue" in p else 0
            served = titles[start : start + T.PAGEVIEWS_TITLES_PER_REQUEST]
            pages = [{"ns": 0, "title": t, **({"pageviews": {"2026-08-01": self.recent.get(t, 0), "2026-08-02": None}}
                                               if t in served else {})} for t in titles]
            data = {"query": {"pages": pages}}
            if start + len(served) < len(titles):
                data["continue"] = {"pvipcontinue": titles[start + len(served)], "continue": "||"}
            return reply(data)
        if p.get("prop") == "redirects":
            pages, followed = [], []
            for title in request.titles():
                article = self.sitelink_redirects.get(title, title)
                if article != title:
                    followed.append({"from": title, "to": article})
                if article not in self.views:
                    pages.append({"ns": 0, "title": article, "missing": True})
                    continue
                rds = [{"pageid": 1, "ns": 0, "title": t, **({"fragment": f} if f else {})}
                       for t, f in self.redirects.get(article, [])]
                pages.append({"pageid": 1, "ns": 0, "title": article, **({"redirects": rds} if rds else {})})
            query = {"pages": pages}
            if followed:
                query["redirects"] = followed
            return reply({"query": query})
        return None
