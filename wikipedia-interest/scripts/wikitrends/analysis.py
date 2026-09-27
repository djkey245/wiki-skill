"""`analyze`: from collected language data to result.json (agent contract §1 p.5, §3).

`evaluate` is pure: LangData of every language in, the result dict out. `collect` is the
network part; `save_result` and `changes` handle the versioned results of a spec.
"""
from __future__ import annotations

import datetime
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import budget, compare, confidence, fs, metrics, months, pageviews, sitematrix, spec, templates, wikidata
from . import thresholds as T
from .confidence import Reason
from .errors import WikitrendsError, make_warning

RESULT_FILE = "result.json"
RESULTS_DIR = "results"


# --- evaluation (no network) ---------------------------------------------------------------


def _round(x: Optional[float], digits: int = 2) -> Optional[float]:
    return None if x is None else round(x, digits)


def _reason_dict(r: Reason, lang: str, labels: Dict[str, str]) -> Dict[str, Any]:
    return {"code": r.code, "severity": r.severity, "text": templates.reason_text(r.code, r.detail, lang, labels),
            "detail": r.detail}


def _failed_cell(lang: str, status: str) -> Dict[str, Any]:
    return {"lang": lang, "status": status, "group": "insufficient_data", "direction": None, "confidence": None,
            "reasons": [], "fmt": {}, "criteria": {}, "criteria_met": None, "size_big": None, "quadrant": None}


def _cell(data: metrics.LangData, baskets: metrics.Baskets, seeds: Sequence[str], criteria: Dict[str, Any],
          lang: str, labels: Dict[str, str], metric: str = "share",
          revised: Sequence[str] = ()) -> Tuple[Dict[str, Any], metrics.CellMetrics]:
    cell = metrics.compute_cell(data, baskets, metric)
    a = confidence.assess(data, baskets, cell, seeds, revised=revised)
    group = compare.group_of(a.direction, a.confidence, a.reasons)
    statuses, met = compare.criteria_status(criteria, group, cell.growth_pct_yr, cell.monthly_views)
    ci = None if cell.trend is None else [round(x, 2) for x in cell.trend.growth_ci]
    raw_ci = None if cell.raw_trend is None else [round(x, 2) for x in cell.raw_trend.growth_ci]
    window = slice(data.window_start, None)
    out = {
        "lang": data.lang, "status": "ok", "basket": cell.basket, "n_articles": len(cell.qids),
        "direction": a.direction, "confidence": a.confidence, "category": a.category, "group": group,
        "growth_pct_yr": _round(cell.growth_pct_yr), "growth_ci": ci,
        "p": None if cell.trend is None else round(cell.trend.p, 4),
        "raw_growth_pct_yr": _round(cell.raw_growth_pct_yr), "raw_growth_ci": raw_ci, "level_ppm": _round(cell.level_ppm),
        "monthly_views": round(cell.monthly_views), "primary_monthly_views": round(cell.primary_monthly_views),
        "recent_yoy_pct": _round(cell.recent_yoy_pct), "redirect_share": round(cell.redirect_share, 3),
        "project_trend_pct_yr": _round(cell.project_trend_pct_yr),
        "fmt": {
            "growth": templates.fmt_growth(cell.growth_pct_yr, lang), "ci": templates.fmt_ci(ci, lang),
            "raw_growth": templates.fmt_growth(cell.raw_growth_pct_yr, lang), "raw_ci": templates.fmt_ci(raw_ci, lang),
            "level_ppm": templates.fmt_ppm(cell.level_ppm, lang), "monthly_views": templates.fmt_views(cell.monthly_views, lang),
        },
        "reasons": [_reason_dict(r, lang, labels) for r in a.reasons],
        "seed_divergence": a.seed_divergence, "breaks": a.breaks,
        "criteria": statuses, "criteria_met": met, "size_big": None, "quadrant": None,
        "contributors": [{"qid": c.qid, "label": labels.get(c.qid, c.qid), "share": round(c.share, 3)} for c in cell.contributors[:5]],
        "series": {
            "index": [_round(v) for v in cell.index],
            "share_ppm": [_round(v, 3) for v in cell.share[window]],
            "views": cell.views[window],
        },
    }
    return out, cell


