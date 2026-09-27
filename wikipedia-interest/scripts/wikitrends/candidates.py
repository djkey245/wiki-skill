"""Basket candidates for `suggest` (data §3, calibration R9, R10).

Sources: `morelike:` and outgoing links of every seed, in every spec language and in `en`.
The cheap score (in how many languages a source found the candidate) picks the top
CANDIDATES_FOR_METADATA; only they get Wikidata claims (P31 filter, relation to a seed) and a
backlink check, so the request count does not grow with the number of links.
Slice 1 has no generality penalty (slice 3).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set

from . import search, wikidata
from . import thresholds as T
from .sitematrix import Site, SiteMatrix

RELATION_PROPERTIES = ("P31", "P279", "P361")


@dataclass
class Candidate:
    qid: str
    label: str = ""
    morelike: Set[str] = field(default_factory=set)  # languages where morelike found it
    links: Set[str] = field(default_factory=set)  # languages where a seed links to it
    backlinks: Set[str] = field(default_factory=set)  # languages where it links back to a seed
    wikidata: bool = False  # P31 / P279 / P361 points to a seed
    coverage: Dict[str, Optional[str]] = field(default_factory=dict)  # spec language -> article title

    @property
    def cheap_score(self) -> float:
        w = T.CANDIDATE_WEIGHTS
        return w["morelike"] * len(self.morelike) + w["link"] * len(self.links)

    @property
    def score(self) -> float:
        w = T.CANDIDATE_WEIGHTS
        return self.cheap_score + w["backlink"] * len(self.backlinks) + w["wikidata"] * self.wikidata

    def in_all(self, langs: Sequence[str]) -> bool:
        return all(self.coverage.get(lang) for lang in langs)


@dataclass
class Suggestion:
    seeds: List[Candidate]  # coverage of the seeds; a seed missing in a language is a gap, not an error
    ranked: List[Candidate]
    draft: List[str]  # seeds + up to DRAFT_CANDIDATES best candidates present in every spec language
    excluded_types: Dict[str, int]  # P31 type label -> how many candidates it removed


def source_languages(langs: Sequence[str]) -> List[str]:
    return list(dict.fromkeys([*langs, "en"]))


def estimate_requests(n_seeds: int, n_langs: int) -> int:
    """Upper bound of suggest requests (cache ignored): sitematrix, seed entities, morelike and links
    per seed and source language, candidate entities, backlink checks."""
    batches = -(-T.CANDIDATES_FOR_METADATA // T.ACTION_API_BATCH)
    sources = n_langs + 1
    return 1 + -(-n_seeds // T.ACTION_API_BATCH) + sources * n_seeds * (1 + T.MAX_LINK_PAGES) + batches + sources * batches


def suggest(
    client: Any,
    cache: Any,
    matrix: SiteMatrix,
    seeds: Sequence[str],
    langs: Sequence[str],
    display_lang: str,
    limit: int = T.SUGGEST_LIMIT,
) -> Suggestion:
    label_langs = list(dict.fromkeys([display_lang, *langs]))
    sites = {lang: matrix.resolve(lang) for lang in source_languages(langs)}
    seed_entities = wikidata.get_entities(client, cache, seeds, languages=label_langs)
    found: Dict[str, Candidate] = {}

    seed_titles: Dict[str, List[str]] = {}
    for lang, site in sites.items():
        titles = [t for t in (wikidata.title_in(seed_entities[s], site.dbname) for s in seeds) if t]
        seed_titles[lang] = titles
        for title in titles:
            for hit in search.morelike(client, cache, site, title):
                if hit.qid and hit.qid not in seeds:
                    found.setdefault(hit.qid, Candidate(hit.qid)).morelike.add(lang)
            for hit in search.outgoing_links(client, cache, site, title):
                if hit.qid and hit.qid not in seeds:
                    found.setdefault(hit.qid, Candidate(hit.qid)).links.add(lang)

    top = sorted(found.values(), key=lambda c: (-c.cheap_score, c.qid))[: T.CANDIDATES_FOR_METADATA]
    entities = wikidata.get_entities(client, cache, [c.qid for c in top], languages=label_langs, claims=True)
    excluded: Dict[str, int] = {}
    kept: List[Candidate] = []
    for cand in top:
        entity = entities[cand.qid]
        if entity.missing:
            continue
        types = [T.EXCLUDED_TYPES[t] for t in entity.claims.get("P31", []) if t in T.EXCLUDED_TYPES]
        if types:
            excluded[types[0]] = excluded.get(types[0], 0) + 1
            continue
        cand.label = entity.label(display_lang, langs)
        cand.wikidata = any(v in seeds for p in RELATION_PROPERTIES for v in entity.claims.get(p, []))
        cand.coverage = {lang: wikidata.title_in(entity, sites[lang].dbname) for lang in langs}
        kept.append(cand)

    for lang, site in sites.items():
        titles = {wikidata.title_in(entities[c.qid], site.dbname): c for c in kept}
        titles.pop(None, None)
        if seed_titles[lang] and titles:
            for title in linking_to(client, site, list(titles), seed_titles[lang]):
                titles[title].backlinks.add(lang)

    ranked = sorted(kept, key=lambda c: (-c.score, -sum(1 for t in c.coverage.values() if t), c.qid))
    seed_rows = [
        Candidate(s, label=seed_entities[s].label(display_lang, langs),
                  coverage={lang: wikidata.title_in(seed_entities[s], sites[lang].dbname) for lang in langs})
        for s in seeds
    ]
    draft = [*seeds, *[c.qid for c in ranked if c.in_all(langs)][: T.DRAFT_CANDIDATES]]
    return Suggestion(seed_rows, ranked[:limit], draft, excluded)


def linking_to(client: Any, site: Site, titles: Sequence[str], targets: Sequence[str]) -> List[str]:
    """Which of `titles` link to any of `targets`: prop=links&pltitles, ACTION_API_BATCH titles a request."""
    out: List[str] = []
    for i in range(0, len(titles), T.ACTION_API_BATCH):
        params = {
            "action": "query", "titles": "|".join(titles[i : i + T.ACTION_API_BATCH]), "prop": "links",
            "pltitles": "|".join(targets), "plnamespace": "0", "pllimit": "max",
        }
        for page in search.query_pages(client, site.api, params, max_requests=2):
            if page.get("links") and page["title"] not in out:
                out.append(page["title"])
    return out
