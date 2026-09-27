"""Spec file: schema, validation, period rules, slug, edit operations (agent contract §2).

The agent never edits spec.json by hand: every change goes through these functions and
the file is written atomically (fs.atomic_write_json).
"""
from __future__ import annotations

import copy
import datetime
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import fs, months
from . import thresholds as T
from .errors import WikitrendsError, make_warning

SPEC_FILE = "spec.json"
VERSION = 1
METRICS = ("share", "views")
SIZE_AXES = ("monthly_views", "level_ppm")
CRITERIA_FIELDS = ("min_monthly_views", "min_growth_pct_yr", "size_axis")
_QID = re.compile(r"^Q[1-9][0-9]*$")
_RELATIVE = re.compile(r"^([1-9][0-9]*)m$")


def _invalid(message: str, hint: str = "Change the spec with 'spec edit'; never edit the JSON by hand") -> WikitrendsError:
    return WikitrendsError("invalid_arguments", message, hint=hint)


# --- slug ------------------------------------------------------------------------------

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e", "є": "ye", "ж": "zh", "з": "z",
    "и": "y", "і": "i", "ї": "yi", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p",
    "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh",
    "щ": "shch", "ь": "", "ю": "yu", "я": "ya", "ё": "yo", "ы": "y", "э": "e", "ъ": "", "ў": "u",
    "ђ": "dj", "ј": "j", "љ": "lj", "њ": "nj", "ћ": "c", "џ": "dz", "ѓ": "g", "ќ": "k", "ѕ": "dz",
    "ł": "l", "ø": "o", "ß": "ss", "æ": "ae", "œ": "oe", "đ": "d", "þ": "th", "ı": "i",
    "'": "", "’": "", "ʼ": "",
}


def slugify(text: str, fallback: str = "", max_length: int = T.SLUG_MAX_LENGTH) -> str:
    """Transliterate to [a-z0-9-]; empty result -> the lowercased fallback (the first seed QID)."""
    out = []
    for char in text.lower():
        char = _TRANSLIT.get(char, char)
        decomposed = unicodedata.normalize("NFKD", char)
        out.append("".join(c for c in decomposed if not unicodedata.combining(c)))
    slug = re.sub(r"[^a-z0-9]+", "-", "".join(out)).strip("-")
    slug = slug[:max_length].rstrip("-")
    return slug or fallback.lower()


def unique(base: str, taken: Sequence[str]) -> str:
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


# --- period ----------------------------------------------------------------------------


def parse_relative(text: str) -> int:
    match = _RELATIVE.match(text)
    if not match:
        raise _invalid(f"invalid period {text!r}", hint="Use months, e.g. --period 24m")
    n = int(match.group(1))
    if n < T.MIN_PERIOD_MONTHS:
        raise WikitrendsError(
            "period_too_short",
            f"{n} months is shorter than {T.MIN_PERIOD_MONTHS}",
            hint="The skill analyses monthly trends over at least 12 months; daily data is out of scope",
        )
    return n


def check_explicit(start: str, end: Optional[str], warnings: List[dict]) -> Tuple[str, Optional[str]]:
    """Validate --start/--end; a start before EARLIEST_MONTH is clipped with start_clipped."""
    months.parse(start)
    if end is not None:
        months.parse(end)
    if months.diff(start, T.EARLIEST_MONTH) < 0:
        warnings.append(make_warning(
            "start_clipped", f"Pageview data starts in {T.EARLIEST_MONTH}; the window starts there",
            requested=start, start=T.EARLIEST_MONTH,
        ))
        start = T.EARLIEST_MONTH
    if end is not None and months.diff(end, start) + 1 < T.MIN_PERIOD_MONTHS:
        raise WikitrendsError(
            "period_too_short",
            f"{start}..{end} is shorter than {T.MIN_PERIOD_MONTHS} months",
            hint="The skill analyses monthly trends over at least 12 months",
        )
    return start, end


def resolve_window(period: Dict[str, Any], latest: str) -> Tuple[str, str]:
    """(start, end) of the analysis window; `latest` is the newest available month (data §1).

    Explicit dates win over `relative`. Without `end` the window ends at `latest`.
    """
    end = period.get("end") or latest
    if months.diff(end, latest) > 0:
        raise WikitrendsError(
            "end_not_available", f"{end} is not published yet", hint=f"Use --end {latest} or earlier", latest=latest
        )
    if period.get("start"):
        start = period["start"]
    else:
        start = months.add(end, -(parse_relative(period["relative"]) - 1))
    if months.diff(end, start) + 1 < T.MIN_PERIOD_MONTHS:
        raise WikitrendsError("period_too_short", f"{start}..{end} is shorter than {T.MIN_PERIOD_MONTHS} months",
                              hint="Choose an earlier --start")
    return start, end