def _pairs(series: Dict[str, List[Optional[float]]], cells: Dict[str, Dict[str, Any]], names: Dict[str, str],
           ranking: List[str], first_month: str, window_start: int, lang: str, metric: str = "share") -> List[Dict[str, Any]]:
    neighbours = set(compare.neighbours(ranking))
    out = []
    for p in compare.pairwise(series, first_month, window_start):
        ci = [round(p.ci[0], 2), round(p.ci[1], 2)]
        item = {"a": p.a, "b": p.b, "diff_pct_yr": round(p.diff_pct_yr, 2), "ci": ci, "p": round(p.p, 4),
                "p_adj": round(p.p_adj, 4), "significant": p.significant,
                "fmt": {"diff": templates.fmt_growth(p.diff_pct_yr, lang), "ci": templates.fmt_ci(ci, lang)}}
        if not p.significant:
            item["reason"] = "difference_not_significant"
        item["shown"] = p.significant or (p.a, p.b) in neighbours or (p.b, p.a) in neighbours
        item["text"] = templates.pair_text(item, cells[p.a], cells[p.b], names[p.a], names[p.b], lang, metric)
        out.append(item)
    return out


def _family(cells: Dict[str, Dict[str, Any]], series: Dict[str, List[Optional[float]]], names: Dict[str, str],
            criteria: Dict[str, Any], first_month: str, window_start: int, lang: str, what: str,
            compare_pairs: bool = True, metric: str = "share") -> Dict[str, Any]:
    """Matrix, recommendations and pairs of one family: a topic's languages or a language's topics."""
    ok = {k: c for k, c in cells.items() if c["status"] == "ok"}
    flags = compare.size_flags({k: float(c["monthly_views"]) for k, c in ok.items()})
    quadrants = {k: compare.quadrant(c["group"], flags[k]) for k, c in ok.items()}
    items = [compare.Item(k, c["group"], quadrants[k], c["criteria"], c["growth_pct_yr"]) for k, c in ok.items()]
    items += [compare.Item(k, c["group"], None, {}, None) for k, c in cells.items() if k not in ok]
    ranking = [i.key for i in compare.order(items)]
    rec = compare.recommend(items)
    keys = {"explore": [i.key for i in rec.explore], "postpone": [i.key for i in rec.postpone], "rest": [i.key for i in rec.rest]}
    return {
        "size_big": flags, "quadrants": quadrants, "matrix": any(v is not None for v in flags.values()),
        "ranking": ranking,
        "pairs": _pairs({k: series[k] for k in ranking if k in series}, cells, names, [k for k in ranking if k in series],
                        first_month, window_start, lang, metric) if compare_pairs and len(series) > 1 else [],
        "recommendations": (templates.recommendation_texts(keys, cells, names, criteria, lang, what, metric)
                            if len(cells) > 1 else None),
    }


