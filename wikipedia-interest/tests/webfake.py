"""Several Wikipedias and Wikidata for topic search, candidates and CLI tests (tests only).

Answers have the shape of the live ones (plan 04, part B): generator results carry `index`
and `pageprops.wikibase_item`, wbgetentities returns labels/descriptions/sitelinks/claims.
"""
import copy
import urllib.parse

from wikifake import SITEMATRIX, aqs_items, client, reply
from wikitrends import months


def _sitematrix(*codes):
    data = copy.deepcopy(SITEMATRIX)
    count = data["sitematrix"]["count"]
    for i, code in enumerate(codes):
        data["sitematrix"][str(count + i)] = {
            "code": code, "name": code, "localname": code,
            "site": [{"url": f"https://{code}.wikipedia.org", "dbname": f"{code}wiki", "code": "wiki"}],
        }
    data["sitematrix"]["count"] = count + len(codes)
    return data


class FakeWeb:
    def __init__(self, published="2026-08"):
        self.entities = {}  # qid -> wbgetentities entity
        self.search = {}  # (lang, query) -> titles in relevance order
        self.morelike = {}  # (lang, title) -> titles
        self.links = {}  # (lang, title) -> titles the page links to
        self.wbsearch = {}  # (language, query) -> [(qid, label, description)]
        self.published = published  # latest month in aggregate and per-article
        self.matrix = _sitematrix("en", "cs")
        self.views = {}  # (lang, title) -> {month: views} or a function month -> views; default 1000
        self.traffic = {}  # lang -> {month: views} or a function month -> views; default 10 000 000

    def entity(self, qid, labels=None, sitelinks=None, claims=None, descriptions=None):
        self.entities[qid] = {
            "type": "item", "id": qid,
            "labels": {k: {"language": k, "value": v} for k, v in (labels or {}).items()},
            "descriptions": {k: {"language": k, "value": v} for k, v in (descriptions or {}).items()},
            "sitelinks": {f"{k}wiki": {"site": f"{k}wiki", "title": t, "badges": []} for k, t in (sitelinks or {}).items()},
            "claims": {
                p: [{"mainsnak": {"snaktype": "value", "property": p,
                                  "datavalue": {"value": {"entity-type": "item", "id": v}, "type": "wikibase-entityid"}},
                     "type": "statement", "rank": "normal"} for v in values]
                for p, values in (claims or {}).items()
            },
        }

    def qid_of(self, lang, title):
        for qid, e in self.entities.items():
            if e["sitelinks"].get(f"{lang}wiki", {}).get("title") == title:
                return qid
        return None

    def _page(self, lang, title, index=None):
        page = {"pageid": abs(hash(title)) % 10**6, "ns": 0, "title": title}
        if index is not None:
            page["index"] = index
        qid = self.qid_of(lang, title)
        if qid:
            page["pageprops"] = {"wikibase_item": qid}
        return page

    def handle(self, request):
        p = request.params
        if request.host == "wikimedia.org":
            return self._aqs(request)
        if p.get("action") == "sitematrix":
            return reply(self.matrix)
        if request.host == "www.wikidata.org":
            if p["action"] == "wbgetentities":
                return reply({"entities": {q: self.entities.get(q, {"id": q, "missing": ""}) for q in p["ids"].split("|")}})
            if p["action"] == "wbsearchentities":
                found = self.wbsearch.get((p["language"], p["search"]), [])
                return reply({"search": [{"id": q, "label": l, "description": d} for q, l, d in found]})
            return None
        lang = request.host.split(".")[0]
        if p.get("meta") == "siteinfo":
            return reply({"query": {"general": {"mainpage": "Main Page"}}})
        if p.get("generator") == "search":
            query = p["gsrsearch"]
            if query.startswith("morelike:"):
                titles = self.morelike.get((lang, query[len("morelike:"):]), [])
            else:
                titles = self.search.get((lang, query), [])
            titles = titles[: int(p["gsrlimit"])]
            return reply({"query": {"pages": [self._page(lang, t, i + 1) for i, t in enumerate(titles)]}} if titles else {"batchcomplete": True})
        if p.get("prop") == "redirects":  # sitelink resolution: pages exist when some entity links to them
            pages = [{"pageid": 1, "ns": 0, "title": t} if self.qid_of(lang, t) else {"ns": 0, "title": t, "missing": True}
                     for t in request.titles()]
            return reply({"query": {"pages": pages}})
        if p.get("generator") == "links":
            titles = self.links.get((lang, p["titles"]), [])
            return reply({"query": {"pages": [self._page(lang, t) for t in titles]}})
        if p.get("prop") == "links" and "pltitles" in p:
            targets = set(p["pltitles"].split("|"))
            pages = []
            for title in request.titles():
                page = self._page(lang, title)
                hits = [t for t in self.links.get((lang, title), []) if t in targets]
                if hits:
                    page["links"] = [{"ns": 0, "title": t} for t in hits]
                pages.append(page)
            return reply({"query": {"pages": pages}})
        return None

    def _aqs(self, request):
        parts = [urllib.parse.unquote(x) for x in request.raw_path.split("/")]
        start, end = parts[-2], parts[-1]
        first, last = f"{start[:4]}-{start[4:6]}", f"{end[:4]}-{end[4:6]}"
        last = min(last, self.published)
        lang = parts[6].split(".")[0]
        if parts[5] == "aggregate":
            source, default = self.traffic.get(lang), 10_000_000
        else:
            source, default = self.views.get((lang, parts[9].replace("_", " "))), 1000
        span = months.span(first, last) if first <= last else []
        if source is None:
            values = {m: default for m in span}
        elif callable(source):
            values = {m: source(m) for m in span}
        else:
            values = {m: source[m] for m in span if m in source}
        return aqs_items(parts[6], values)

    def client(self, **kwargs):
        return client(self.handle, **kwargs)
