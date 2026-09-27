"""A whole scenario with a scripted agent, metrics, gates and the report."""
import os
import subprocess
import sys
from pathlib import Path

from test_cli_env import record
from wteval import run, scenarios
from wteval.transcript import ToolCall, Turn

SKILL_DIR = Path(__file__).resolve().parents[1]


class ScriptedRunner:
    """Runs the real CLI for the commands it is given, answers with canned text."""
    name, model = "fake", "scripted"

    def __init__(self, script):
        self.script = script

    def session(self, project, env, skill_dir):
        runner = self

        class Session:
            def send(self, text):
                answer, commands = runner.script.pop(0)
                calls = []
                for argv in commands:
                    proc = subprocess.run([sys.executable, str(skill_dir / "scripts" / "wikitrends.py"), *argv],
                                          env=env, capture_output=True, text=True)
                    calls.append(ToolCall("Bash", "python3 wikitrends.py " + " ".join(argv), proc.stdout))
                return Turn(text, calls, answer, 1000, 100)
        return Session()


def test_run_scenario_with_a_scripted_agent(tmp_path):
    corpus = tmp_path / "corpus"
    record(corpus / "s1", tmp_path)
    scenario = scenarios.validate({"id": "s1", "title": "t", "today": "2026-09", "steps": [
        {"user": "Які мови?", "checks": [{"check": "asked"}], "stop_on_fail": True, "answer_checks": False},
        {"user": "uk, pl, cs", "checks": [{"check": "commands", "expect": ["quick"]}]}]})
    quick = ["quick", "астрономія", "--seed", "Q1", "--langs", "uk,pl,cs", "--display-lang", "uk"]
    runner = ScriptedRunner([("Які мови вас цікавлять?", []),
                             ("Досліджувати далі: pl. Мовний розділ ≠ ринок.", [quick])])
    record_ = run.run_scenario(scenario, runner, corpus_root=corpus, skill_dir=SKILL_DIR, base_env={"PATH": os.environ["PATH"]})
    assert record_["status"] == "ok" and record_["replay_misses"] == 0
    assert record_["steps"][0]["passed"]
    names = [c["check"] for c in record_["steps"][1]["checks"]]
    assert "commands" in names and "numbers_match" in names  # answer checks run once a result exists
    summary = run.summarize([record_, {**record_, "run": 2, "passed": True}], [scenario])
    assert summary["s1"]["runs"] == 2 and summary["s1"]["median_tool_calls"] == 1


def test_gates_and_report():
    summary = {"example_1_pl_cs": {"runs": 5, "pass_rate": 0.6, "median_tokens": 50000, "median_tool_calls": 12,
                                   "errors": 0, "replay_misses": 0, "example": True, "steps_pass_rate": [0.6]},
               "partial": {"runs": 5, "pass_rate": 1.0, "median_tokens": 30000, "median_tool_calls": 8,
                           "errors": 0, "replay_misses": 1, "example": False, "steps_pass_rate": [1.0]}}
    baseline = {"partial": {"pass_rate": 1.0, "median_tokens": 20000, "median_tool_calls": 8}}
    failures = run.gates(summary, baseline)
    assert any("example_1_pl_cs: pass rate 60%" in f for f in failures)
    assert any("partial: median_tokens" in f for f in failures)
    text = run.report(summary, failures, {"runner": "claude", "model": "claude-haiku-4-5", "date": "d"})
    assert "| example_1_pl_cs (example) | 5 | 60% |" in text and "Gates: FAILED" in text
