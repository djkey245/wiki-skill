"""`find`: topic search with the "clear leader" rule (agent contract §1 p.2, calibration R17).

CirrusSearch returns no relevance score through generator=search, so the leader rule relies on
titles: the first hit is a clear leader when its title (or its label in the query language)
equals the query and no other hit is the same name with a qualifier ("Python (film)") or a
disambiguation page. A query that matches nothing exactly is ambiguous unless only one
candidate is found.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, List, Optional

from . import search, wikidata
from . import thresholds as T
from .sitematrix import Site


@dataclass(frozen=True)
class Candidate:
    qid: str
    label: str
    description: str
    title: Optional[str]  # article in the query language; None for wbsearchentities hits


@dataclass(frozen=True)
class FindResult:
    candidates: List[Candidate]
    leader: Optional[str]  # QID of the clear leader, or None: ambiguous_topic
    source: str  # "search" | "wbsearch"


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold().replace("_", " ")
    return re.sub(r"\s+", " ", text).strip()


def base_name(title: str) -> str:
    """'Python (programming language)' -> 'python'."""
    return normalize(re.sub(r"\s*\([^)]*\)\s*$", "", title))


def find(client: Any, cache: Any, site: Site, query: str, lang: str) -> FindResult:
    hits = [h for h in search.search_pages(client, cache, site, query) if h.qid]
    source = "search"
    if not hits:
        found = search.wbsearch(client, cache, query, lang, T.FIND_CANDIDATES)
        candidates = [Candidate(h.qid, h.label or h.qid, h.description, None) for h in found]
        return FindResult(candidates, _wbsearch_leader(query, candidates), "wbsearch")

    qids = list(dict.fromkeys(h.qid for h in hits))[: T.FIND_CANDIDATES * 2]
    entities = wikidata.get_entities(client, cache, qids, languages=[lang], claims=True)
    title_of = {}
    for h in hits:
        title_of.setdefault(h.qid, h.title)
    q = normalize(query)
    disambiguation_with_name = False
    candidates: List[Candidate] = []
    for qid in qids:
        entity = entities[qid]
        if entity.missing:
            continue
        if T.DISAMBIGUATION in entity.claims.get("P31", []):
            disambiguation_with_name |= base_name(title_of[qid]) == q
            continue
        label = entity.label(lang)
        candidates.append(Candidate(qid, label, entity.descriptions.get(lang) or entity.descriptions.get("en", ""), title_of[qid]))
    candidates = candidates[: T.FIND_CANDIDATES]
    return FindResult(candidates, _search_leader(q, candidates, disambiguation_with_name), source)


def _search_leader(q: str, candidates: List[Candidate], disambiguation_with_name: bool) -> Optional[str]:
    if not candidates or disambiguation_with_name:
        return None
    if len(candidates) == 1:
        return candidates[0].qid
    first, rest = candidates[0], candidates[1:]
    exact = normalize(first.title or "") == q or normalize(first.label) == q
    rival = any(base_name(c.title or c.label) == q for c in rest)
    return first.qid if exact and not rival else None


def _wbsearch_leader(query: str, candidates: List[Candidate]) -> Optional[str]:
    if len(candidates) == 1:
        return candidates[0].qid
    q = normalize(query)
    exact = [c for c in candidates if normalize(c.label) == q]
    return exact[0].qid if len(exact) == 1 and candidates[0] is exact[0] else None
