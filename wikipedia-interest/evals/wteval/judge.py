"""LLM judge by the binary checklist (evals/judge-checklist.md, calibration §4)."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, Optional

ITEMS = ("exaggeration", "reasons", "comparison", "limits", "action")
CHECKLIST = Path(__file__).resolve().parents[1] / "judge-checklist.md"


def facts(result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """What the judge needs from result.json, small enough for one prompt."""
    if not result:
        return {}
    out: Dict[str, Any] = {"period": f"{result['period']['start']}..{result['period']['end']}", "topics": []}
    for t in result["topics"]:
        out["topics"].append({
            "topic": t["label"],
            "basket": [i["label"] for i in t["items"]],
            "cells": [{"lang": c["lang"], "group": c["group"], "growth": c["fmt"].get("growth"),
                       "high_reasons": [r["text"] for r in c["reasons"] if r["severity"] in ("high", "blocking")]}
                      for c in t["cells"]],
            "not_significant_pairs": [[p["a"], p["b"]] for p in t["pairs"] if not p["significant"]],
            "explore_next": [x["key"] for x in (t.get("recommendations") or {}).get("explore", [])],
        })
    return out


def prompt(question: str, answer: str, result: Optional[Dict[str, Any]], checklist: str) -> str:
    return (
        "You grade an assistant's answer about Wikipedia pageview trends against a checklist.\n\n"
        f"{checklist}\n\nFacts from the analysis (JSON):\n{json.dumps(facts(result), ensure_ascii=False)}\n\n"
        f"User question:\n{question}\n\nAssistant answer:\n{answer}\n\n"
        "Reply with JSON only: {" + ", ".join(f'"{i}": {{"pass": true, "why": "..."}}' for i in ITEMS) + "}"
    )


def parse(text: str) -> Dict[str, Dict[str, Any]]:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("no JSON in the judge's reply")
    data = json.loads(match.group(0))
    out = {}
    for item in ITEMS:
        value = data.get(item)
        if not isinstance(value, dict) or not isinstance(value.get("pass"), bool):
            raise ValueError(f"judge item {item!r} missing or malformed")
        out[item] = {"pass": value["pass"], "why": str(value.get("why", ""))}
    return out


def judge(call: Callable[[str], str], question: str, answer: str, result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    text = call(prompt(question, answer, result, CHECKLIST.read_text(encoding="utf-8")))
    items = parse(text)
    return {"items": items, "passed": all(v["pass"] for v in items.values())}


def claude_judge(model: str, executable: str = "claude", env: Optional[Dict[str, str]] = None) -> Callable[[str], str]:
    """A judge call through Claude Code: `claude -p --output-format json`, no tools."""
    def call(text: str) -> str:
        proc = subprocess.run([executable, "-p", text, "--model", model, "--output-format", "json", "--max-turns", "1"],
                              capture_output=True, text=True, timeout=300, env=env)
        return json.loads(proc.stdout)["result"]
    return call
