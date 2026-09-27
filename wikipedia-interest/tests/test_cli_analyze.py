"""analyze / quick through the CLI on a fake Wikimedia (no network)."""
import io
import json
import math
from pathlib import Path

from webfake import FakeWeb
from wikitrends import cli, months
from wikitrends import thresholds as T
from wikitrends.deadline import Deadline
from wikitrends.http import HttpClient
from wikifake import RoutingTransport

TODAY = "2026-09"  # published up to 2026-08: the window is 2024-09..2026-08


def run_cli(argv, tmp_path, web, *, deadline=None):
    transport = RoutingTransport(web.handle)

    def factory(default):
        return HttpClient(transport, sleep=lambda s: None, deadline=deadline or default, rate_per_minute=None)

    stdout = io.StringIO()
    env = {"WIKITRENDS_HOME": str(tmp_path / "wd"), "WIKITRENDS_CACHE_DIR": str(tmp_path / "cache")}
    code = cli.run(argv, env=env, cwd=tmp_path, home=tmp_path, stdout=stdout, http_factory=factory, today=TODAY)
    return code, json.loads(stdout.getvalue()), transport


def growth(level, pct_yr, wobble=0.0):
    """Monthly views growing pct_yr %/yr from 2020-01 (with a small deterministic wobble)."""
    def views(month):
        t = months.diff(month, "2020-01")
        return int(level * (1 + pct_yr / 100) ** (t / 12) * (1 + wobble * math.sin(t * 2.1)))
    return views


def astronomy():
    """Q1..Q3 in uk, pl, cs; pl grows 30 %/yr, cs and uk are flat and noisy."""
    web = FakeWeb()
    titles = {"Q1": "Astronomia", "Q2": "Gwiazda", "Q3": "Galaktyka"}
    for q, t in titles.items():
        web.entity(q, {"uk": f"укр {t}", "en": t}, {"uk": f"uk {t}", "pl": t, "cs": f"cs {t}"})
        web.views[("pl", t)] = growth(5000, 30.0, 0.03)
        web.views[("cs", f"cs {t}")] = growth(5000, 0.0, 0.4)
        web.views[("uk", f"uk {t}")] = growth(3000, 0.0, 0.4)
    web.search[("uk", "астрономія")] = ["uk Astronomia"]
    return web


def write_spec(tmp_path, langs=("uk", "pl", "cs"), items=("Q1", "Q2", "Q3"), seeds=("Q1",)):
    path = tmp_path / "wd" / "astronomy" / "spec.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "version": 1, "display_lang": "uk", "langs": list(langs), "period": {"relative": "24m"},
        "options": {"include_redirects": True, "max_redirects": 10, "metric": "share"},
        "topics": [{"id": "astronomy", "label": "астрономія", "seeds": list(seeds), "items": list(items)}],
    }))
    return path


def is_article(request, lang=None):
    return "/per-article/" in request.raw_path and "Main_Page" not in request.raw_path and (
        lang is None or f"/{lang}.wikipedia/" in request.raw_path)


def test_analyze_writes_versioned_results_and_fixes_the_window(tmp_path):
    path = write_spec(tmp_path)
    code, out, _ = run_cli(["analyze", str(path)], tmp_path, astronomy())
    assert code == 0 and out["ok"] and "partial" not in out
    assert json.loads(path.read_text())["period"] == {"relative": "24m", "start": "2024-09", "end": "2026-08"}
    cells = {c["lang"]: c for c in out["topics"][0]["cells"]}
    assert cells["pl"]["group"] == "promising" and cells["pl"]["growth"].endswith("%/рік")
    assert out["answer_md"].startswith("**астрономія: ріст підтверджено — pl")
    saved = json.loads((path.parent / "result.json").read_text())
    assert saved["result"] == out["result"] and Path(out["result"]).exists()
    assert saved["answer_md_full"] and saved["topics"][0]["cells"][0]["series"]["index"]
    assert out["changes"] == [] and out["text_lang"] == "uk"


def test_second_analyze_reads_articles_from_the_cache(tmp_path):
    path = write_spec(tmp_path)
    web = astronomy()
    run_cli(["analyze", str(path)], tmp_path, web)
    code, out, transport = run_cli(["analyze", str(path)], tmp_path, web)
    assert code == 0 and out["changes"] == []
    assert transport.count(lambda r: is_article(r)) == 0
    assert transport.count(lambda r: "/aggregate/" in r.raw_path) > 0  # always re-requested (data §5)
    assert len(list((path.parent / "results").iterdir())) == 2
    code, out, transport = run_cli(["analyze", str(path), "--no-cache"], tmp_path, web)
    assert code == 0 and transport.count(lambda r: is_article(r)) > 0  # reads bypass the cache


def test_added_language_narrows_common_and_changes_say_so(tmp_path):
    path = write_spec(tmp_path, langs=("pl", "cs"))
    web = astronomy()
    web.entity("Q4", {"uk": "Планета"}, {"pl": "Planeta", "cs": "cs Planeta"})  # no uk article
    spec_data = json.loads(path.read_text())
    spec_data["topics"][0]["items"].append("Q4")
    path.write_text(json.dumps(spec_data))
    run_cli(["analyze", str(path)], tmp_path, web)
    run_cli(["spec", "edit", str(path), "--add-lang", "uk"], tmp_path, web)
    code, out, _ = run_cli(["analyze", str(path)], tmp_path, web)
    assert code == 0
    narrowed = next(c for c in out["changes"] if c["kind"] == "common_narrowed")
    assert narrowed["removed"] == [{"qid": "Q4", "label": "Планета"}] and narrowed["because_langs"] == ["uk"]


