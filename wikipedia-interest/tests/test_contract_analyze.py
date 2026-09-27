"""quick -> analyze on recorded live answers (tests/fixtures/quick_*, no network)."""
import io
import json
from pathlib import Path

import pytest

from record_fixtures import FIXTURES, QUICK_RUNS, QUICK_TODAY
from wikitrends import cli
from wikitrends import thresholds as T
from wikitrends.http import HttpClient, ReplayTransport


def replay_quick(name, tmp_path):
    directory = FIXTURES / name
    if not directory.exists():
        pytest.fail(f"no fixtures for {name!r}: run tests/record_fixtures.py {name} (plan 06, Task 7)")
    http = HttpClient(ReplayTransport(directory), sleep=lambda s: None, rate_per_minute=None)
    stdout = io.StringIO()
    env = {"WIKITRENDS_HOME": str(tmp_path / "wd"), "WIKITRENDS_CACHE_DIR": str(tmp_path / "cache")}
    code = cli.run(QUICK_RUNS[name], env=env, cwd=tmp_path, home=tmp_path, stdout=stdout,
                   http_factory=lambda deadline: http, today=QUICK_TODAY)
    return code, stdout.getvalue(), http


@pytest.mark.parametrize("name", sorted(QUICK_RUNS))
def test_quick_on_live_data(name, tmp_path):  # П10, П11, П12
    code, text, http = replay_quick(name, tmp_path)
    out = json.loads(text)
    assert code == 0 and out["ok"] and "partial" not in out, out
    assert http.requests_made <= T.MAX_SUGGEST_REQUESTS + T.MAX_REQUESTS  # П10
    result = json.loads(Path(out["result"]).read_text())
    assert all(x["status"] == "ok" for x in result["langs"])  # П11
    topic = result["topics"][0]
    assert all(r["text"] for c in topic["cells"] for r in c["reasons"])
    assert out["answer_md"].startswith(f"**{topic['headline']}**")
    assert len(text) < 7000  # П12: stdout within ~2k tokens (R13)
    if name == "quick_pl_cs":
        assert not topic["matrix"] and (topic["pairs"] or topic["pairs_skipped"])
    if name == "quick_five":
        assert topic["matrix"] and topic["recommendations"] is not None
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((Path(__file__).resolve().parents[1] / "references" / "output-schema.json").read_text())
    jsonschema.Draft202012Validator({"$ref": "#/$defs/analyze_stdout", "$defs": schema["$defs"]}).validate(out)
