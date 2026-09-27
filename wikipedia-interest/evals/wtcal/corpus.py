"""The calibration corpus (calibration §2.4, level 0a): topics chosen at random from English
Vital articles level 4 with a fixed generator seed, plus task-like topics reported apart;
baskets frozen by `suggest` at recording time; full series from 2015-07 stored as JSON.

corpus.json:  {"seed", "langs", "fields", "per_field", "extra", "recorded", "topics": [
                 {"id", "qid", "label", "field", "items", "random": bool}]}
data/<id>.json.gz: {"end", "langs": {lang: {"first_month", "denominator", "articles": {qid: {"views", "redirect_views"}},
                                            "exclusions": {qid: {"code", "detail"}}}}}
"""
from __future__ import annotations

import gzip
import json
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from wikitrends import candidates, metrics, months, pageviews, sitematrix, topics, wikidata
from wikitrends import thresholds as T

ROOT = Path(__file__).resolve().parents[1] / "calibration"
CORPUS = ROOT / "corpus.json"
DATA = ROOT / "data"
VITAL = "Wikipedia:Vital articles/Level/4/{field}"
FIELDS = ("Arts", "Philosophy and religion", "Everyday life", "Society and social sciences",
          "Biology and health sciences", "Physical sciences", "Technology", "Mathematics")
LANGS = ("en", "es", "pl", "uk", "cs")  # 2 large, 1 mid, 2 smaller editions; the task's pl, cs, uk included
EXTRA = ("intermittent fasting", "astronomy", "English as a second or foreign language", "ketogenic diet")
FULL_START = T.EARLIEST_MONTH
WINDOW_FOR_FULL_HISTORY = months.add(T.EARLIEST_MONTH, T.HISTORY_BEFORE_WINDOW)  # history_start = 2015-07


def load(path: Path = CORPUS) -> Dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    ids = [t["id"] for t in data["topics"]]
    if len(set(ids)) != len(ids) or not all(t["qid"] in t["items"] for t in data["topics"]):
        raise ValueError("corpus.json: duplicate ids, or a seed outside its basket")
    return data


# --- selection and recording (network, Part B) ------------------------------------------------


def vital_titles(http: Any, field: str) -> List[str]:
    """Article links (namespace 0) of one Vital articles level-4 page, all continuation pages."""
    titles: List[str] = []
    params = {"action": "query", "format": "json", "redirects": "1", "prop": "links", "titles": VITAL.format(field=field),
              "plnamespace": "0", "pllimit": "max"}
    while True:
        reply = http.get("https://en.wikipedia.org/w/api.php?" + _query(params)).json()
        for page in reply.get("query", {}).get("pages", {}).values():
            titles += [link["title"] for link in page.get("links", [])]
        if "continue" not in reply:
            return sorted(set(titles))
        params.update(reply["continue"])


def _query(params: Dict[str, str]) -> str:
    import urllib.parse

    return urllib.parse.urlencode(params)


def select(http: Any, cache: Any, *, seed: int = 20260927, per_field: int = 2, langs: Sequence[str] = LANGS,
           fields: Sequence[str] = FIELDS, extra: Sequence[str] = EXTRA) -> List[Dict[str, Any]]:
    """Random topics per field whose seed has an article in every corpus language, then the extras."""
    rng = random.Random(seed)
    matrix = sitematrix.SiteMatrix.load(http, cache)
    sites = matrix.resolve_many(list(langs))
    en = matrix.resolve("en")
    chosen: List[Dict[str, Any]] = []
    for field in fields:
        titles = vital_titles(http, field)
        rng.shuffle(titles)
        taken = 0
        for title in titles:
            if taken == per_field:
                break
            found = topics.find(http, cache, en, title, "en")
            if found.leader is None:
                continue
            entity = wikidata.get_entities(http, cache, [found.leader], languages=["en"])[found.leader]
            if all(wikidata.title_in(entity, s.dbname) for s in sites) and found.leader not in {c["qid"] for c in chosen}:
                chosen.append({"qid": found.leader, "label": entity.label("en", ["en"]), "field": field, "random": True})
                taken += 1
    for query in extra:
        found = topics.find(http, cache, en, query, "en")
        leader = found.leader or (found.candidates[0].qid if found.candidates else None)
        if leader is not None:
            chosen.append({"qid": leader, "label": query, "field": "task-like", "random": False})
    return chosen