# --- schema ----------------------------------------------------------------------------


def _check_qids(values: Any, where: str) -> List[str]:
    if not isinstance(values, list) or not all(isinstance(q, str) and _QID.match(q) for q in values):
        raise _invalid(f"{where}: expected a list of QIDs like Q42")
    if len(set(values)) != len(values):
        raise _invalid(f"{where}: duplicate QIDs")
    return values


def validate(data: Any) -> Dict[str, Any]:
    """Check a spec and fill defaults; raises invalid_arguments / limits_exceeded."""
    if not isinstance(data, dict) or data.get("version") != VERSION:
        raise _invalid(f"not a spec of version {VERSION}")
    spec = copy.deepcopy(data)
    if not isinstance(spec.get("display_lang"), str) or not spec["display_lang"]:
        raise _invalid("display_lang is required")
    langs = spec.get("langs")
    if not isinstance(langs, list) or not langs or not all(isinstance(x, str) and x for x in langs) or len(set(langs)) != len(langs):
        raise _invalid("langs: expected a non-empty list of distinct language codes")
    period = spec.get("period")
    if not isinstance(period, dict) or not (period.get("relative") or period.get("start")):
        raise _invalid("period: relative or start is required")
    if period.get("relative"):
        parse_relative(period["relative"])
    for key in ("start", "end"):
        if period.get(key) is not None:
            months.parse(period[key])

    options = spec.setdefault("options", {})
    options.setdefault("include_redirects", True)
    options.setdefault("max_redirects", T.MAX_REDIRECTS)
    options.setdefault("metric", "share")
    if not isinstance(options["max_redirects"], int) or options["max_redirects"] < 0:
        raise _invalid("options.max_redirects: expected an integer >= 0")
    if options["metric"] not in METRICS:
        raise _invalid(f"options.metric: expected one of {', '.join(METRICS)}")
    if "criteria" in options:
        check_criteria(options["criteria"])

    topics = spec.get("topics")
    if not isinstance(topics, list) or not topics:
        raise _invalid("topics: at least one topic is required")
    ids = []
    for i, topic in enumerate(topics):
        where = f"topics[{i}]"
        if not isinstance(topic, dict) or not isinstance(topic.get("id"), str) or not isinstance(topic.get("label"), str):
            raise _invalid(f"{where}: id and label are required")
        if slugify(topic["id"], topic["id"]) != topic["id"]:
            raise _invalid(f"{where}.id: only [a-z0-9-] is allowed")
        seeds = _check_qids(topic.get("seeds", []), f"{where}.seeds")
        items = _check_qids(topic.get("items", []), f"{where}.items")
        if not seeds or not set(seeds) <= set(items):
            raise _invalid(f"{where}: seeds must be non-empty and part of items")
        ids.append(topic["id"])
    if len(set(ids)) != len(ids):
        raise _invalid("topics: duplicate ids")
    check_limits(spec)
    return spec


def check_limits(spec: Dict[str, Any]) -> None:
    problems = []
    if len(spec["langs"]) > T.MAX_LANGS:
        problems.append(f"{len(spec['langs'])} languages (max {T.MAX_LANGS})")
    if len(spec["topics"]) > T.MAX_TOPICS:
        problems.append(f"{len(spec['topics'])} topics (max {T.MAX_TOPICS})")
    for topic in spec["topics"]:
        if len(topic["items"]) > T.MAX_QIDS_PER_TOPIC:
            problems.append(f"topic {topic['id']}: {len(topic['items'])} QIDs (max {T.MAX_QIDS_PER_TOPIC})")
    if problems:
        raise WikitrendsError("limits_exceeded", "; ".join(problems), hint="Narrow the request: fewer languages, topics or QIDs")


def check_criteria(criteria: Any) -> None:
    if not isinstance(criteria, dict):
        raise _invalid("options.criteria: expected an object")
    for key, value in criteria.items():
        if key not in CRITERIA_FIELDS:
            raise _invalid(f"unknown criterion {key!r}", hint="Use min_monthly_views, min_growth_pct_yr, size_axis")
        if key == "size_axis":
            if value not in SIZE_AXES:
                raise _invalid(f"size_axis: expected one of {', '.join(SIZE_AXES)}")
        elif not isinstance(value, (int, float)) or isinstance(value, bool):
            raise _invalid(f"{key}: expected a number")
    if criteria.get("min_monthly_views", 0) < 0:
        raise _invalid("min_monthly_views: expected a number >= 0")


# --- files -----------------------------------------------------------------------------