def evaluate(
    spec_data: Dict[str, Any], window: Tuple[str, str], data: Dict[str, metrics.LangData],
    failed: Dict[str, str], labels: Dict[str, str], revised: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, Any]:
    """The result dict without paths, answer_md, warnings and changes (added by the caller).

    Trend, checks, groups and pairwise tests use the main metric (options.metric); `revised`
    maps a language to the historical months Wikimedia recalculated (data_revised)."""
    lang = templates.text_lang(spec_data["display_lang"])
    metric = spec_data["options"].get("metric", "share")
    revised = revised or {}
    criteria = dict(spec_data["options"].get("criteria") or {})
    criteria.pop("size_axis", None)  # slice 2
    start, end = window
    langs = spec_data["langs"]
    ok_langs = [l for l in langs if l in data]
    first_month = pageviews.history_start(start)
    window_start = months.diff(start, first_month)
    topics = []
    by_lang_cells: Dict[str, Dict[str, Dict[str, Any]]] = {l: {} for l in langs}
    by_lang_series: Dict[str, Dict[str, List[Optional[float]]]] = {l: {} for l in langs}
    for topic in spec_data["topics"]:
        qids, seeds = topic["items"], topic["seeds"]
        baskets = metrics.build_baskets(qids, seeds, {l: data[l] for l in ok_langs}) if ok_langs else {}
        cells: Dict[str, Dict[str, Any]] = {}
        series: Dict[str, List[Optional[float]]] = {}
        for l in langs:
            if l not in data:
                cells[l] = _failed_cell(l, "failed")
            elif not baskets[l].primary:
                cells[l] = _failed_cell(l, "no_articles")
            else:
                cells[l], cell = _cell(data[l], baskets[l], seeds, criteria, lang, labels, metric, revised.get(l, ()))
                if cell.trend is not None:
                    series[l] = cell.values
                    by_lang_series[l][topic["id"]] = cell.values
            by_lang_cells[l][topic["id"]] = cells[l]
        common = next(iter(baskets.values())).common if baskets else []
        family = _family(cells, series, {l: l for l in langs}, criteria, first_month, window_start, lang, "langs",
                         compare_pairs=bool(common), metric=metric)
        for l, c in cells.items():
            c["size_big"] = family["size_big"].get(l)
            c["quadrant"] = family["quadrants"].get(l)
        excluded = []
        seen = set()
        for l in ok_langs:
            for e in baskets[l].excluded:
                if (e.qid, e.lang) not in seen:
                    seen.add((e.qid, e.lang))
                    excluded.append({"qid": e.qid, "label": labels.get(e.qid, e.qid), "lang": e.lang, "code": e.code,
                                     **({"detail": e.detail} if e.detail else {})})
        topic_out = {
            "id": topic["id"], "label": topic["label"],
            "seeds": [{"qid": q, "label": labels.get(q, q)} for q in seeds],
            "items": [{"qid": q, "label": labels.get(q, q)} for q in qids],
            "common": common, "excluded": excluded,
            "cells": [cells[l] for l in langs],
            "matrix": family["matrix"], "ranking": family["ranking"], "pairs": family["pairs"],
            "pairs_skipped": None if common else "no_common_basket",
            "recommendations": family["recommendations"],
        }
        topic_out["headline"] = templates.headline(topic["label"], [c for c in topic_out["cells"]], lang)
        topics.append(topic_out)

    by_lang = []
    if len(spec_data["topics"]) > 1:
        names = {t["id"]: f"«{t['label']}»" if lang == "uk" else f"“{t['label']}”" for t in spec_data["topics"]}
        for l in ok_langs:
            family = _family(by_lang_cells[l], by_lang_series[l], names, criteria, first_month, window_start, lang, "topics",
                             metric=metric)
            by_lang.append({"lang": l, "ranking": family["ranking"], "matrix": family["matrix"],
                            "quadrants": family["quadrants"], "pairs": family["pairs"],
                            "recommendations": family["recommendations"]})
    return {
        "display_lang": spec_data["display_lang"], "text_lang": lang,
        "period": {"start": start, "end": end, "months": months.span(start, end),
                   **({"relative": spec_data["period"]["relative"]} if spec_data["period"].get("relative") else {})},
        "metric": metric, "criteria": criteria or None,
        "langs": [{"lang": l, "status": "ok" if l in data else "failed", **({"error": failed[l]} if l in failed else {})}
                  for l in langs],
        "topics": topics, "by_lang": by_lang,
    }


def analysed_cells(result: Dict[str, Any]) -> int:
    return sum(1 for t in result["topics"] for c in t["cells"] if c["status"] == "ok")


# --- stdout ------------------------------------------------------------------------------------


