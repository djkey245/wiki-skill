"""criteria.size_axis: the matrix splits by level_ppm instead of monthly_views (recommendations §3–4)."""
import json

from test_cli_analyze import astronomy, run_cli, write_spec


def cells(path):
    result = json.loads((path.parent / "result.json").read_text())
    return result, {c["lang"]: c for c in result["topics"][0]["cells"]}


def test_level_ppm_axis_changes_the_size_split_not_the_groups(tmp_path):
    path = write_spec(tmp_path)
    web = astronomy()
    web.traffic["uk"] = lambda m: 1_000_000  # a small edition: few views, a large share
    run_cli(["analyze", str(path)], tmp_path, web)
    result, by_views = cells(path)
    assert result["size_axis"] == "monthly_views" and by_views["uk"]["size_big"] is False
    code, out, _ = run_cli(["spec", "edit", str(path), "--criteria", "size_axis=level_ppm"], tmp_path, web)
    assert code == 0 and out["criteria"] == {"size_axis": "level_ppm"}
    code, out, _ = run_cli(["analyze", str(path)], tmp_path, web)
    result, by_share = cells(path)
    assert result["size_axis"] == "level_ppm" and result["criteria"] is None
    assert by_share["uk"]["size_big"] is True
    assert {k: c["group"] for k, c in by_share.items()} == {k: c["group"] for k, c in by_views.items()}
    assert "висока частка" in out["answer_md"] and "Вісь розміру матриці: частка" in out["answer_md"]
    assert all(c["criteria"] == {} for c in by_share.values())