def load(path: Path) -> Dict[str, Any]:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise _invalid(f"no spec at {path}", hint="Run 'ls' to list specs and use the absolute path it prints")
    except (OSError, ValueError) as exc:
        raise _invalid(f"cannot read spec {path}: {exc}")
    return validate(data)


def save(path: Path, spec: Dict[str, Any]) -> None:
    fs.atomic_write_json(Path(path), validate(spec))


def create(workdir: Path, spec: Dict[str, Any]) -> Path:
    """Write a new spec to <workdir>/<slug>/spec.json; a taken slug gets -2, -3, …"""
    spec = validate(spec)
    first = spec["topics"][0]
    base = slugify(first["label"], first["seeds"][0])
    taken = [p.name for p in Path(workdir).iterdir() if (p / SPEC_FILE).exists()] if Path(workdir).exists() else []
    directory = Path(workdir) / unique(base, taken)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / SPEC_FILE
    fs.atomic_write_json(path, spec)
    return path.resolve()


def new_spec(display_lang: str, langs: Sequence[str], topics: Sequence[Dict[str, Any]], period: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "version": VERSION,
        "display_lang": display_lang,
        "langs": list(langs),
        "period": period or {"relative": f"{T.DEFAULT_PERIOD_MONTHS}m"},
        "options": {"include_redirects": True, "max_redirects": T.MAX_REDIRECTS, "metric": "share"},
        "topics": [dict(t) for t in topics],
    }


def make_topic(label: str, seeds: Sequence[str], items: Sequence[str], taken_ids: Sequence[str] = ()) -> Dict[str, Any]:
    items = list(dict.fromkeys([*seeds, *items]))
    return {"id": unique(slugify(label, seeds[0]), taken_ids), "label": label, "seeds": list(seeds), "items": items}


# --- edit operations -------------------------------------------------------------------


def find_topic(spec: Dict[str, Any], topic_id: Optional[str]) -> Dict[str, Any]:
    ids = [t["id"] for t in spec["topics"]]
    if topic_id is None:
        if len(ids) == 1:
            return spec["topics"][0]
        raise WikitrendsError("topic_required", "The spec has several topics", hint="Repeat with --topic <id>", topics=ids)
    for topic in spec["topics"]:
        if topic["id"] == topic_id:
            return topic
    raise WikitrendsError("topic_required", f"No topic {topic_id!r}", hint="Repeat with --topic <id>", topics=ids)


def add_items(spec: Dict[str, Any], topic_id: Optional[str], qids: Sequence[str]) -> None:
    topic = find_topic(spec, topic_id)
    _check_qids(list(dict.fromkeys(qids)), "--add")
    topic["items"] = list(dict.fromkeys([*topic["items"], *qids]))


def drop_items(spec: Dict[str, Any], topic_id: Optional[str], qids: Sequence[str]) -> None:
    topic = find_topic(spec, topic_id)
    dropped = set(qids)
    if dropped & set(topic["seeds"]) and set(topic["seeds"]) <= dropped:
        raise _invalid("cannot drop every seed of a topic", hint="Drop the topic with --drop-topic or add another seed first")
    topic["seeds"] = [q for q in topic["seeds"] if q not in dropped]
    topic["items"] = [q for q in topic["items"] if q not in dropped]


def add_topic(spec: Dict[str, Any], label: str, seeds: Sequence[str], items: Sequence[str] = ()) -> Dict[str, Any]:
    _check_qids(list(seeds), "--seed")
    topic = make_topic(label, seeds, items, [t["id"] for t in spec["topics"]])
    spec["topics"].append(topic)
    return topic


def drop_topic(spec: Dict[str, Any], topic_id: str) -> None:
    topic = find_topic(spec, topic_id)
    if len(spec["topics"]) == 1:
        raise _invalid("cannot drop the only topic")
    spec["topics"].remove(topic)


def add_lang(spec: Dict[str, Any], lang: str) -> None:
    if lang not in spec["langs"]:
        spec["langs"].append(lang)


def drop_lang(spec: Dict[str, Any], lang: str) -> None:
    if lang not in spec["langs"]:
        raise _invalid(f"{lang!r} is not in the spec", hint=f"Languages: {', '.join(spec['langs'])}")
    if len(spec["langs"]) == 1:
        raise _invalid("cannot drop the only language")
    spec["langs"].remove(lang)


def set_relative(spec: Dict[str, Any], relative: str) -> None:
    parse_relative(relative)
    spec["period"] = {"relative": relative}


def set_explicit(spec: Dict[str, Any], start: str, end: Optional[str], warnings: List[dict]) -> None:
    start, end = check_explicit(start, end, warnings)
    spec["period"] = {"start": start, **({"end": end} if end else {})}


