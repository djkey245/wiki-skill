"""Argument parsing, JSON output and exit codes. Exactly one JSON object goes to stdout."""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shlex
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, TextIO, Tuple

from . import analysis, budget, candidates, fs, months, pageviews, sitematrix, spec, templates, topics, wikidata
from . import thresholds as T
from .cache import Cache
from .deadline import Deadline, DeadlineReached
from .errors import WikitrendsError, needs_user
from .http import HttpClient

SCHEMA_VERSION = 1
CACHE_FILE = "cache.sqlite"
SCRIPT = Path(__file__).resolve().parents[1] / "wikitrends.py"
REPORT_SCRIPT = SCRIPT.with_name("report.py")


class JsonArgumentParser(argparse.ArgumentParser):
    """argparse that raises instead of printing usage and exiting."""

    def error(self, message: str) -> None:  # type: ignore[override]
        raise WikitrendsError(
            "invalid_arguments", message, hint=f"Run: python3 <skill_dir>/scripts/{self.prog} --help"
        )


@dataclass
class Context:
    env: Mapping[str, str]
    cwd: Path
    home: Path
    workdir_arg: Optional[str]
    warnings: List[dict] = field(default_factory=list)
    argv: List[str] = field(default_factory=list)
    http_factory: Optional[Callable[[Deadline], HttpClient]] = None
    today: Optional[str] = None  # current (incomplete) month, YYYY-MM
    _workdir: Optional[Path] = None

    def workdir(self) -> Path:
        if self._workdir is None:
            self._workdir = fs.resolve_workdir(self.workdir_arg, self.env, self.cwd, self.home, self.warnings)
        return self._workdir

    def cache_dir(self) -> Path:
        return fs.resolve_cache_dir(self.env, self.home, self.workdir())

    def open_cache(self) -> Cache:
        return Cache(self.cache_dir() / CACHE_FILE)

    def http(self, max_seconds: float = T.DEFAULT_MAX_SECONDS) -> HttpClient:
        deadline = Deadline(max_seconds)
        if self.http_factory is not None:
            return self.http_factory(deadline)
        client = HttpClient.from_env(self.env, deadline)
        cap = self.env.get("WIKITRENDS_REQUEST_CAP")
        if cap:  # evals: the deadline hits after N network requests, reproducibly on a replay corpus
            client.deadline = Deadline(float(cap), clock=lambda: float(client.requests_made))
        return client

    def current_month(self) -> str:
        """The current (incomplete) month; WIKITRENDS_TODAY pins it so a replay corpus keeps matching."""
        return (self.today or self.env.get("WIKITRENDS_TODAY")
                or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m"))

    def command(self, *args: str) -> str:
        """A ready command for next_steps, with the absolute script path."""
        return " ".join(["python3", shlex.quote(str(SCRIPT)), *(shlex.quote(a) for a in args)])

    def report_command(self, *args: str) -> str:
        """The report command for next_steps: uv reads the script's PEP 723 header."""
        return " ".join(["uv", "run", shlex.quote(str(REPORT_SCRIPT)), *(shlex.quote(a) for a in args)])


def parse_duration(text: str) -> float:
    match = re.fullmatch(r"(\d+)d", text)
    if not match:
        raise WikitrendsError("invalid_arguments", f"invalid duration {text!r}", hint="Use days, e.g. --older-than 90d")
    return int(match.group(1)) * 24 * 3600


def cmd_cache_info(args: argparse.Namespace, ctx: Context) -> Dict[str, Any]:
    cache = ctx.open_cache()
    try:
        return cache.info()
    finally:
        cache.close()


def cmd_cache_clear(args: argparse.Namespace, ctx: Context) -> Dict[str, Any]:
    older = parse_duration(args.older_than) if args.older_than else None
    cache = ctx.open_cache()
    try:
        return {"deleted": cache.clear(older_than_seconds=older)}
    finally:
        cache.close()


def _langs(text: Optional[str]) -> List[str]:
    if not text:
        raise WikitrendsError(
            "langs_required", "No languages given",
            hint="Ask the user which Wikipedia languages to analyse; suggest 3-5 with a short reason",
        )
    return list(dict.fromkeys(x.strip() for x in text.split(",") if x.strip()))


def _display_lang(value: Optional[str]) -> str:
    if not value:
        raise WikitrendsError(
            "display_lang_required", "--display-lang is required",
            hint="Repeat the command with --display-lang <the user's language code>",
        )
    return value


def _seeds(values: Optional[List[str]]) -> List[str]:
    seeds = list(dict.fromkeys(values or []))
    if not seeds or not all(re.fullmatch(r"Q[1-9][0-9]*", q) for q in seeds):
        raise WikitrendsError("invalid_arguments", "expected --seed Q<number>", hint="Take QIDs from the output of find")
    return seeds


def cmd_find(args: argparse.Namespace, ctx: Context) -> Dict[str, Any]:
    cache = ctx.open_cache()
    try:
        http = ctx.http()
        site = sitematrix.SiteMatrix.load(http, cache).resolve(args.lang)
        result = topics.find(http, cache, site, args.query, args.lang)
    finally:
        cache.close()
    rows = [{"qid": c.qid, "label": c.label, "description": c.description, **({"title": c.title} if c.title else {})}
            for c in result.candidates]
    if not rows:
        return {"query": args.query, "lang": args.lang, "candidates": [],
                "message": "Nothing found", "hint": "Rephrase the query or search in another language (--lang en)"}
    if result.leader is None:
        return needs_user("ambiguous_topic", "Several topics match the query; ask the user which one is meant",
                          query=args.query, lang=args.lang, candidates=rows)
    template = ctx.command("quick", args.query, "--seed", result.leader, "--langs", "<langs>", "--display-lang", "<user lang>")
    return {"query": args.query, "lang": args.lang, "leader": result.leader, "candidates": rows, "next_steps": [template]}


def cmd_suggest(args: argparse.Namespace, ctx: Context) -> Dict[str, Any]:
    display_lang = _display_lang(args.display_lang)
    langs = _langs(args.langs)
    seeds = _seeds(args.seed)
    budget.check(candidates.estimate_requests(len(seeds), len(langs)), limit=T.MAX_SUGGEST_REQUESTS,
                 allow_large=args.allow_large, command="suggest")
    cache = ctx.open_cache()
    try:
        http = ctx.http(args.max_seconds)
        matrix = sitematrix.SiteMatrix.load(http, cache)
        matrix.resolve_many(langs)
        found = candidates.suggest(http, cache, matrix, seeds, langs, display_lang, args.limit)
    finally:
        cache.close()
    label = args.topic or found.seeds[0].label
    if args.spec:
        path = Path(args.spec)
        data = spec.load(path)
        if set(data["langs"]) != set(langs):
            raise WikitrendsError("invalid_arguments", "--langs differ from the languages of the spec",
                                  hint=f"Use --langs {','.join(data['langs'])} or change them with spec edit")
        topic = spec.add_topic(data, label, seeds, found.draft)
        spec.save(path, data)
        path = path.resolve()
    else:
        topic = spec.make_topic(label, seeds, found.draft)
        path = spec.create(ctx.workdir(), spec.new_spec(display_lang, langs, [topic]))
    labels = {c.qid: c.label for c in [*found.seeds, *found.ranked]}
    gaps = {c.qid: [lang for lang, title in c.coverage.items() if not title] for c in found.seeds}
    return {
        "spec": str(path),
        "topic": {"id": topic["id"], "label": topic["label"]},
        "draft": [{"qid": q, "label": labels.get(q, q)} for q in found.draft],
        "candidates": [
            {"qid": c.qid, "label": c.label, "score": round(c.score, 1),
             "langs": ",".join(lang for lang, title in c.coverage.items() if title)}
            for c in found.ranked
        ],
        **({"seed_gaps": {q: g for q, g in gaps.items() if g}} if any(gaps.values()) else {}),
        **({"excluded_types": found.excluded_types} if found.excluded_types else {}),
        "requests": http.requests_made,
        "next_steps": [
            ctx.command("analyze", str(path)),
            ctx.command("spec", "edit", str(path), "--topic", topic["id"], "--add", "<QID>", "--drop", "<QID>"),
        ],
    }


def _latest_month(ctx: Context, langs: List[str]) -> str:
    cache = ctx.open_cache()
    try:
        http = ctx.http()
        matrix = sitematrix.SiteMatrix.load(http, cache)
        mains = {}
        for site in matrix.resolve_many(langs):
            mains[site.project] = sitematrix.main_page(http, cache, site)
        latest = pageviews.latest_month(http, mains, ctx.current_month())
    finally:
        cache.close()
    if latest is None:
        raise WikitrendsError("no_data", "No month is published for every language yet", hint="Repeat later")
    return latest


def cmd_spec_edit(args: argparse.Namespace, ctx: Context) -> Dict[str, Any]:
    path = Path(args.file)
    data = spec.load(path)
    changed = False
    if args.add_topic:
        spec.add_topic(data, args.add_topic, _seeds(args.seed))
        changed = True
    elif args.seed:
        raise WikitrendsError("invalid_arguments", "--seed goes with --add-topic", hint="Use --add Q.. to add items")
    if args.drop_topic:
        spec.drop_topic(data, args.drop_topic)
        changed = True
    if args.add:
        spec.add_items(data, args.topic, args.add)
        changed = True
    if args.drop:
        spec.drop_items(data, args.topic, args.drop)
        changed = True
    if args.add_lang or args.drop_lang:
        if args.add_lang:
            cache = ctx.open_cache()
            try:
                sitematrix.SiteMatrix.load(ctx.http(), cache).resolve_many(args.add_lang)
            finally:
                cache.close()
            for lang in args.add_lang:
                spec.add_lang(data, lang)
        for lang in args.drop_lang or []:
            spec.drop_lang(data, lang)
        changed = True
    if args.period and args.start:
        raise WikitrendsError("invalid_arguments", "--period and --start are mutually exclusive",
                              hint="Use --period 24m or --start YYYY-MM [--end YYYY-MM]")
    if args.end and not args.start:
        raise WikitrendsError("invalid_arguments", "--end needs --start", hint="Use --start YYYY-MM --end YYYY-MM")
    if args.period:
        spec.set_relative(data, args.period)
        changed = True
    if args.start:
        spec.set_explicit(data, args.start, args.end, ctx.warnings)
        if args.end:
            latest = _latest_month(ctx, data["langs"])
            if months.diff(data["period"]["end"], latest) > 0:
                raise WikitrendsError("end_not_available", f"{args.end} is not published yet",
                                      hint=f"Use --end {latest} or earlier", latest=latest)
        changed = True
    if args.refresh_window:
        spec.refresh_window(data)
        changed = True
    if args.metric:
        spec.set_metric(data, args.metric)
        changed = True
    if args.clear_criteria:
        spec.clear_criteria(data)
        changed = True
    if args.criteria:
        spec.set_criteria(data, spec.parse_criteria(args.criteria))
        changed = True
    if not changed:
        raise WikitrendsError("invalid_arguments", "nothing to change", hint="Run spec edit --help")
    spec.check_limits(data)
    spec.save(path, data)
    return {**spec.summary(data, path), "next_steps": [ctx.command("analyze", str(path.resolve()))]}


def _analyze(ctx: Context, path: Path, http: HttpClient, cache: Cache, *, allow_large: bool, no_cache: bool,
             command: str, latest: Optional[str] = None) -> Dict[str, Any]:
    """Collect, evaluate, save a versioned result; the stdout part (agent contract §1 p.5)."""
    path = Path(path).resolve()
    data = spec.load(path)
    spec.check_limits(data)
    reader = analysis.ReadBypass(cache) if no_cache else cache
    matrix = sitematrix.SiteMatrix.load(http, reader)
    sites = dict(zip(data["langs"], matrix.resolve_many(data["langs"])))
    if latest is None:
        latest = analysis.latest_month(http, reader, list(sites.values()), ctx.current_month())
    window, changed = analysis.fix_window(data, latest)
    if changed:
        spec.save(path, data)
    qids = list(dict.fromkeys(q for t in data["topics"] for q in t["items"]))
    entities = wikidata.get_entities(http, reader, qids, languages=[data["display_lang"], *data["langs"]])
    got = analysis.collect(http, reader, data, sites, entities, window, allow_large=allow_large)
    ctx.warnings.extend(got.warnings)
    if got.partial:
        done = len(data["langs"]) - len(got.partial)
        return {"partial": True, "spec": str(path), "progress": {"langs_done": done, "langs_total": len(data["langs"])},
                "message": "Time limit reached; collected data is cached", "requests": {"network": http.requests_made},
                "next_steps": [command]}
    labels = analysis.entity_labels(entities, data["display_lang"], data["langs"])
    result = analysis.evaluate(data, window, got.data, got.failed, labels, got.revised)
    if analysis.analysed_cells(result) == 0:
        raise WikitrendsError("no_data", "No topic x language pair could be analysed",
                              hint="Tell the user; check the languages and the basket (spec edit)")
    previous = analysis.load_previous(path)
    found = analysis.changes(previous, result)
    if previous is not None:
        found += [{"kind": "data_revised", "lang": lang, "months": revised,
                   "note": "the previous result was computed on the old data"} for lang, revised in got.revised.items()]
    result.update({
        "spec": str(path), "latest_month": latest, "answer_md_full": templates.answer_md(result, full=True),
        "changes": found, "warnings": list(ctx.warnings), "requests": {"network": http.requests_made},
    })
    result_path = analysis.save_result(path, result)
    return {"spec": str(path), "result": str(result_path), **analysis.compact(result),
            "answer_md": templates.answer_md(result, full=False), "changes": found,
            "requests": result["requests"], "next_steps": [ctx.report_command(str(path))]}


def cmd_analyze(args: argparse.Namespace, ctx: Context) -> Dict[str, Any]:
    cache = ctx.open_cache()
    try:
        http = ctx.http(args.max_seconds)
        return _analyze(ctx, Path(args.file), http, cache, allow_large=args.allow_large, no_cache=args.no_cache,
                        command=ctx.command(*ctx.argv))
    finally:
        cache.close()


def _period(args: argparse.Namespace, ctx: Context) -> Optional[Dict[str, Any]]:
    if args.period and args.start:
        raise WikitrendsError("invalid_arguments", "--period and --start are mutually exclusive",
                              hint="Use --period 24m or --start YYYY-MM [--end YYYY-MM]")
    if args.end and not args.start:
        raise WikitrendsError("invalid_arguments", "--end needs --start", hint="Use --start YYYY-MM --end YYYY-MM")
    if args.period:
        spec.parse_relative(args.period)
        return {"relative": args.period}
    if args.start:
        start, end = spec.check_explicit(args.start, args.end, ctx.warnings)
        return {"start": start, **({"end": end} if end else {})}
    return None


def cmd_quick(args: argparse.Namespace, ctx: Context) -> Dict[str, Any]:
    display_lang = _display_lang(args.display_lang)
    langs = _langs(args.langs)
    if not args.seed and not args.lang:
        raise WikitrendsError("invalid_arguments", "quick needs --lang (the language of the query) or --seed",
                              hint="Add --lang <language of the query>, or --seed Q.. taken from find")
    period = _period(args, ctx)
    cache = ctx.open_cache()
    try:
        http = ctx.http(args.max_seconds)
        matrix = sitematrix.SiteMatrix.load(http, cache)
        sites = matrix.resolve_many(langs)
        if args.seed:
            seeds = _seeds(args.seed)
        else:
            found = topics.find(http, cache, matrix.resolve(args.lang), args.query, args.lang)
            rows = [{"qid": c.qid, "label": c.label, "description": c.description} for c in found.candidates]
            if not rows:
                return {"query": args.query, "lang": args.lang, "candidates": [], "message": "Nothing found",
                        "hint": "Rephrase the query or search in another language (--lang en)"}
            if found.leader is None:
                return needs_user("ambiguous_topic", "Several topics match the query; ask the user which one is meant",
                                  query=args.query, lang=args.lang, candidates=rows)
            seeds = [found.leader]
        budget.check(candidates.estimate_requests(len(seeds), len(langs)), limit=T.MAX_SUGGEST_REQUESTS,
                     allow_large=args.allow_large, command="suggest")
        suggestion = candidates.suggest(http, cache, matrix, seeds, langs, display_lang, T.SUGGEST_LIMIT)
        latest = analysis.latest_month(http, cache, sites, ctx.current_month())
        data = spec.new_spec(display_lang, langs, [spec.make_topic(args.query, seeds, suggestion.draft)], period)
        analysis.fix_window(data, latest)  # end_not_available / period_too_short before anything is written
        path = spec.create(ctx.workdir(), data)  # on disk before collecting pageviews (agent contract §1 p.1)
        command = ctx.command("analyze", str(path))
        try:
            out = _analyze(ctx, path, http, cache, allow_large=args.allow_large, no_cache=False, command=command,
                           latest=latest)
        except DeadlineReached:
            out = {"partial": True, "spec": str(path), "message": "Time limit reached; collected data is cached",
                   "next_steps": [command]}
    finally:
        cache.close()
    labels = {c.qid: c.label for c in [*suggestion.seeds, *suggestion.ranked]}
    out["basket"] = [{"qid": q, "label": labels.get(q, q)} for q in data["topics"][0]["items"]]
    return out


def cmd_ls(args: argparse.Namespace, ctx: Context) -> Dict[str, Any]:
    directory = Path(args.directory).resolve() if args.directory else ctx.workdir()
    return {"workdir": str(directory), "specs": spec.list_specs(directory)}


Handler = Callable[[argparse.Namespace, Context], Dict[str, Any]]

COMMANDS: Dict[Tuple[str, ...], Handler] = {
    ("cache", "info"): cmd_cache_info,
    ("cache", "clear"): cmd_cache_clear,
    ("find",): cmd_find,
    ("suggest",): cmd_suggest,
    ("spec", "edit"): cmd_spec_edit,
    ("ls",): cmd_ls,
    ("analyze",): cmd_analyze,
    ("quick",): cmd_quick,
}


def build_parser() -> JsonArgumentParser:
    parser = JsonArgumentParser(prog="wikitrends.py")
    parser.add_argument("--workdir")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=JsonArgumentParser)

    cache = sub.add_parser("cache")
    cache_sub = cache.add_subparsers(dest="subcommand", required=True, parser_class=JsonArgumentParser)
    cache_sub.add_parser("info")
    clear = cache_sub.add_parser("clear")
    clear.add_argument("--older-than")

    find = sub.add_parser("find")
    find.add_argument("query")
    find.add_argument("--lang", required=True)

    suggest = sub.add_parser("suggest")
    suggest.add_argument("--seed", action="append")
    suggest.add_argument("--langs")
    suggest.add_argument("--display-lang")
    suggest.add_argument("--topic")
    suggest.add_argument("--spec")
    suggest.add_argument("--limit", type=int, default=T.SUGGEST_LIMIT)
    suggest.add_argument("--new", action="store_true")  # slice 1 always creates a new spec
    suggest.add_argument("--allow-large", action="store_true")
    suggest.add_argument("--max-seconds", type=float, default=T.DEFAULT_MAX_SECONDS)

    spec_cmd = sub.add_parser("spec")
    spec_sub = spec_cmd.add_subparsers(dest="subcommand", required=True, parser_class=JsonArgumentParser)
    edit = spec_sub.add_parser("edit")
    edit.add_argument("file")
    edit.add_argument("--topic")
    edit.add_argument("--add", nargs="+")
    edit.add_argument("--drop", nargs="+")
    edit.add_argument("--add-topic")
    edit.add_argument("--seed", action="append")
    edit.add_argument("--drop-topic")
    edit.add_argument("--add-lang", action="append")
    edit.add_argument("--drop-lang", action="append")
    edit.add_argument("--period")
    edit.add_argument("--start")
    edit.add_argument("--end")
    edit.add_argument("--refresh-window", action="store_true")
    edit.add_argument("--metric")
    edit.add_argument("--criteria")
    edit.add_argument("--clear-criteria", action="store_true")

    ls = sub.add_parser("ls")
    ls.add_argument("directory", nargs="?")

    analyze = sub.add_parser("analyze")
    analyze.add_argument("file")
    analyze.add_argument("--allow-large", action="store_true")
    analyze.add_argument("--no-cache", action="store_true")
    analyze.add_argument("--max-seconds", type=float, default=T.DEFAULT_MAX_SECONDS)

    quick = sub.add_parser("quick")
    quick.add_argument("query")
    quick.add_argument("--langs")
    quick.add_argument("--display-lang")
    quick.add_argument("--lang")
    quick.add_argument("--seed", action="append")
    quick.add_argument("--period")
    quick.add_argument("--start")
    quick.add_argument("--end")
    quick.add_argument("--new", action="store_true")  # slice 1 always creates a new spec
    quick.add_argument("--allow-large", action="store_true")
    quick.add_argument("--max-seconds", type=float, default=T.DEFAULT_MAX_SECONDS)
    return parser

