"""Scenario files (evals/scenarios/*.json): user messages, checks per step, run settings."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from .checks import CHECKS

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"
TOP = {"id", "title", "steps", "example", "openrouter", "today", "corpus", "setup", "env", "note"}
STEP = {"user", "checks", "stop_on_fail", "answer_checks", "judge"}


def validate(data: Dict[str, Any], name: str = "") -> Dict[str, Any]:
    where = data.get("id", name)
    unknown = set(data) - TOP
    if unknown:
        raise ValueError(f"{where}: unknown keys {sorted(unknown)}")
    for key in ("id", "title", "steps"):
        if key not in data:
            raise ValueError(f"{where}: missing {key!r}")
    if not data["steps"]:
        raise ValueError(f"{where}: no steps")
    for i, step in enumerate(data["steps"], 1):
        if set(step) - STEP or not isinstance(step.get("user"), str) or not step["user"].strip():
            raise ValueError(f"{where} step {i}: needs a 'user' message and only {sorted(STEP)}")
        for check in step.get("checks", []):
            if check.get("check") not in CHECKS:
                raise ValueError(f"{where} step {i}: unknown check {check.get('check')!r}")
    for argv in data.get("setup", []):
        if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
            raise ValueError(f"{where}: setup entries are argv lists of wikitrends.py")
    data.setdefault("example", False)
    data.setdefault("openrouter", False)
    data.setdefault("corpus", data["id"])
    data.setdefault("setup", [])
    data.setdefault("env", {})
    return data


def load(path: Path) -> Dict[str, Any]:
    return validate(json.loads(Path(path).read_text(encoding="utf-8")), Path(path).stem)


def load_all(directory: Path = SCENARIOS) -> List[Dict[str, Any]]:
    items = [load(p) for p in sorted(Path(directory).glob("*.json"))]
    ids = [s["id"] for s in items]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate scenario ids")
    return items
