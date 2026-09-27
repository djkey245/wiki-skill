"""stdout and result.json against references/output-schema.json (testing §4)."""
import json
from pathlib import Path

import pytest

from test_cli_analyze import astronomy, run_cli, write_spec
from wikitrends import errors
from wikitrends.deadline import Deadline

jsonschema = pytest.importorskip("jsonschema")

SCHEMA = json.loads((Path(__file__).resolve().parents[1] / "references" / "output-schema.json").read_text())


def check(instance, definition, *, stdout=True):
    validator = jsonschema.Draft202012Validator({"$ref": f"#/$defs/{definition}", "$defs": SCHEMA["$defs"]})
    validator.validate(instance)
    if stdout:  # every stdout object also matches the top level: ok, schema_version, warnings
        jsonschema.Draft202012Validator(SCHEMA).validate(instance)


def test_schema_is_valid():
    jsonschema.Draft202012Validator.check_schema(SCHEMA)


def test_analyze_stdout_and_result_file(tmp_path):
    path = write_spec(tmp_path)
    code, out, _ = run_cli(["analyze", str(path)], tmp_path, astronomy())
    assert code == 0
    check(out, "analyze_stdout")
    check_result = json.loads(Path(out["result"]).read_text())
    check({**check_result, "ok": True, "schema_version": 1}, "result")


def test_quick_stdout_partial_and_needs_user(tmp_path):
    code, out, _ = run_cli(["quick", "астрономія", "--seed", "Q1", "--langs", "uk,pl,cs", "--display-lang", "uk"],
                           tmp_path, astronomy())
    check(out, "analyze_stdout")
    path = write_spec(tmp_path / "p")
    code, out, _ = run_cli(["analyze", str(path)], tmp_path / "p", astronomy(), deadline=Deadline(0))
    check(out, "partial")
    web = astronomy()
    web.entity("Q308", {"uk": "Меркурій"}, {"uk": "Меркурій"})
    web.entity("Q47652", {"uk": "Меркурій"}, {"uk": "Меркурій (міфологія)"})
    web.search[("uk", "Меркурій")] = ["Меркурій", "Меркурій (міфологія)"]
    code, out, _ = run_cli(["find", "Меркурій", "--lang", "uk"], tmp_path, web)
    check(out, "needs_user")


@pytest.mark.parametrize("code", sorted(errors.REGISTRY))
def test_every_registry_code_matches_the_schema(code):
    info = errors.REGISTRY[code]
    if info.status == "needs_user":
        payload = errors.needs_user(code, "message")
        check({**payload, "schema_version": 1}, "needs_user")
        assert payload["ok"] is True and info.exit_code == 0
    else:
        payload = errors.WikitrendsError(code, "message", hint="hint").to_payload()
        check({**payload, "schema_version": 1}, "error")
        assert payload["ok"] is False and info.exit_code in (1, 2, 3, 4, 5, 6)


def test_every_warning_code_matches_the_schema():
    for code in errors.WARNING_CODES:
        check(errors.make_warning(code, "message"), "warning", stdout=False)


def test_reason_codes_of_the_schema_match_the_checks():
    from wikitrends import confidence

    codes = set(SCHEMA["$defs"]["reason"]["properties"]["code"]["enum"])
    assert codes == set(confidence.SEVERITY) | {"low_volume"}
