"""Live smoke (testing §9): the real Wikimedia APIs. Run explicitly:

    WIKITRENDS_CONTACT=you@example.com uv run pytest -m live -v
"""
import io
import json
import os
from pathlib import Path

import pytest

from wikitrends import cli

pytestmark = pytest.mark.live


def run(argv, tmp_path):
    env = {**os.environ, "WIKITRENDS_HOME": str(tmp_path / "wd"), "WIKITRENDS_CACHE_DIR": str(tmp_path / "cache")}
    env.pop("WIKITRENDS_HTTP", None)
    for _ in range(4):  # partial: continue with next_steps, as the agent would
        stdout = io.StringIO()
        code = cli.run(argv, env=env, cwd=tmp_path, home=tmp_path, stdout=stdout)
        out = json.loads(stdout.getvalue())
        if not out.get("partial"):
            return code, out
        argv = out["next_steps"][0].split()[2:]
    return code, out


def test_quick_live(tmp_path):
    code, out = run(["quick", "астрономія", "--lang", "uk", "--langs", "uk,pl,cs", "--display-lang", "uk"], tmp_path)
    assert code == 0 and out["ok"], out
    assert out["answer_md"].startswith("**") and Path(out["result"]).exists()


def test_find_suggest_analyze_report_live(tmp_path):
    code, out = run(["find", "astronomia", "--lang", "pl"], tmp_path)
    assert code == 0 and out["leader"] == "Q333"
    code, out = run(["suggest", "--seed", "Q333", "--langs", "pl,cs", "--display-lang", "uk"], tmp_path)
    assert code == 0 and out["draft"]
    spec_path = out["spec"]
    code, out = run(["analyze", spec_path], tmp_path)
    assert code == 0 and out["ok"] and out["next_steps"][0].startswith("uv run ")
    render = pytest.importorskip("wikitrends.render")
    stdout = io.StringIO()
    assert render.run([spec_path], stdout) == 0
    files = json.loads(stdout.getvalue())["files"]
    assert render.pages(Path(files["pdf"]).read_bytes()) == 1