def compact(result: Dict[str, Any]) -> Dict[str, Any]:
    """The stdout view: headlines, key numbers and reasons; details stay in result.json."""
    many = sum(len(t["cells"]) for t in result["topics"]) > T.MAX_DETAILED_CELLS
    topics = []
    for t in result["topics"]:
        cells = []
        for c in t["cells"]:
            if many and not templates.is_key_cell(c):
                continue
            limit = None if templates.keeps_everything(c) else T.MAX_REASONS_SHOWN
            reasons = [{k: r[k] for k in ("code", "severity", "text")} for r in c["reasons"]]
            cells.append({"lang": c["lang"], "status": c["status"], "group": c["group"], "confidence": c["confidence"],
                          "growth": c["fmt"].get("growth"), "ci": c["fmt"].get("ci"),
                          "criteria_met": c["criteria_met"], "reasons": reasons[:limit] if limit else reasons})
        entry = {"id": t["id"], "label": t["label"], "headline": t["headline"], "cells": cells}
        if many:
            counts: Dict[str, int] = {}
            for c in t["cells"]:
                counts[c["group"]] = counts.get(c["group"], 0) + 1
            entry["groups"] = counts
        topics.append(entry)
    return {"text_lang": result["text_lang"], "period": {k: result["period"][k] for k in ("start", "end")},
            "topics": topics}


# --- versioned results and changes -----------------------------------------------------------


def load_previous(spec_path: Path) -> Optional[Dict[str, Any]]:
    path = Path(spec_path).parent / RESULT_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_result(spec_path: Path, result: Dict[str, Any], now: Optional[datetime.datetime] = None) -> Path:
    """results/<timestamp>.json plus a copy in result.json, both atomic (agent contract §1 p.5)."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    directory = Path(spec_path).parent / RESULTS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    path, n = directory / f"{stamp}.json", 2
    while path.exists():
        path, n = directory / f"{stamp}-{n}.json", n + 1
    result["result"] = str(path.resolve())
    fs.atomic_write_json(path, result)
    fs.atomic_write_json(Path(spec_path).parent / RESULT_FILE, result)
    return path.resolve()


def changes(previous: Optional[Dict[str, Any]], current: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Differences from the previous result of the same spec: period, languages, basket, group,
    direction, confidence; a common basket narrowed by an added language is named explicitly."""
    if previous is None:
        return []
    out: List[Dict[str, Any]] = []
    old_p, new_p = previous["period"], current["period"]
    if (old_p["start"], old_p["end"]) != (new_p["start"], new_p["end"]):
        out.append({"kind": "period", "from": f"{old_p['start']}..{old_p['end']}", "to": f"{new_p['start']}..{new_p['end']}"})
    old_langs = [x["lang"] for x in previous["langs"]]
    new_langs = [x["lang"] for x in current["langs"]]
    added = [l for l in new_langs if l not in old_langs]
    removed = [l for l in old_langs if l not in new_langs]
    if added or removed:
        out.append({"kind": "langs", "added": added, "removed": removed})
    old_topics = {t["id"]: t for t in previous["topics"]}
    for topic in current["topics"]:
        old = old_topics.get(topic["id"])
        if old is None:
            out.append({"kind": "topic_added", "topic": topic["id"]})
            continue
        old_items = [i["qid"] for i in old["items"]]
        new_items = [i["qid"] for i in topic["items"]]
        if old_items != new_items:
            out.append({"kind": "basket", "topic": topic["id"],
                        "added": [q for q in new_items if q not in old_items],
                        "removed": [q for q in old_items if q not in new_items]})
        lost = [q for q in old["common"] if q not in topic["common"] and q in new_items]
        if lost:
            labels = {i["qid"]: i["label"] for i in topic["items"]}
            out.append({"kind": "common_narrowed", "topic": topic["id"], "because_langs": added,
                        "removed": [{"qid": q, "label": labels.get(q, q)} for q in lost],
                        "note": "trends of the other languages may change without any change in their data"})
        old_cells = {c["lang"]: c for c in old["cells"]}
        for cell in topic["cells"]:
            prev = old_cells.get(cell["lang"])
            if prev is None:
                continue
            for key in ("group", "direction", "confidence"):
                if prev.get(key) != cell.get(key):
                    out.append({"kind": key, "topic": topic["id"], "lang": cell["lang"], "from": prev.get(key), "to": cell.get(key)})
    for topic_id in old_topics:
        if topic_id not in {t["id"] for t in current["topics"]}:
            out.append({"kind": "topic_removed", "topic": topic_id})
    return out