def _command_key(args: argparse.Namespace) -> Tuple[str, ...]:
    subcommand = getattr(args, "subcommand", None)
    return (args.command, subcommand) if subcommand else (args.command,)


def run(
    argv: List[str],
    env: Mapping[str, str],
    cwd: Path,
    home: Path,
    stdout: TextIO,
    *,
    http_factory: Optional[Callable[[Deadline], HttpClient]] = None,
    today: Optional[str] = None,
) -> int:
    ctx = Context(env=env, cwd=Path(cwd), home=Path(home), workdir_arg=None, argv=list(argv),
                  http_factory=http_factory, today=today)
    try:
        args = build_parser().parse_args(argv)
        ctx.workdir_arg = args.workdir
        result = COMMANDS[_command_key(args)](args, ctx)
        payload: Dict[str, Any] = {"ok": True}
        payload.update(result)
        exit_code = 0
    except DeadlineReached:
        # Nothing is written before the network work ends, and finished requests are cached:
        # repeating the same command continues (data §4).
        payload = {"ok": True, "partial": True, "message": "Time limit reached; results so far are cached",
                   "next_steps": [ctx.command(*ctx.argv)]}
        exit_code = 0
    except WikitrendsError as exc:
        payload = exc.to_payload()
        exit_code = exc.exit_code
    except Exception as exc:  # noqa: BLE001 - stdout must stay JSON
        traceback.print_exc(file=sys.stderr)
        payload = {
            "ok": False,
            "code": "internal_error",
            "message": f"{type(exc).__name__}: {exc}",
            "hint": "Tell the user the skill failed; do not retry in a loop",
        }
        exit_code = 1
    payload["schema_version"] = SCHEMA_VERSION
    if ctx.warnings:
        payload["warnings"] = ctx.warnings
    stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    stdout.flush()
    return exit_code


def main() -> None:
    sys.exit(run(sys.argv[1:], dict(os.environ), Path.cwd(), Path.home(), sys.stdout))
