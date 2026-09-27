"""Scenarios, both runners and the judge of the evals, on fakes (no model, no network)."""
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from test_eval_checks import FIXTURE, result
from wteval import judge, scenarios
from wteval.runners import (SESSION_VARS, ClaudeCodeRunner, OpenRouterRunner, RateLimited, clean_env,
                            prepare_project)

SKILL_DIR = Path(__file__).resolve().parents[1]


def test_scenarios_are_valid():
    items = scenarios.load_all()
    ids = {s["id"] for s in items}
    assert {"example_1_pl_cs", "example_2_intent", "example_3_audiences", "partial", "trigger_irrelevant"} <= ids
    assert {s["id"] for s in items if s["example"]} == {"example_1_pl_cs", "example_2_intent", "example_3_audiences"}
    assert {s["id"] for s in items if s["openrouter"]} == {s["id"] for s in items if s["example"]}
    with pytest.raises(ValueError):
        scenarios.validate({"id": "x", "title": "x", "steps": [{"user": "a", "checks": [{"check": "nope"}]}]})


def fake_claude(tmp_path):
    """An executable that records its argv and environment and prints the recorded stream."""
    script = tmp_path / "claude"
    script.write_text(f"#!{sys.executable}\n"
                      "import json, os, sys\n"
                      f"open({str(tmp_path / 'calls.jsonl')!r}, 'a').write(json.dumps({{'argv': sys.argv[1:], "
                      "'env': {k: v for k, v in os.environ.items() if k.startswith(('WIKITRENDS', 'CLAUDE'))}, 'cwd': os.getcwd()}) + '\\n')\n"
                      f"sys.stdout.write(open({str(FIXTURE)!r}).read())\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_claude_runner_resumes_and_cleans_the_environment(tmp_path):
    project, installed = prepare_project(SKILL_DIR, tmp_path / "root")
    assert (installed / "SKILL.md").exists() and not (installed / "tests").exists()
    env = clean_env({"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "outer", "PATH": os.environ["PATH"]},
                    {"WIKITRENDS_HOME": "/wd"})
    session = ClaudeCodeRunner("claude-haiku-4-5", executable=str(fake_claude(tmp_path))).session(project, env, installed)
    first, second = session.send("one"), session.send("two")
    assert first.answer == second.answer == "done"
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert "--resume" not in calls[0]["argv"] and calls[1]["argv"][-2:] == ["--resume", "00000000-0000-4000-8000-000000000001"]
    assert all(v not in calls[0]["env"] for v in SESSION_VARS) and calls[0]["env"]["WIKITRENDS_HOME"] == "/wd"
    assert calls[0]["cwd"] == str(project) and calls[0]["argv"][calls[0]["argv"].index("--model") + 1] == "claude-haiku-4-5"


class FakeOpenRouter:
    def __init__(self, replies, models=None):
        self.replies, self.sent = list(replies), []
        self.models = models or [{"id": "vendor/m:free", "supported_parameters": ["tools"], "context_length": 131072}]

    def __call__(self, method, url, headers, body):
        if method == "GET":
            return 200, {}, json.dumps({"data": self.models}).encode()
        self.sent.append(json.loads(body))
        status, headers, data = self.replies.pop(0)
        return status, headers, json.dumps(data).encode()


def message(content=None, tool=None):
    msg = {"role": "assistant", "content": content}
    if tool:
        msg["tool_calls"] = [{"id": "c1", "type": "function", "function": {"name": "bash", "arguments": json.dumps({"command": tool})}}]
    return {"choices": [{"message": msg}], "usage": {"prompt_tokens": 100, "completion_tokens": 10}}


def test_openrouter_runner_tool_loop_and_limits(tmp_path):
    http = FakeOpenRouter([(200, {}, message(tool="echo hi")), (200, {}, message("готово"))])
    sleeps = []
    runner = OpenRouterRunner("vendor/m:free", "key", http=http, sleep=sleeps.append, clock=lambda: 0.0,
                              execute=lambda c, p, e: (f"ran {c}", False))
    assert runner.check_model()["id"] == "vendor/m:free"
    (tmp_path / "SKILL.md").write_text("# skill")
    t = runner.session(tmp_path, {}, tmp_path).send("питання")
    assert t.answer == "готово" and [c.command for c in t.calls] == ["echo hi"] and t.tokens == 220
    assert http.sent[1]["messages"][-1] == {"role": "tool", "tool_call_id": "c1", "content": "ran echo hi"}
    assert "# skill" in http.sent[0]["messages"][0]["content"] and http.sent[0]["tools"][0]["function"]["name"] == "bash"
    with pytest.raises(ValueError):
        OpenRouterRunner("openrouter/free", "key")
    with pytest.raises(ValueError):
        OpenRouterRunner("vendor/m:free", "k", http=FakeOpenRouter([], models=[{"id": "vendor/m:free", "supported_parameters": [], "context_length": 1}])).check_model()


def test_openrouter_rate_limits(tmp_path):
    (tmp_path / "SKILL.md").write_text("# skill")
    daily = FakeOpenRouter([(429, {"Retry-After": "3600"}, {"error": "free-models-per-day"})])
    runner = OpenRouterRunner("vendor/m:free", "k", http=daily, sleep=lambda s: None)
    with pytest.raises(RateLimited):
        runner.session(tmp_path, {}, tmp_path).send("q")
    sleeps = []
    brief = FakeOpenRouter([(429, {"Retry-After": "7"}, {}), (200, {}, message("ok"))])
    runner = OpenRouterRunner("vendor/m:free", "k", http=brief, sleep=sleeps.append, clock=lambda: 0.0)
    assert runner.session(tmp_path, {}, tmp_path).send("q").answer == "ok" and 7.0 in sleeps
    runner._starts = [0.0] * 20  # 20 requests in the last minute
    runner._throttle()
    assert sleeps[-1] == 60


def test_judge_prompt_and_parse():
    r = result()
    captured = {}

    def call(text):
        captured["prompt"] = text
        return "Here: " + json.dumps({i: {"pass": i != "limits", "why": "x"} for i in judge.ITEMS})

    verdict = judge.judge(call, "питання", "відповідь", r)
    assert verdict["passed"] is False and verdict["items"]["limits"]["pass"] is False
    assert "not_significant_pairs" in captured["prompt"] and "exaggeration" in captured["prompt"]
    with pytest.raises(ValueError):
        judge.parse('{"exaggeration": {"pass": true}}')
