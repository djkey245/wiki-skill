"""report.py / render.py on results made by the CLI on a fake Wikimedia (no network)."""
import io
import json
from pathlib import Path

import pytest

from test_analysis import LABELS, WINDOW, many_cells
from test_cli_analyze import astronomy, run_cli, write_spec
from wikitrends import analysis, cli, templates

matplotlib = pytest.importorskip("matplotlib")
from wikitrends import render  # noqa: E402

REPORT = Path(__file__).resolve().parents[1] / "scripts" / "report.py"


def analysed(tmp_path, display_lang="uk"):
    path = write_spec(tmp_path)
    data = json.loads(path.read_text())
    data["display_lang"] = display_lang
    path.write_text(json.dumps(data))
    code, out, _ = run_cli(["analyze", str(path)], tmp_path, astronomy())
    assert code == 0, out
    return path, out


def report(argv):
    stdout = io.StringIO()
    code = render.run(argv, stdout)
    return code, json.loads(stdout.getvalue())


def test_analyze_points_to_the_report(tmp_path):
    path, out = analysed(tmp_path)
    assert out["next_steps"] == [f"uv run {cli.REPORT_SCRIPT} {path}"]


def test_report_files_and_text(tmp_path):
    path, _ = analysed(tmp_path)
    code, out = report([str(path)])
    assert code == 0 and out["ok"] and out["out"] == str(path.parent / "report")
    files = out["files"]
    assert [Path(p).name for p in files["png"]] == ["index-astronomy.png"]
    result = json.loads((path.parent / "result.json").read_text())
    md = Path(files["md"]).read_text()
    assert md.startswith(result["answer_md_full"] + "\n\n### Графіки\n![астрономія](index-astronomy.png)")
    pdf = Path(files["pdf"]).read_bytes()
    assert pdf.startswith(b"%PDF") and render.pages(pdf) == 1
    assert Path(files["png"][0]).read_bytes().startswith(b"\x89PNG")
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((REPORT.parents[1] / "references" / "output-schema.json").read_text())
    jsonschema.Draft202012Validator({"$ref": "#/$defs/report_stdout", "$defs": schema["$defs"]}).validate(out)


def test_same_result_same_bytes(tmp_path):
    path, _ = analysed(tmp_path)
    report([str(path), "--out", str(tmp_path / "a")])
    result = path.parent / "result.json"
    data = json.loads(result.read_text())
    result.write_text(json.dumps(data, sort_keys=True))  # another key order
    report([str(path), "--out", str(tmp_path / "b")])
    for name in ("report.pdf", "report.md", "index-astronomy.png"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes(), name
    assert b"CreationDate" not in (tmp_path / "a" / "report.pdf").read_bytes()


def test_filters_formats_and_out(tmp_path):
    path, _ = analysed(tmp_path)
    code, out = report([str(path), "--lang", "pl", "--format", "md", "--out", str(tmp_path / "pl")])
    assert code == 0 and list(out["files"]) == ["md"]
    md = Path(out["files"]["md"]).read_text()
    assert "| pl |" in md and "| cs |" not in md and "| uk |" not in md
    code, out = report([str(path), "--topic", "nope"])
    assert code == 2 and out["code"] == "invalid_arguments" and "astronomy" in out["hint"]
    code, out = report([str(path), "--format", "docx"])
    assert code == 2 and out["code"] == "invalid_arguments"


def test_english_text_for_languages_without_templates(tmp_path):
    path, _ = analysed(tmp_path, display_lang="pl")
    code, out = report([str(path), "--format", "md"])
    assert out["text_lang"] == "en"
    assert "growth confirmed" in Path(out["files"]["md"]).read_text()


def test_missing_result_says_how_to_get_it(tmp_path):
    path = write_spec(tmp_path)
    code, out = report([str(path)])
    assert code == 2 and out["code"] == "invalid_arguments" and "analyze" in out["hint"]


def test_many_cells_fit_one_page(tmp_path):
    spec_data, data = many_cells()
    result = analysis.evaluate(spec_data, WINDOW, data, {}, LABELS)
    result.update(spec="x", result="x", latest_month="2024-12", changes=[], warnings=[], requests={"network": 0},
                  answer_md_full=templates.answer_md(result, full=True))
    target = tmp_path / "many" / "result.json"
    target.parent.mkdir()
    target.write_text(json.dumps(result))
    code, out = report([str(target), "--format", "pdf"])
    assert code == 0
    assert render.pages(Path(out["files"]["pdf"]).read_bytes()) == 1
    size, lines = render.fit_text(result, 0.4)
    width = int(125 * 7 / size)
    recommendations, _, limits = render.page_text(result, short=True)  # the full block does not fit here
    assert size in (7.0, 6.5, 6.0, 5.5, 5.0) and len(lines) <= int(0.4 / render.line_height(size))
    assert lines[: len(render._wrap(recommendations, width))] == render._wrap(recommendations, width)
    assert lines[-3:] == render._wrap(limits, width)[-3:]
