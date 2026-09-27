"""Environment hooks the evals rely on: WIKITRENDS_HTTP replay, WIKITRENDS_TODAY, WIKITRENDS_REQUEST_CAP."""
import io
import json
import shlex
from pathlib import Path

from test_cli_analyze import TODAY, astronomy
from wikifake import RoutingTransport
from wikitrends import cli
from wikitrends.http import HttpClient, RecordingTransport

QUICK = ["quick", "астрономія", "--seed", "Q1", "--langs", "uk,pl,cs", "--display-lang", "uk"]


def record(corpus, tmp_path):
    """A full quick on the fake Wikimedia, recorded into `corpus`."""
    transport = RecordingTransport(RoutingTransport(astronomy().handle), corpus)
    stdout = io.StringIO()
    env = {"WIKITRENDS_HOME": str(tmp_path / "rec"), "WIKITRENDS_CACHE_DIR": str(tmp_path / "rec-cache")}
    cli.run(QUICK, env=env, cwd=tmp_path, home=tmp_path, stdout=stdout,
            http_factory=lambda d: HttpClient(transport, sleep=lambda s: None, deadline=d, rate_per_minute=None),
            today=TODAY)
    assert json.loads(stdout.getvalue())["ok"]


def run(argv, tmp_path, corpus, **extra):
    env = {"WIKITRENDS_HOME": str(tmp_path / "wd"), "WIKITRENDS_CACHE_DIR": str(tmp_path / "cache"),
           "WIKITRENDS_HTTP": f"replay:{corpus}", "WIKITRENDS_TODAY": TODAY, **extra}
    stdout = io.StringIO()
    code = cli.run(argv, env=env, cwd=tmp_path, home=tmp_path, stdout=stdout)
    return code, json.loads(stdout.getvalue())


def test_replay_and_pinned_month_reproduce_a_recorded_quick(tmp_path):
    record(tmp_path / "corpus", tmp_path)
    code, out = run(QUICK, tmp_path, tmp_path / "corpus")
    assert code == 0 and out["ok"] and "partial" not in out, out
    assert json.loads(Path(out["spec"]).read_text())["period"]["end"] == "2026-08"


def test_request_cap_gives_partial_and_next_steps_finish(tmp_path):
    """What an agent does with partial: run next_steps until done. quick is repeated while no
    spec is written, then analyze of the written spec; no -2 duplicate appears."""
    record(tmp_path / "corpus", tmp_path)
    argv, partials = QUICK, 0
    for _ in range(12):
        code, out = run(argv, tmp_path, tmp_path / "corpus", WIKITRENDS_REQUEST_CAP="15")  # > the uncached overhead of analyze: 3 requests a language
        assert code == 0 and out["ok"], out
        if not out.get("partial"):
            break
        partials += 1
        argv = shlex.split(out["next_steps"][0])[2:]  # drop "python3 <script>"
        if "spec" in out:
            assert argv == ["analyze", out["spec"]]
    assert partials >= 2 and "partial" not in out and Path(out["result"]).exists()
    assert len(list((tmp_path / "wd").glob("*/spec.json"))) == 1
