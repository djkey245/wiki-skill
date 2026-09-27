"""Wikidata entities: sitelinks, labels with fallback, descriptions, entity-valued claims (data §2)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import thresholds as T
from .errors import WikitrendsError

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
CLAIM_PROPERTIES = ("P31", "P279", "P361")


@dataclass(frozen=True)
class Entity:
    qid: str  # as requested
    canonical: str  # target QID if the requested one is a redirect
    missing: bool = False
    labels: Dict[str, str] = field(default_factory=dict)
    descriptions: Dict[str, str] = field(default_factory=dict)
    sitelinks: Dict[str, str] = field(default_factory=dict)  # dbname -> title
    claims: Dict[str, List[str]] = field(default_factory=dict)  # P31/P279/P361 -> QIDs

    def label(self, display_lang: str, fallback_langs: Sequence[str] = ()) -> str:
        """display_lang -> en -> the other requested languages -> the QID (data §2, testing §4)."""
        for lang in [display_lang, "en", *fallback_langs, *sorted(self.labels)]:
            if lang in self.labels:
                return self.labels[lang]
        return self.qid


def _values(block: Dict[str, Any]) -> Dict[str, str]:
    return {lang: item["value"] for lang, item in block.items()}


def _claims(block: Dict[str, Any]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for prop in CLAIM_PROPERTIES:
        ids = []
        for statement in block.get(prop, []):
            snak = statement.get("mainsnak", {})
            if statement.get("rank") == "deprecated" or snak.get("snaktype") != "value":
                continue
            value = snak.get("datavalue", {}).get("value", {})
            if isinstance(value, dict) and "id" in value:
                ids.append(value["id"])
        if ids:
            out[prop] = ids
    return out


def parse_entities(data: Dict[str, Any], requested: Sequence[str]) -> Dict[str, Entity]:
    entities = data.get("entities", {})
    by_origin: Dict[str, Dict[str, Any]] = {}
    for key, value in entities.items():
        by_origin[key] = value
        origin = value.get("redirects", {}).get("from")
        if origin:
            by_origin[origin] = value
    out: Dict[str, Entity] = {}
    for qid in requested:
        value = by_origin.get(qid)
        if value is None or "missing" in value:
            out[qid] = Entity(qid, qid, missing=True)
            continue
        out[qid] = Entity(
            qid=qid,
            canonical=value.get("id", qid),
            labels=_values(value.get("labels", {})),
            descriptions=_values(value.get("descriptions", {})),
            sitelinks={site: item["title"] for site, item in value.get("sitelinks", {}).items()},
            claims=_claims(value.get("claims", {})),
        )
    return out


def get_entities(
    client: Any, cache: Any, qids: Sequence[str], *, languages: Sequence[str], claims: bool = False
) -> Dict[str, Entity]:
    """Batches of ACTION_API_BATCH ids; each entity is cached for the metadata TTL."""
    langs = "|".join(sorted(set(languages) | {"en"}))
    props = "labels|descriptions|sitelinks" + ("|claims" if claims else "")
    kind = f"entity:{props}:{langs}"
    out: Dict[str, Entity] = {}
    todo: List[str] = []
    for qid in dict.fromkeys(qids):
        cached = cache.get_meta(kind, qid)
        if cached is None:
            todo.append(qid)
        else:
            out[qid] = Entity(**cached)
    for i in range(0, len(todo), T.ACTION_API_BATCH):
        batch = todo[i : i + T.ACTION_API_BATCH]
        data, invalid = _fetch_batch(client, batch, props, langs)
        for qid in invalid:
            out[qid] = Entity(qid, qid, missing=True)  # not cached: an id that does not exist yet may appear
        for qid, entity in parse_entities(data, [q for q in batch if q not in invalid]).items():
            out[qid] = entity
            cache.put_meta(kind, qid, entity.__dict__)
    return out


def _fetch_batch(client: Any, batch: Sequence[str], props: str, langs: str) -> Tuple[Dict[str, Any], List[str]]:
    """wbgetentities for a batch. A malformed or never-issued id (QX, Q999999999999) fails the whole
    batch with `no-such-entity` naming that id (a deleted item is `missing`), so it is dropped and
    the batch repeated. Returns (data, dropped ids)."""
    ids = list(batch)
    invalid: List[str] = []
    while ids:
        data = client.get_action(
            WIKIDATA_API, {"action": "wbgetentities", "ids": "|".join(ids), "props": props, "languages": langs},
            maxlag=False, allowed_errors=("no-such-entity",),
        )
        error = data.get("error")
        if error is None:
            return data, invalid
        bad = error.get("id")
        if error.get("code") != "no-such-entity" or bad not in ids:
            raise WikitrendsError("network_error", f"wbgetentities: {error.get('code')}: {error.get('info', '')}",
                                  hint="Repeat the same command once later")
        ids.remove(bad)
        invalid.append(bad)
    return {"entities": {}}, invalid


def title_in(entity: Entity, dbname: str) -> Optional[str]:
    return None if entity.missing else entity.sitelinks.get(dbname)
