"""Repeated requests: reused_spec, existing_spec, --new, newer_data_available (agent contract §2, testing §4)."""
import json

from test_cli_analyze import astronomy, is_article, run_cli, write_spec
from wikitrends import spec

QUICK = ["quick", "астрономія", "--seed", "Q1", "--langs", "uk,pl,cs", "--display-lang", "uk"]


def spec_dirs(tmp_path):
    return sorted(p.parent.name for p in (tmp_path / "wd").glob("*/spec.json"))


def test_repeated_quick_reuses_the_spec_and_the_cache(tmp_path):
    web = astronomy()
    code, first, _ = run_cli(QUICK, tmp_path, web)
    assert code == 0 and "reused_spec" not in first
    code, again, transport = run_cli(QUICK, tmp_path, web)
    assert code == 0 and again["reused_spec"] is True and again["spec"] == first["spec"]
    assert transport.count(lambda r: is_article(r)) == 0  # only aggregate is requested again (data §5)
    assert len(spec_dirs(tmp_path)) == 1


def test_other_languages_stop_with_existing_spec_and_new_makes_another(tmp_path):
    web = astronomy()
    _, first, _ = run_cli(QUICK, tmp_path, web)
    before = json.loads(open(first["spec"]).read())
    code, out, _ = run_cli(QUICK[:4] + ["--langs", "pl,cs"] + QUICK[6:], tmp_path, web)
    assert code == 0 and out["status"] == "needs_user" and out["code"] == "existing_spec"
    assert out["spec"] == first["spec"] and json.loads(open(first["spec"]).read()) == before
    assert "--drop-lang uk" in out["next_steps"][0] and out["next_steps"][1].endswith("--new")
    code, out, _ = run_cli(QUICK[:4] + ["--langs", "pl,cs"] + QUICK[6:] + ["--new"], tmp_path, web)
    assert code == 0 and out["spec"] != first["spec"] and len(spec_dirs(tmp_path)) == 2


def test_another_period_is_existing_spec_too(tmp_path):
    web = astronomy()
    run_cli(QUICK, tmp_path, web)
    code, out, _ = run_cli(QUICK + ["--period", "36m"], tmp_path, web)
    assert out["code"] == "existing_spec" and "--period 36m" in out["next_steps"][0]


def test_specs_with_two_topics_never_match(tmp_path):
    path = write_spec(tmp_path)
    data = json.loads(path.read_text())
    data["topics"].append({"id": "b", "label": "b", "seeds": ["Q2"], "items": ["Q2"]})
    path.write_text(json.dumps(data))
    code, out, _ = run_cli(QUICK, tmp_path, astronomy())
    assert code == 0 and "reused_spec" not in out and out["spec"] != str(path.resolve())


def test_suggest_reuses_or_stops(tmp_path):
    web = astronomy()
    _, first, _ = run_cli(QUICK, tmp_path, web)
    args = ["suggest", "--seed", "Q1", "--display-lang", "uk"]
    code, out, _ = run_cli(args + ["--langs", "uk,pl,cs"], tmp_path, web)
    assert out["reused_spec"] is True and out["spec"] == first["spec"] and "analyze" in out["next_steps"][0]
    code, out, _ = run_cli(args + ["--langs", "uk,pl,cs,sk"], tmp_path, web)
    assert out["code"] == "existing_spec" and "--add-lang sk" in out["next_steps"][0]


def test_newer_data_only_for_relative_periods(tmp_path):
    path = write_spec(tmp_path)
    data = json.loads(path.read_text())
    data["period"] = {"relative": "24m", "start": "2024-06", "end": "2026-05"}  # published up to 2026-08
    path.write_text(json.dumps(data))
    code, out, _ = run_cli(["analyze", str(path)], tmp_path, astronomy())
    warning = next(w for w in out["warnings"] if w["code"] == "newer_data_available")
    assert warning["months"] == 3 and out["period"]["end"] == "2026-05"  # the stored window is kept
    assert out["next_steps"][1].endswith("--refresh-window")
    data["period"] = {"start": "2024-06", "end": "2026-05"}
    path.write_text(json.dumps(data))
    code, out, _ = run_cli(["analyze", str(path)], tmp_path, astronomy())
    assert not any(w["code"] == "newer_data_available" for w in out.get("warnings", []))


def test_same_period_rules():
    assert spec.same_period({"relative": "24m", "start": "2024-09", "end": "2026-08"}, None)
    assert not spec.same_period({"relative": "36m"}, None)
    assert spec.same_period({"start": "2023-01", "end": "2024-12"}, {"start": "2023-01", "end": "2024-12"})
    assert not spec.same_period({"relative": "24m"}, {"start": "2023-01"})
    assert spec.newer_months({"relative": "24m", "end": "2026-05"}, "2026-08") == 3
    assert spec.newer_months({"start": "2024-01", "end": "2026-05"}, "2026-08") == 0
