"""find / suggest / spec edit / ls through the CLI on a fake Wikimedia (no network)."""
import io
import json
from pathlib import Path

import pytest

from test_candidates import english_exams
from webfake import FakeWeb
from wikitrends import cli
from wikitrends import thresholds as T
from wikitrends.deadline import Deadline
from wikitrends.http import HttpClient
from wikifake import RoutingTransport


def run_cli(argv, tmp_path, web, *, deadline_seconds=None):
    transport = RoutingTransport(web.handle)

    def factory(deadline):
        if deadline_seconds is not None:
            deadline = Deadline(deadline_seconds)
        return HttpClient(transport, sleep=lambda s: None, deadline=deadline, rate_per_minute=None)

    stdout = io.StringIO()
    env = {"WIKITRENDS_HOME": str(tmp_path / "wd"), "WIKITRENDS_CACHE_DIR": str(tmp_path / "cache")}
    code = cli.run(argv, env=env, cwd=tmp_path, home=tmp_path, stdout=stdout, http_factory=factory, today="2026-09")
    return code, json.loads(stdout.getvalue()), transport


def suggest(tmp_path, web, *extra):
    return run_cli(["suggest", "--seed", "Q1", "--langs", "uk,pl", "--display-lang", "uk", *extra], tmp_path, web)


def test_find_leader_and_ambiguous(tmp_path):
    web = FakeWeb()
    web.entity("Q333", {"uk": "астрономія"}, {"uk": "Астрономія"})
    web.entity("Q308", {"uk": "Меркурій"}, {"uk": "Меркурій"})
    web.entity("Q47652", {"uk": "Меркурій"}, {"uk": "Меркурій (міфологія)"})
    web.search[("uk", "астрономія")] = ["Астрономія"]
    web.search[("uk", "Меркурій")] = ["Меркурій", "Меркурій (міфологія)"]

    code, out, _ = run_cli(["find", "астрономія", "--lang", "uk"], tmp_path, web)
    assert code == 0 and out["ok"] and out["leader"] == "Q333"
    assert "--seed Q333" in out["next_steps"][0] and str(cli.SCRIPT) in out["next_steps"][0]

    code, out, _ = run_cli(["find", "Меркурій", "--lang", "uk"], tmp_path, web)
    assert code == 0 and out["ok"] and out["status"] == "needs_user" and out["code"] == "ambiguous_topic"
    assert [c["qid"] for c in out["candidates"]] == ["Q308", "Q47652"]


def test_find_unknown_lang(tmp_path):
    code, out, _ = run_cli(["find", "x", "--lang", "ukr"], tmp_path, FakeWeb())
    assert code == 2 and out["code"] == "unknown_lang" and "uk" in out["similar"]


def test_suggest_creates_spec_with_draft(tmp_path):
    code, out, _ = suggest(tmp_path, english_exams())
    assert code == 0 and out["ok"]
    path = Path(out["spec"])
    assert path.is_absolute() and path == tmp_path / "wd" / "ielts" / "spec.json"
    saved = json.loads(path.read_text())
    assert saved["display_lang"] == "uk" and saved["langs"] == ["uk", "pl"] and saved["period"] == {"relative": "24m"}
    assert saved["topics"] == [{"id": "ielts", "label": "IELTS", "seeds": ["Q1"], "items": ["Q1", "Q2", "Q6"]}]
    assert [d["qid"] for d in out["draft"]] == ["Q1", "Q2", "Q6"]
    assert out["candidates"][0] == {"qid": "Q2", "label": "TOEFL", "score": 4.5, "langs": "uk,pl"}
    assert out["next_steps"][0].endswith(f"analyze {path}")


def test_suggest_topic_label_and_adding_to_a_spec(tmp_path):
    web = english_exams()
    _, first, _ = suggest(tmp_path, web, "--topic", "іспити з англійської")
    assert first["topic"] == {"id": "ispyty-z-anhliiskoyi", "label": "іспити з англійської"}
    code, out, _ = suggest(tmp_path, web, "--topic", "ще одна", "--spec", first["spec"])
    assert code == 0 and out["spec"] == first["spec"]
    assert [t["id"] for t in json.loads(Path(first["spec"]).read_text())["topics"]] == ["ispyty-z-anhliiskoyi", "shche-odna"]
    code, out, _ = run_cli(["suggest", "--seed", "Q1", "--langs", "uk,cs", "--display-lang", "uk", "--spec", first["spec"]],
                           tmp_path, web)
    assert code == 2 and out["code"] == "invalid_arguments"


