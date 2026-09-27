"""Slice 2 through the CLI: spec edit --metric, data_revised in cells, breaks and seeds in result.json."""
import json

from test_cli_analyze import astronomy, growth, run_cli, write_spec


def test_metric_views_changes_the_trend_metric_and_the_texts(tmp_path):
    path = write_spec(tmp_path)
    web = astronomy()
    web.traffic["pl"] = growth(10_000_000, 50.0)  # the edition grows faster: pl's share falls while its views grow
    code, out, _ = run_cli(["spec", "edit", str(path), "--metric", "views", "--criteria", "min_growth_pct_yr=15"],
                           tmp_path, web)
    assert code == 0 and out["metric"] == "views" and json.loads(path.read_text())["options"]["metric"] == "views"
    code, out, _ = run_cli(["analyze", str(path)], tmp_path, web)
    assert code == 0
    result = json.loads((path.parent / "result.json").read_text())
    pl = next(c for c in result["topics"][0]["cells"] if c["lang"] == "pl")
    assert result["metric"] == "views" and pl["group"] == "promising" and pl["raw_growth_ci"] is not None
    assert pl["raw_growth_pct_yr"] < -5 and "metric_disagreement" in {r["code"] for r in pl["reasons"]}
    assert "ріст переглядів ≥ 15 %/рік" in out["answer_md"]
    assert "сирі перегляди статей кошика" in result["answer_md_full"]
    code, out, _ = run_cli(["spec", "edit", str(path), "--metric", "people"], tmp_path, web)
    assert code == 2 and out["code"] == "invalid_arguments"


def test_revised_language_cells_carry_data_revised(tmp_path):
    path = write_spec(tmp_path)
    web = astronomy()
    run_cli(["analyze", str(path)], tmp_path, web)
    web.traffic["pl"] = lambda m: 11_000_000 if m == "2025-03" else 10_000_000
    run_cli(["analyze", str(path)], tmp_path, web)
    cells = {c["lang"]: c for c in json.loads((path.parent / "result.json").read_text())["topics"][0]["cells"]}
    assert [r["severity"] for r in cells["pl"]["reasons"] if r["code"] == "data_revised"] == ["info"]
    assert not any(r["code"] == "data_revised" for r in cells["cs"]["reasons"])


def test_result_keeps_break_checks_and_the_seed_status(tmp_path):
    path = write_spec(tmp_path)
    run_cli(["analyze", str(path)], tmp_path, astronomy())
    cells = json.loads((path.parent / "result.json").read_text())["topics"][0]["cells"]
    for cell in cells:  # the window 2024-09 .. 2026-08 crosses the 2025 bot-detection break
        assert [b["id"] for b in cell["breaks"]] == ["bot_detection_2025"]
        assert cell["breaks"][0]["status"] in ("significant", "not_significant", "not_checked")
        assert cell["seed_divergence"] in ("agrees", "diverges", "not_checked")
