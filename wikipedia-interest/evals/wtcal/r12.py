"""R12 at level 0a (calibration §3.5): the three task examples on a cold cache, as the agent runs
them: a command, then its next_steps while the output says partial. Wall time per call, calls,
network requests and 429 answers. Needs the network (Part B)."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Sequence

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "wikitrends.py"
MAX_CALLS = 5
EXAMPLES = {  # the three task examples as the skill's decision tree runs them (seeds of example 2 from find)
    "example_1": ["quick", "астрономія", "--lang", "uk", "--langs", "pl,cs", "--display-lang", "uk"],
    "example_2": ["quick", "інтервальне голодування", "--lang", "uk", "--langs", "uk,pl,cs", "--display-lang", "uk"],
    "example_3": ["quick", "вивчення англійської мови", "--lang", "uk", "--langs", "pl,cs,ro,hu", "--display-lang", "uk"],
}
CRITERIA = {"max_call_seconds": 100.0, "max_calls": 2}  # calibration R12: <= 2 calls of 90 s, none above 100 s


def run_cli(argv: Sequence[str], env: Dict[str, str]) -> Dict[str, Any]:
    started = time.monotonic()
    done = subprocess.run([sys.executable, str(SCRIPT), *argv], capture_output=True, text=True, env=env)
    out = json.loads(done.stdout)
    out["_seconds"] = round(time.monotonic() - started, 1)
    return out


def measure(argv: Sequence[str], run: Callable[[Sequence[str], Dict[str, str]], Dict[str, Any]] = run_cli) -> Dict[str, Any]:
    """One example on a fresh workdir and cache."""
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "WIKITRENDS_HOME": str(Path(tmp) / "wd"), "WIKITRENDS_CACHE_DIR": str(Path(tmp) / "cache")}
        calls: List[Dict[str, Any]] = []
        command = list(argv)
        while len(calls) < MAX_CALLS:
            out = run(command, env)
            requests = out.get("requests", {})
            calls.append({"argv": command[0], "seconds": out.pop("_seconds", None), "partial": bool(out.get("partial")),
                          "network": requests.get("network") if isinstance(requests, dict) else requests,
                          "throttled": requests.get("throttled", 0) if isinstance(requests, dict) else 0,
                          "ok": out.get("ok"), "code": out.get("code")})
            if not out.get("partial"):
                break
            command = shlex.split(out["next_steps"][0])[2:]  # drop "python3 <script>"
    return {"calls": calls, "n_calls": len(calls), "max_call_seconds": max(c["seconds"] or 0 for c in calls),
            "network": sum(c["network"] or 0 for c in calls), "throttled": sum(c["throttled"] or 0 for c in calls),
            "finished": not calls[-1]["partial"] and bool(calls[-1]["ok"])}


def verdicts(report: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, bool]]:
    return {name: {"calls": r["n_calls"] <= CRITERIA["max_calls"] and r["finished"],
                   "seconds": r["max_call_seconds"] <= CRITERIA["max_call_seconds"],
                   "no_429": r["throttled"] == 0, "one_call": r["n_calls"] == 1}
            for name, r in report.items()}
