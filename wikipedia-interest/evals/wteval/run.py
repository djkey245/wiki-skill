"""Run scenarios with a runner, check transcripts, judge answers, write metrics and gates.

    python3 evals/run.py --runner claude --model claude-haiku-4-5 --runs 5 [--scenario ID ...]
    python3 evals/run.py --runner openrouter --model <vendor/model:free> --openrouter-set
"""
from __future__ import annotations

import argparse
import datetime
import gzip
import json
import os
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import checks, judge, scenarios
from .runners import ClaudeCodeRunner, OpenRouterRunner, RateLimited, clean_env, prepare_project
from .transcript import Transcript, Turn

EVALS = Path(__file__).resolve().parents[1]
SKILL_DIR = EVALS.parent
CORPUS = EVALS / "corpus"
RESULTS = EVALS / "results"
CONFIG = json.loads((EVALS / "config.json").read_text())  # today: the month the corpus was recorded in
# R22 starting gates; the values are fixed after the first 5-run baseline (calibration R22)
GATE_PASS_RATE = 0.8
GATE_GROWTH = 1.2
GATE_PARTIAL = 0.9


def count_records(directory: Path) -> int:
    total = 0
    for path in Path(directory).glob("*.jsonl.gz"):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            total += sum(1 for line in handle if line.strip())
    return total


def newest_result(workdir: Path) -> Optional[Dict[str, Any]]:
    files = sorted(Path(workdir).glob("*/result.json"), key=lambda p: (p.stat().st_mtime, str(p)))
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


def errors_in(turn: Turn) -> int:
    """Failed tool calls and CLI errors the scenario did not ask for."""
    n = 0
    for call in turn.calls:
        if call.is_error:
            n += 1
        elif call.name == "Bash" and '"ok": false' in call.output:
            n += 1
    return n


def run_scenario(scenario: Dict[str, Any], runner: Any, *, run: int = 1, corpus_root: Path = CORPUS,
                 judge_call: Optional[Callable[[str], str]] = None, skill_dir: Path = SKILL_DIR,
                 base_env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"eval-{scenario['id']}-") as tmp:
        root = Path(tmp)
        project, installed = prepare_project(skill_dir, root)
        corpus = Path(corpus_root) / scenario["corpus"]
        env = clean_env(base_env if base_env is not None else os.environ, {
            "WIKITRENDS_HOME": str(root / "wd"), "WIKITRENDS_CACHE_DIR": str(root / "cache"),
            "WIKITRENDS_HTTP": f"replay-or-record:{corpus}", "WIKITRENDS_TODAY": scenario.get("today") or CONFIG["today"],
            **scenario["env"]})
        for argv in scenario["setup"]:
            subprocess.run([sys.executable, str(installed / "scripts" / "wikitrends.py"), *argv], env=env,
                           capture_output=True, text=True, timeout=600, check=True)
        before = count_records(corpus)
        transcript = Transcript(scenario["id"], runner.name, runner.model)
        session = runner.session(project, env, installed)
        steps: List[Dict[str, Any]] = []
        passed = True
        try:
            for step in scenario["steps"]:
                turn = session.send(step["user"])
                transcript.turns.append(turn)
                result = newest_result(root / "wd")
                results = [checks.run_check(c["check"], turn, result, {k: v for k, v in c.items() if k != "check"})
                           for c in step.get("checks", [])]
                if step.get("answer_checks", True) and result is not None and turn.answer:
                    named = {c["check"] for c in step.get("checks", [])}
                    results += [checks.run_check(n, turn, result) for n in checks.ANSWER_CHECKS if n not in named]
                verdict = None
                if judge_call is not None and step.get("judge", step.get("answer_checks", True)) and turn.answer:
                    verdict = judge.judge(judge_call, step["user"], turn.answer, result)
                ok = turn.error is None and all(r["passed"] for r in results) and (verdict is None or verdict["passed"])
                steps.append({"user": step["user"], "passed": ok, "checks": results, "judge": verdict,
                              "error": turn.error, "tokens": turn.tokens, "tool_calls": len(turn.calls)})
                passed = passed and ok
                if not ok and step.get("stop_on_fail"):
                    break
        except RateLimited as exc:
            transcript.status = "rate_limited"
            steps.append({"user": "", "passed": False, "error": f"rate limited: {exc}", "checks": [], "judge": None,
                          "tokens": 0, "tool_calls": 0})
        transcript.replay_misses = count_records(corpus) - before
        completed = len(steps) == len(scenario["steps"]) and transcript.status == "ok"
        return {
            "scenario": scenario["id"], "run": run, "runner": runner.name, "model": runner.model,
            "status": transcript.status, "passed": passed and completed, "steps": steps,
            "tokens": sum(t.tokens for t in transcript.turns), "tool_calls": sum(len(t.calls) for t in transcript.turns),
            "errors": sum(errors_in(t) for t in transcript.turns), "replay_misses": transcript.replay_misses,
            "transcript": transcript.to_json(),
        }


