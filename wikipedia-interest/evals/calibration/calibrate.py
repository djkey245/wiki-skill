#!/usr/bin/env python3
"""Calibration level 0a (calibration §5): python3 evals/calibration/calibrate.py {select,record,r1,r12}.

select, record and r12 need the network; r1 runs offline on the recorded corpus. Reports go to
evals/calibration/reports/<date>-<name>.json (+ .md)."""
import argparse
import datetime
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parents[1] / "scripts")]

from wikitrends import deadline, http as wthttp  # noqa: E402
from wikitrends.cache import Cache  # noqa: E402
from wtcal import corpus as C  # noqa: E402
from wtcal import r1, r12  # noqa: E402

REPORTS = HERE / "reports"


def client():
    return wthttp.HttpClient.from_env(dict(os.environ), deadline.Deadline(24 * 3600))


def cache():
    root = Path(os.environ.get("WIKITRENDS_CACHE_DIR", HERE / ".cache"))
    root.mkdir(parents=True, exist_ok=True)
    return Cache(root / "cache.sqlite")


def save(name, report, markdown):
    REPORTS.mkdir(exist_ok=True)
    stamp = datetime.date.today().isoformat()
    (REPORTS / f"{stamp}-{name}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    (REPORTS / f"{stamp}-{name}.md").write_text(markdown + "\n")
    print(markdown)


def table(rows, head):
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    return lines + ["| " + " | ".join("" if v is None else str(v) for v in row) + " |" for row in rows]


def main(argv=None):
    parser = argparse.ArgumentParser(prog="calibrate.py")
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("select")
    s.add_argument("--seed", type=int, default=20260927)
    s.add_argument("--per-field", type=int, default=2)
    r = sub.add_parser("record")
    r.add_argument("--end", required=True, help="last month of the full series, e.g. 2026-08")
    a = sub.add_parser("r1")
    a.add_argument("--end", required=True, help="end of the analysed window, e.g. 2026-08")
    a.add_argument("--shuffles", type=int, default=10)
    sub.add_parser("r12")
    args = parser.parse_args(argv)

    if args.command == "select":
        http, c = client(), cache()
        chosen = C.freeze_baskets(http, c, C.select(http, c, seed=args.seed, per_field=args.per_field))
        C.CORPUS.write_text(json.dumps({"seed": args.seed, "langs": list(C.LANGS), "fields": list(C.FIELDS),
                                        "per_field": args.per_field, "extra": list(C.EXTRA), "topics": chosen},
                                       ensure_ascii=False, indent=2) + "\n")
        print(f"{len(chosen)} topics -> {C.CORPUS}")
    elif args.command == "record":
        written = C.record(client(), cache(), C.load(), args.end)
        print(f"recorded {len(written)} topics -> {C.DATA}")
    elif args.command == "r1":
        corpus = C.load()
        report = r1.run(r1.corpus_series(corpus, C.read, C.window, args.end), shuffles=args.shuffles)
        report.update(end=args.end, topics=len(corpus["topics"]), verdicts=r1.verdicts(report))
        rows = [[size, *(v[k] for k in ("series", "false_trend", "power_20", "step_trended_high", "step_like_on_step",
                                         "false_stable_at_10", "autocorr_capped_real"))]
                for size, v in report["sizes"].items()]
        md = "\n".join([f"# R1, level 0a (preliminary: no held-out part), window ending {args.end}", "",
                        *table(rows, ["size", "series", "false trend", "power 20 %/yr", "step: trend + high",
                                      "step_like on step", "false stable at 10 %/yr", "autocorr_capped (real)"]),
                        "", f"Test / CI agreement: {report['test_ci_agreement']}",
                        "", "Verdicts: " + json.dumps(report["verdicts"])])
        save("r1", report, md)
    else:
        report = {name: r12.measure(argv) for name, argv in r12.EXAMPLES.items()}
        verdicts = r12.verdicts(report)
        rows = [[n, r["n_calls"], r["max_call_seconds"], r["network"], r["throttled"], r["finished"], verdicts[n]]
                for n, r in report.items()]
        save("r12", {"examples": report, "verdicts": verdicts},
             "\n".join(["# R12, level 0a: cold cache", "",
                        *table(rows, ["example", "calls", "max s/call", "requests", "429", "finished", "verdicts"])]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
