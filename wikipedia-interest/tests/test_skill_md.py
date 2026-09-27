"""SKILL.md and references/* stay consistent with the code (agent contract §4)."""
import json
import re
from pathlib import Path

import pytest

from wikitrends import cli, confidence, errors, spec
from wikitrends import thresholds as T

ROOT = Path(__file__).resolve().parents[1]
SKILL = (ROOT / "SKILL.md").read_text(encoding="utf-8") if (ROOT / "SKILL.md").exists() else ""
COMMANDS = ("quick", "find", "suggest", "spec edit", "analyze", "ls")
# codes where the agent must do something other than "follow the hint" (architecture §3 registry)
SPECIAL = ("ambiguous_topic", "network_blocked", "network_unavailable", "report_deps_missing", "internal_error",
           "workdir_fallback", "partial")
SLICE_2 = ("existing_spec", "newer_data_available", "--metric", "--refresh-window", "reused_spec", "size_axis")


def frontmatter(text):
    """The YAML subset SKILL.md uses: `key: value` and folded `key: >-` blocks."""
    match = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert match, "no frontmatter"
    out, key = {}, None
    for line in match.group(1).splitlines():
        if line.startswith("  ") and key:
            out[key] = (out[key] + " " + line.strip()).strip()
        else:
            key, _, value = line.partition(":")
            out[key] = "" if value.strip() == ">-" else value.strip()
    return out, text[match.end():]


def test_frontmatter():
    meta, body = frontmatter(SKILL)
    assert set(meta) == {"name", "description", "compatibility"}
    assert meta["name"] == ROOT.name == "wikipedia-interest"
    assert 0 < len(meta["description"]) <= 1024 and len(meta["compatibility"]) <= 500
    for phrase in ("Вікіпедія", "перегляди", "інтерес до теми", "мовний розділ", "Wikipedia pageviews"):
        assert phrase in meta["description"], phrase
    assert len(body.strip().splitlines()) <= 150


def subparsers():
    parser = cli.build_parser()
    choices = parser._subparsers._group_actions[0].choices
    edit = choices["spec"]._subparsers._group_actions[0].choices["edit"]
    return {**{name: p for name, p in choices.items() if name != "spec"}, "spec edit": edit}


def command_lines():
    """Every command written in SKILL.md: code spans and lines of code blocks."""
    spans = re.findall(r"`([^`\n]+)`", SKILL)
    blocks = [line for block in re.findall(r"```\n(.*?)```", SKILL, re.S) for line in block.splitlines()]
    out, previous = [], None
    for line in spans + blocks:
        stripped = line.strip()
        if previous is not None and stripped.startswith("["):  # continuation of the previous command
            out[-1] = (previous, out[-1][1] + " " + stripped)
            continue
        for name in COMMANDS:
            if stripped.startswith(name + " ") or stripped == name:
                out.append((name, stripped))
                previous = name
                break
        else:
            previous = None
    return out


def test_every_command_and_flag_exists():
    parsers = subparsers()
    found = command_lines()
    assert {name for name, _ in found} == set(COMMANDS)
    for name, line in found:
        options = {o for a in parsers[name]._actions for o in a.option_strings}
        for flag in re.findall(r"(?<![\w-])--[a-z][a-z-]*", line):
            assert flag in options, f"{name}: {flag}"


def test_report_flags_exist():
    render = pytest.importorskip("wikitrends.render")
    options = {o for a in render.build_parser()._actions for o in a.option_strings}
    lines = [x for x in re.findall(r"`([^`\n]*report\.py[^`\n]*)`", SKILL) + SKILL.splitlines() if "report.py" in x]
    assert lines
    for line in lines:
        for flag in re.findall(r"(?<![\w-])--[a-z][a-z-]*", line):
            assert flag in options, flag


def test_codes_with_their_own_action_are_covered():
    for code in SPECIAL:
        assert code in SKILL, code
        assert code in errors.REGISTRY or code in errors.WARNING_CODES or code == "partial"
    for branch in SLICE_2:  # the slice-2 tree has its branches (overview §3)
        assert branch in SKILL, branch


def test_hard_rules_are_there():
    for phrase in ("not a market", "views are not people", "Never claim that a trend will continue",
                   "Never edit spec", "difference_not_significant", "not confirmed", "Never compute numbers"):
        assert phrase in SKILL, phrase


def test_references_exist_and_are_english():
    for name in re.findall(r"`references/([\w.-]+)`", SKILL):
        path = ROOT / "references" / name
        assert path.exists(), name
        if path.suffix == ".md":
            assert not re.search("[а-яіїєґ]{4,}", re.sub(r"`[^`]*`|```.*?```", "", path.read_text(), flags=re.S)), name


def test_every_reason_is_explained():
    text = (ROOT / "references" / "reasons.md").read_text()
    headings = set(re.findall(r"^## `(\w+)`", text, re.M))
    assert set(confidence.SEVERITY) | {"low_volume", "difference_not_significant"} <= headings


def test_every_threshold_is_documented():
    text = (ROOT / "references" / "methodology.md").read_text()
    names = [n for n in vars(T) if n.isupper()]
    missing = [n for n in names if f"`{n}`" not in text]
    assert not missing, missing


def test_spec_format_example_is_valid_and_edits_parse():
    text = (ROOT / "references" / "spec-format.md").read_text()
    example = json.loads(re.search(r"```json\n(.*?)```", text, re.S).group(1))
    spec.validate(example)
    options = {o for a in subparsers()["spec edit"]._actions for o in a.option_strings}
    edits = [line for line in re.search(r"## Editing\n\n```\n(.*?)```", text, re.S).group(1).splitlines() if line]
    for line in edits:
        assert line.startswith("spec edit <spec>")
        for flag in re.findall(r"(?<![\w-])--[a-z][a-z-]*", line.split("#")[0]):
            assert flag in options, flag