@pytest.mark.parametrize(
    "argv, code_name",
    [
        (["suggest", "--seed", "Q1", "--display-lang", "uk"], "langs_required"),
        (["suggest", "--seed", "Q1", "--langs", "uk"], "display_lang_required"),
        (["suggest", "--seed", "x1", "--langs", "uk", "--display-lang", "uk"], "invalid_arguments"),
        (["suggest", "--seed", "Q1", "--langs", "uk,xx", "--display-lang", "uk"], "unknown_lang"),
    ],
)
def test_suggest_input_errors(tmp_path, argv, code_name):
    code, out, _ = run_cli(argv, tmp_path, english_exams())
    assert (code, out["ok"], out["code"]) == (2, False, code_name)


def test_suggest_budget_and_deadline(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "MAX_SUGGEST_REQUESTS", 5)
    code, out, transport = suggest(tmp_path, english_exams())
    assert code == 2 and out["code"] == "budget_exceeded" and transport.count() == 0
    code, out, _ = suggest(tmp_path, english_exams(), "--allow-large")
    assert code == 0
    monkeypatch.setattr(T, "MAX_SUGGEST_REQUESTS", 250)
    code, out, _ = run_cli(["suggest", "--seed", "Q1", "--langs", "uk,pl", "--display-lang", "uk"], tmp_path / "late",
                           english_exams(), deadline_seconds=0)
    assert code == 0 and out["partial"] is True
    assert out["next_steps"][0].endswith("suggest --seed Q1 --langs uk,pl --display-lang uk")
    assert not (tmp_path / "late" / "wd").exists() or not list((tmp_path / "late" / "wd").glob("*/spec.json"))


def test_spec_edit_operations(tmp_path):
    web = english_exams()
    _, created, _ = suggest(tmp_path, web)
    path = created["spec"]
    code, out, _ = run_cli(["spec", "edit", path, "--add", "Q9", "--drop", "Q6", "--add-lang", "cs",
                            "--criteria", "min_monthly_views=50000,min_growth_pct_yr=15"], tmp_path, web)
    assert code == 0 and out["langs"] == ["uk", "pl", "cs"] and out["criteria"] == {"min_monthly_views": 50000, "min_growth_pct_yr": 15}
    assert json.loads(Path(path).read_text())["topics"][0]["items"] == ["Q1", "Q2", "Q9"]
    assert out["next_steps"] == [f"python3 {cli.SCRIPT} analyze {path}"]

    code, out, _ = run_cli(["spec", "edit", path, "--add-topic", "екзамени", "--seed", "Q2"], tmp_path, web)
    assert code == 0 and len(out["topics"]) == 2
    code, out, _ = run_cli(["spec", "edit", path, "--add", "Q10"], tmp_path, web)
    assert (code, out["code"], out["topics"]) == (2, "topic_required", ["ielts", "ekzameny"])

    code, out, _ = run_cli(["spec", "edit", path, "--start", "2014-01"], tmp_path, web)
    assert code == 0 and out["period"] == {"start": "2015-07"}
    assert [w["code"] for w in out["warnings"]] == ["start_clipped"]
    code, out, _ = run_cli(["spec", "edit", path, "--refresh-window"], tmp_path, web)
    assert code == 2 and "--period" in out["hint"]
    code, out, _ = run_cli(["spec", "edit", path, "--period", "36m", "--start", "2023-01"], tmp_path, web)
    assert code == 2 and out["code"] == "invalid_arguments"
    code, out, _ = run_cli(["spec", "edit", path, "--period", "6m"], tmp_path, web)
    assert code == 2 and out["code"] == "period_too_short"
    code, out, _ = run_cli(["spec", "edit", path], tmp_path, web)
    assert code == 2 and out["code"] == "invalid_arguments"


def test_spec_edit_end_must_be_published(tmp_path):
    web = english_exams()
    _, created, _ = suggest(tmp_path, web)
    code, out, _ = run_cli(["spec", "edit", created["spec"], "--start", "2024-01", "--end", "2026-08"], tmp_path, web)
    assert code == 0 and out["period"] == {"start": "2024-01", "end": "2026-08"}
    code, out, _ = run_cli(["spec", "edit", created["spec"], "--start", "2024-01", "--end", "2026-09"], tmp_path, web)
    assert (code, out["code"], out["latest"]) == (2, "end_not_available", "2026-08")


def test_ls(tmp_path):
    web = english_exams()
    code, out, _ = run_cli(["ls"], tmp_path, web)
    assert code == 0 and out["specs"] == []
    _, created, _ = suggest(tmp_path, web)
    code, out, _ = run_cli(["ls"], tmp_path, web)
    assert [s["spec"] for s in out["specs"]] == [created["spec"]]
    assert out["specs"][0]["last_analysis"] is None