def refresh_window(spec: Dict[str, Any]) -> None:
    period = spec["period"]
    if not period.get("relative"):
        raise _invalid("the spec has explicit dates and no relative period",
                       hint="Set --period 24m, or new dates with --start/--end")
    spec["period"] = {"relative": period["relative"]}


def set_metric(spec: Dict[str, Any], metric: str) -> None:
    """options.metric: the main metric of the trend (agent contract §2)."""
    if metric not in METRICS:
        raise _invalid(f"unknown metric {metric!r}", hint="Use --metric share or --metric views")
    spec["options"]["metric"] = metric


def parse_criteria(text: str) -> Dict[str, Any]:
    """'min_monthly_views=50000,min_growth_pct_yr=15,size_axis=level_ppm' -> dict."""
    out: Dict[str, Any] = {}
    for part in filter(None, (p.strip() for p in text.split(","))):
        key, sep, value = part.partition("=")
        key = key.strip()
        if not sep or key not in CRITERIA_FIELDS:
            raise _invalid(f"invalid criterion {part!r}",
                           hint="Use min_monthly_views=<number>, min_growth_pct_yr=<number>, size_axis=monthly_views|level_ppm")
        if key == "size_axis":
            out[key] = value.strip()
            continue
        try:
            out[key] = float(value) if "." in value else int(value)
        except ValueError:
            raise _invalid(f"{key}: {value!r} is not a number")
    check_criteria(out)
    return out


def set_criteria(spec: Dict[str, Any], criteria: Dict[str, Any]) -> None:
    spec["options"].setdefault("criteria", {}).update(criteria)


def clear_criteria(spec: Dict[str, Any]) -> None:
    spec["options"].pop("criteria", None)


# --- listing ---------------------------------------------------------------------------


def summary(spec: Dict[str, Any], path: Optional[Path] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "langs": spec["langs"],
        "period": spec["period"],
        "metric": spec["options"].get("metric", "share"),
        "topics": [{"id": t["id"], "label": t["label"], "seeds": t["seeds"], "items": len(t["items"])} for t in spec["topics"]],
    }
    if spec["options"].get("criteria"):
        out["criteria"] = spec["options"]["criteria"]
    if path is not None:
        out["spec"] = str(Path(path).resolve())
    return out


def list_specs(directory: Path) -> List[Dict[str, Any]]:
    """Specs under a directory: slug, topics, languages, period, last analysis (agent contract §1 p.7)."""
    out = []
    root = Path(directory)
    if not root.exists():
        return out
    for path in sorted(root.glob(f"*/{SPEC_FILE}")):
        try:
            spec = load(path)
        except WikitrendsError:
            continue
        result = path.parent / "result.json"
        item = summary(spec, path)
        item["slug"] = path.parent.name
        item["last_analysis"] = _mtime(result) if result.exists() else None
        out.append(item)
    return out


# --- repeated requests (agent contract §2, slice 2) --------------------------------------


def same_period(existing: Dict[str, Any], requested: Optional[Dict[str, Any]]) -> bool:
    """A request without a period means the default relative period."""
    requested = requested or {"relative": f"{T.DEFAULT_PERIOD_MONTHS}m"}
    if requested.get("relative"):
        return existing.get("relative") == requested["relative"]
    if existing.get("relative"):
        return False
    return existing.get("start") == requested.get("start") and (
        not requested.get("end") or existing.get("end") == requested["end"])


def find_existing(workdir: Path, seeds: Sequence[str]) -> Optional[Tuple[Path, Dict[str, Any]]]:
    """The newest spec with ONE topic and the same set of seeds; specs with several topics never
    match (it is unclear that the user wants to continue that comparison)."""
    found = []
    root = Path(workdir)
    if not root.exists():
        return None
    for path in root.glob(f"*/{SPEC_FILE}"):
        try:
            data = load(path)
        except WikitrendsError:
            continue
        if len(data["topics"]) == 1 and set(data["topics"][0]["seeds"]) == set(seeds):
            found.append((path.stat().st_mtime, str(path), data))
    if not found:
        return None
    _, path, data = max(found)
    return Path(path).resolve(), data


def newer_months(period: Dict[str, Any], latest: str) -> int:
    """Months published after the fixed end of a relative spec; 0 for explicit dates (the user chose them)."""
    if not period.get("relative") or not period.get("end"):
        return 0
    return max(months.diff(latest, period["end"]), 0)


def _mtime(path: Path) -> str:
    stamp = datetime.datetime.fromtimestamp(path.stat().st_mtime, tz=datetime.timezone.utc)
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")