def test_revised_history_is_reported(tmp_path):
    path = write_spec(tmp_path)
    web = astronomy()
    run_cli(["analyze", str(path)], tmp_path, web)
    web.traffic["pl"] = lambda m: 11_000_000 if m == "2025-03" else 10_000_000
    code, out, _ = run_cli(["analyze", str(path)], tmp_path, web)
    assert code == 0
    assert any(w["code"] == "data_revised" and w["lang"] == "pl" and "2025-03" in w["months"] for w in out["warnings"])
    assert any(c["kind"] == "data_revised" and c["lang"] == "pl" for c in out["changes"])


def test_budget(tmp_path, monkeypatch):
    path = write_spec(tmp_path)
    monkeypatch.setattr(T, "MAX_REQUESTS", 3)
    code, out, _ = run_cli(["analyze", str(path)], tmp_path, astronomy())
    assert code == 2 and out["code"] == "budget_exceeded"
    code, out, _ = run_cli(["analyze", str(path), "--allow-large"], tmp_path, astronomy())
    assert code == 0 and out["ok"]


def test_no_pair_analysed_is_no_data(tmp_path):
    path = write_spec(tmp_path, items=("Q8", "Q9"), seeds=("Q8",))  # no such items: no articles anywhere
    code, out, _ = run_cli(["analyze", str(path)], tmp_path, astronomy())
    assert code == 3 and out["ok"] is False and out["code"] == "no_data"


def test_deadline_gives_partial_and_the_same_command(tmp_path):
    path = write_spec(tmp_path)
    code, out, _ = run_cli(["analyze", str(path)], tmp_path, astronomy(), deadline=Deadline(0))
    assert code == 0 and out["ok"] and out["partial"]
    assert out["next_steps"] == [cli.Context({}, tmp_path, tmp_path, None).command("analyze", str(path))]


def test_quick_with_seed_skips_find_and_creates_the_spec_first(tmp_path):
    code, out, transport = run_cli(["quick", "астрономія", "--seed", "Q1", "--langs", "uk,pl,cs", "--display-lang", "uk"],
                                   tmp_path, astronomy())
    assert code == 0 and out["ok"], out
    assert transport.count(lambda r: r.params.get("generator") == "search" and not r.params["gsrsearch"].startswith("morelike:")) == 0
    assert Path(out["spec"]).exists() and Path(out["result"]).exists()
    assert out["basket"][0] == {"qid": "Q1", "label": "укр Astronomia"}


def test_quick_find_leader_and_argument_errors(tmp_path):
    code, out, _ = run_cli(["quick", "астрономія", "--lang", "uk", "--langs", "uk,pl,cs", "--display-lang", "uk"],
                           tmp_path, astronomy())
    assert code == 0 and out["ok"] and out["basket"][0]["qid"] == "Q1"
    code, out, _ = run_cli(["quick", "астрономія", "--langs", "uk", "--display-lang", "uk"], tmp_path, astronomy())
    assert code == 2 and out["code"] == "invalid_arguments"
    code, out, _ = run_cli(["quick", "астрономія", "--lang", "uk", "--display-lang", "uk"], tmp_path, astronomy())
    assert code == 2 and out["code"] == "langs_required"
    code, out, _ = run_cli(["quick", "астрономія", "--lang", "uk", "--langs", "uk"], tmp_path, astronomy())
    assert code == 2 and out["code"] == "display_lang_required"
    code, out, _ = run_cli(["quick", "астрономія", "--seed", "Q1", "--langs", "uk,pl", "--display-lang", "uk",
                            "--start", "2025-01", "--end", "2026-12"], tmp_path, astronomy())
    assert code == 2 and out["code"] == "end_not_available" and out["latest"] == "2026-08"


def test_quick_ambiguous_writes_nothing(tmp_path):
    web = astronomy()
    web.entity("Q308", {"uk": "Меркурій"}, {"uk": "Меркурій"})
    web.entity("Q47652", {"uk": "Меркурій"}, {"uk": "Меркурій (міфологія)"})
    web.search[("uk", "Меркурій")] = ["Меркурій", "Меркурій (міфологія)"]
    code, out, _ = run_cli(["quick", "Меркурій", "--lang", "uk", "--langs", "uk", "--display-lang", "uk"], tmp_path, web)
    assert code == 0 and out["status"] == "needs_user" and out["code"] == "ambiguous_topic"
    assert not (tmp_path / "wd").exists() or not list((tmp_path / "wd").glob("*/spec.json"))


def test_quick_deadline_during_collection_continues_with_analyze(tmp_path):
    started = {"articles": False}
    web = astronomy()
    handle = web.handle

    def watching(request):
        if is_article(request):
            started["articles"] = True
        return handle(request)

    web.handle = watching
    deadline = Deadline(1.0, clock=lambda: 5.0 if started["articles"] else 0.0)
    code, out, _ = run_cli(["quick", "астрономія", "--seed", "Q1", "--langs", "uk,pl,cs", "--display-lang", "uk"],
                           tmp_path, web, deadline=deadline)
    assert code == 0 and out["partial"]
    spec_path = Path(out["spec"])
    assert spec_path.exists() and out["next_steps"][0].endswith(f"analyze {spec_path}")
    code, out, _ = run_cli(["analyze", str(spec_path)], tmp_path, web)
    assert code == 0 and "partial" not in out
    assert [p.parent.name for p in (tmp_path / "wd").glob("*/spec.json")] == [spec_path.parent.name]  # no -2