def freeze_baskets(http: Any, cache: Any, chosen: Sequence[Dict[str, Any]], langs: Sequence[str] = LANGS) -> List[Dict[str, Any]]:
    """The `suggest` draft of every topic, frozen: later changes of suggest never change the corpus."""
    matrix = sitematrix.SiteMatrix.load(http, cache)
    out = []
    for i, topic in enumerate(chosen):
        found = candidates.suggest(http, cache, matrix, [topic["qid"]], list(langs), "en", T.SUGGEST_LIMIT)
        items = list(dict.fromkeys([topic["qid"], *found.draft]))
        out.append({"id": f"t{i + 1:02d}", **topic, "items": items})
    return out


def record(http: Any, cache: Any, corpus: Dict[str, Any], end: str, out_dir: Path = DATA) -> List[str]:
    """Full series (2015-07 .. end) of every topic x language; returns the ids written.
    Existing files are kept, so an interrupted recording continues where it stopped."""
    out_dir.mkdir(parents=True, exist_ok=True)
    matrix = sitematrix.SiteMatrix.load(http, cache)
    sites = dict(zip(corpus["langs"], matrix.resolve_many(corpus["langs"])))
    written = []
    for topic in corpus["topics"]:
        path = out_dir / f"{topic['id']}.json.gz"
        if path.exists():
            continue
        entities = wikidata.get_entities(http, cache, topic["items"], languages=["en"])
        langs: Dict[str, Any] = {}
        for lang, site in sites.items():
            links = {q: t for q in topic["items"] if q in entities for t in [wikidata.title_in(entities[q], site.dbname)] if t}
            plan = pageviews.plan_lang(http, cache, lang, site, links, WINDOW_FOR_FULL_HISTORY, end)
            got = pageviews.fetch_lang(http, cache, plan)
            if got.status != "ok" or got.data is None:
                raise RuntimeError(f"{topic['id']} {lang}: {got.status} {got.error or ''}")
            langs[lang] = dump(got.data)
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump({"end": end, "langs": langs}, f)
        written.append(topic["id"])
    return written


# --- series (offline) --------------------------------------------------------------------------


def dump(data: metrics.LangData) -> Dict[str, Any]:
    return {"first_month": data.first_month, "denominator": data.denominator,
            "articles": {q: {"views": a.views, "redirect_views": a.redirect_views} for q, a in data.articles.items()},
            "exclusions": {q: {"code": e.code, "detail": e.detail} for q, e in data.exclusions.items()}}


def restore(lang: str, raw: Dict[str, Any], window_start: int = 0) -> metrics.LangData:
    return metrics.LangData(
        lang, raw["first_month"], window_start, list(raw["denominator"]),
        {q: metrics.Article(q, list(a["views"]), list(a["redirect_views"])) for q, a in raw["articles"].items()},
        {q: metrics.Exclusion(q, e["code"], lang, e.get("detail")) for q, e in raw.get("exclusions", {}).items()},
    )


def read(topic_id: str, data_dir: Path = DATA) -> Dict[str, Any]:
    with gzip.open(data_dir / f"{topic_id}.json.gz", "rt", encoding="utf-8") as f:
        return json.load(f)


def window(full: metrics.LangData, end: str, n_months: int = T.DEFAULT_PERIOD_MONTHS) -> Optional[metrics.LangData]:
    """The LangData analyze would load for a window of n_months ending at `end`: history of up to
    HISTORY_BEFORE_WINDOW months before its start. None when the full series does not cover it."""
    start = months.add(end, -(n_months - 1))
    first = pageviews.history_start(start)
    i0, i1 = months.diff(first, full.first_month), months.diff(end, full.first_month) + 1
    if i0 < 0 or i1 > len(full.denominator):
        return None
    return metrics.LangData(
        full.lang, first, months.diff(start, first), full.denominator[i0:i1],
        {q: metrics.Article(q, a.views[i0:i1], a.redirect_views[i0:i1]) for q, a in full.articles.items()},
        dict(full.exclusions),
    )