def summarize(records: Sequence[Dict[str, Any]], scenario_list: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    by_id = {s["id"]: s for s in scenario_list}
    out: Dict[str, Any] = {}
    for sid in dict.fromkeys(r["scenario"] for r in records):
        runs = [r for r in records if r["scenario"] == sid and r["status"] != "rate_limited"]
        if not runs:
            out[sid] = {"runs": 0, "status": "rate_limited"}
            continue
        rates = [1.0 if r["passed"] else 0.0 for r in runs]
        out[sid] = {
            "runs": len(runs), "pass_rate": sum(rates) / len(rates),
            "median_tokens": statistics.median(r["tokens"] for r in runs),
            "median_tool_calls": statistics.median(r["tool_calls"] for r in runs),
            "errors": sum(r["errors"] for r in runs), "replay_misses": sum(r["replay_misses"] for r in runs),
            "example": by_id.get(sid, {}).get("example", False),
            "steps_pass_rate": [statistics.mean(1.0 if r["steps"][i]["passed"] else 0.0 for r in runs if len(r["steps"]) > i)
                                for i in range(max(len(r["steps"]) for r in runs))],
        }
    return out


def gates(summary: Dict[str, Any], baseline: Optional[Dict[str, Any]] = None) -> List[str]:
    """R22: task examples pass ≥ 80 % and not below the baseline beyond its spread; medians of
    tokens and tool calls at most 20 % above the baseline; the partial scenario finishes ≥ 90 %."""
    failures = []
    for sid, s in summary.items():
        if s.get("runs", 0) == 0:
            continue
        if s["example"] and s["pass_rate"] < GATE_PASS_RATE:
            failures.append(f"{sid}: pass rate {s['pass_rate']:.0%} < {GATE_PASS_RATE:.0%}")
        if sid == "partial" and s["pass_rate"] < GATE_PARTIAL:
            failures.append(f"{sid}: pass rate {s['pass_rate']:.0%} < {GATE_PARTIAL:.0%}")
        old = (baseline or {}).get(sid)
        if not old:
            continue
        if s["example"] and s["pass_rate"] < old["pass_rate"] - old.get("spread", 0.0):
            failures.append(f"{sid}: pass rate below the baseline {old['pass_rate']:.0%}")
        for key in ("median_tokens", "median_tool_calls"):
            if s[key] > old[key] * GATE_GROWTH:
                failures.append(f"{sid}: {key} {s[key]} > {GATE_GROWTH} x {old[key]}")
    return failures


def report(summary: Dict[str, Any], failures: Sequence[str], meta: Dict[str, Any]) -> str:
    lines = [f"# Evals: {meta['runner']} / {meta['model']} ({meta['date']})", "",
             "| Scenario | Runs | Pass rate | Median tokens | Median tool calls | Errors | Replay misses |",
             "|---|---|---|---|---|---|---|"]
    for sid, s in summary.items():
        if s.get("runs", 0) == 0:
            lines.append(f"| {sid} | 0 | rate limited | | | | |")
            continue
        mark = " (example)" if s["example"] else ""
        lines.append(f"| {sid}{mark} | {s['runs']} | {s['pass_rate']:.0%} | {s['median_tokens']:.0f} | "
                     f"{s['median_tool_calls']:.0f} | {s['errors']} | {s['replay_misses']} |")
    lines += ["", "Gates: " + ("passed" if not failures else "FAILED"), *[f"- {f}" for f in failures]]
    return "\n".join(lines) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="evals/run.py")
    parser.add_argument("--runner", choices=("claude", "openrouter"), required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--scenario", action="append")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--judge-model")
    parser.add_argument("--openrouter-set", action="store_true", help="only the scenarios marked for OpenRouter")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--out", type=Path, default=RESULTS)
    args = parser.parse_args(argv)
    chosen = [s for s in scenarios.load_all() if (not args.scenario or s["id"] in args.scenario)
              and (not args.openrouter_set or s["openrouter"])]
    if args.runner == "claude":
        runner: Any = ClaudeCodeRunner(args.model)
    else:
        runner = OpenRouterRunner(args.model, os.environ["OPENROUTER_API_KEY"])
        runner.check_model()
    judge_call = judge.claude_judge(args.judge_model, env=clean_env(os.environ, {})) if args.judge_model else None
    records = []
    for scenario in chosen:
        for run in range(1, args.runs + 1):
            record = run_scenario(scenario, runner, run=run, judge_call=judge_call)
            records.append(record)
            print(f"{scenario['id']} #{run}: {'pass' if record['passed'] else record['status'] if record['status'] != 'ok' else 'FAIL'}",
                  file=sys.stderr)
            if record["status"] == "rate_limited":
                break
        if records and records[-1]["status"] == "rate_limited":
            break  # the daily limit is spent: keep what ran (testing §11)
    summary = summarize(records, chosen)
    baseline = json.loads(args.baseline.read_text()) if args.baseline else None
    failures = gates(summary, baseline)
    meta = {"runner": runner.name, "model": runner.model, "date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")}
    args.out.mkdir(parents=True, exist_ok=True)
    stem = args.out / f"{meta['date']}-{runner.name}-{runner.model.replace('/', '_').replace(':', '_')}"
    stem.with_suffix(".json").write_text(json.dumps({"meta": meta, "summary": summary, "gates": failures,
                                                     "records": records}, ensure_ascii=False, indent=1))
    stem.with_suffix(".md").write_text(report(summary, failures, meta))
    print(report(summary, failures, meta))
    return 1 if failures and args.runner == "claude" else 0  # OpenRouter is reported, never gates a release


if __name__ == "__main__":
    sys.exit(main())