# --- collection (network) ----------------------------------------------------------------------


class ReadBypass:
    """--no-cache: every read misses, every write still lands in the cache (data §5)."""

    def __init__(self, cache: Any) -> None:
        self._cache = cache

    def get_views(self, project: str, title: str, agent: str, wanted: Sequence[str]) -> Tuple[Dict[str, int], List[str]]:
        return {}, list(wanted)

    def get_meta(self, kind: str, key: str) -> None:
        return None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cache, name)


def latest_month(http: Any, cache: Any, sites: Sequence[sitematrix.Site], today: str) -> str:
    mains = {site.project: sitematrix.main_page(http, cache, site) for site in sites}
    latest = pageviews.latest_month(http, mains, today)
    if latest is None:
        raise WikitrendsError("no_data", "No month is published for every language yet", hint="Repeat later")
    return latest


def fix_window(spec_data: Dict[str, Any], latest: str) -> Tuple[Tuple[str, str], bool]:
    """(start, end) of the window and whether the spec changed: the first analyze writes
    missing start/end into the spec, so later runs reproduce the same analysis (agent contract §2)."""
    period = spec_data["period"]
    start, end = spec.resolve_window(period, latest)
    changed = period.get("start") != start or period.get("end") != end
    period["start"], period["end"] = start, end
    return (start, end), changed


@dataclass
class Collected:
    data: Dict[str, metrics.LangData] = field(default_factory=dict)
    failed: Dict[str, str] = field(default_factory=dict)
    partial: List[str] = field(default_factory=list)  # languages the deadline cut
    revised: Dict[str, List[str]] = field(default_factory=dict)
    warnings: List[Dict[str, Any]] = field(default_factory=list)


def entity_labels(entities: Dict[str, wikidata.Entity], display_lang: str, langs: Sequence[str]) -> Dict[str, str]:
    return {q: e.label(display_lang, langs) for q, e in entities.items()}


def collect(http: Any, cache: Any, spec_data: Dict[str, Any], sites: Dict[str, sitematrix.Site],
            entities: Dict[str, wikidata.Entity], window: Tuple[str, str], *, allow_large: bool) -> Collected:
    """plan_lang for every language, one budget check, then fetch_lang (data §2, §4)."""
    start, end = window
    max_redirects = spec_data["options"].get("max_redirects", T.MAX_REDIRECTS)
    qids = list(dict.fromkeys(q for t in spec_data["topics"] for q in t["items"]))
    plans = {}
    for lang in spec_data["langs"]:
        site = sites[lang]
        sitelinks = {}
        for q in qids:
            title = wikidata.title_in(entities[q], site.dbname) if q in entities else None
            if title:
                sitelinks[q] = title
        plans[lang] = pageviews.plan_lang(http, cache, lang, site, sitelinks, start, end)
    estimate = sum(pageviews.estimate_requests(cache, p, max_redirects) for p in plans.values())
    budget.check(estimate, limit=T.MAX_REQUESTS, allow_large=allow_large)
    out = Collected()
    for lang, plan in plans.items():
        got = pageviews.fetch_lang(http, cache, plan, max_redirects)
        if got.revised_months:
            out.revised[lang] = got.revised_months
            out.warnings.append(make_warning("data_revised", f"{lang}: historical months were recalculated by Wikimedia",
                                             lang=lang, months=got.revised_months))
        if got.status == "partial":
            out.partial.append(lang)
        elif got.status == "failed":
            out.failed[lang] = got.error or "failed"
            out.warnings.append(make_warning("fetch_failed", f"{lang}: project traffic could not be loaded", lang=lang))
        else:
            assert got.data is not None
            out.data[lang] = got.data
            failed_qids = [q for q, e in got.data.exclusions.items() if e.code == "fetch_failed"]
            if failed_qids:
                out.warnings.append(make_warning("fetch_failed", f"{lang}: some articles could not be loaded",
                                                 lang=lang, qids=failed_qids))
    return out

