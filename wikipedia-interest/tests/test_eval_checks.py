"""Transcript parsing and the automatic checks of the evals, without any model."""
from pathlib import Path

from test_analysis import LABELS, WINDOW, make_spec, three_langs
from wikitrends import analysis
from wteval import checks
from wteval.transcript import ToolCall, Turn, turn_from_claude_stream

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "evals" / "stream_echo.jsonl"


def result():
    return analysis.evaluate(make_spec(["uk", "pl", "cs"]), WINDOW, three_langs(), {}, LABELS)


def cells(r):
    return {c["lang"]: c for c in r["topics"][0]["cells"]}


def turn(answer, *commands, calls=()):
    return Turn("q", [*(ToolCall("Bash", c) for c in commands), *calls], answer)


def test_claude_stream_is_parsed():
    t, session = turn_from_claude_stream(FIXTURE.read_text().splitlines(), "echo please")
    assert session == "00000000-0000-4000-8000-000000000001"
    assert [(c.name, c.command, c.output, c.is_error) for c in t.calls] == [("Bash", "echo hello", "hello", False)]
    assert t.answer == "done" and t.output_tokens > 0 and t.input_tokens > 0 and t.error is None
    assert not t.skill_used


def test_skill_use_is_detected():
    assert turn("", "python3 /x/scripts/wikitrends.py quick a").skill_used
    assert Turn("q", [ToolCall("Skill", '{"skill": "wikipedia-interest"}')]).skill_used
    assert Turn("q", [ToolCall("Read", "{}", path="/p/.claude/skills/wikipedia-interest/SKILL.md")]).skill_used


def test_commands_order_and_forbid():
    t = turn("", "python3 w/wikitrends.py find x --lang uk", "python3 w/wikitrends.py quick x --seed Q1")
    assert checks.commands(t, None, expect=[r"find", r"quick .*--seed"])[0]
    assert not checks.commands(t, None, expect=[r"quick", r"find"])[0]
    assert not checks.commands(t, None, forbid=[r"--period"] + [r"find"])[0]


def test_asked():
    assert checks.asked(turn("Які мови вас цікавлять?"), None)[0]
    assert not checks.asked(turn("Ось результат.", "python3 w/wikitrends.py quick a --langs pl"), None)[0]


def test_numbers_match():
    r = result()
    pl = cells(r)["pl"]
    good = turn(f"У pl ріст підтверджено: {pl['fmt']['growth']} {pl['fmt']['ci']}.")
    assert checks.numbers_match(good, r)[0]
    assert not checks.numbers_match(turn("У pl ріст +73 %/рік."), r)[0]


def test_no_unfounded_comparison():
    r = result()
    pair = next(p for p in r["topics"][0]["pairs"] if not p["significant"])
    a, b = pair["a"], pair["b"]
    assert not checks.no_unfounded_comparison(turn(f"У {a} інтерес росте швидше, ніж у {b}."), r)[0]
    assert checks.no_unfounded_comparison(turn(f"Оцінки для {a} і {b} різні, але різниця між ними статистично не підтверджена; швидше не скажеш."), r)[0]


def test_groups_honest_and_future():
    r = result()
    r["topics"][0]["cells"][2]["group"] = "emerging"  # cs
    assert not checks.groups_honest(turn("У cs ріст підтверджено."), r)[0]
    assert checks.groups_honest(turn("У cs ріст не підтверджено."), r)[0]
    assert not checks.groups_honest(turn("Сильне зростання в pl."), r)[0]
    r["topics"][0]["cells"][0]["group"] = "no_clear_trend"  # uk
    assert not checks.groups_honest(turn("Для uk замало даних."), r)[0]
    assert not checks.no_future_claims(turn("Інтерес і надалі буде рости."), r)[0]


def test_high_reasons_and_limits():
    r = result()
    assert not checks.high_reasons_mentioned(turn("Все добре."), r)[0]  # cs has 'concentrated'
    assert checks.high_reasons_mentioned(turn("У cs одна стаття дає більшу частину зміни; у uk мало переглядів."), r)[0]
    assert checks.limits_mentioned(turn("Мовний розділ — не ринок."), r)[0]
    assert not checks.limits_mentioned(turn("Мовний розділ — не ринок. Це 65 000 людей щомісяця."), r)[0]


def test_recommendations_match():
    r = result()
    assert checks.recommendations_match(turn("Досліджувати далі: pl."), r)[0]
    assert not checks.recommendations_match(turn("Рекомендую pl."), r)[0]


def test_manual_edits_and_own_reports():
    edit = ToolCall("Edit", "{}", path="/wd/astronomy/spec.json")
    assert not checks.no_manual_edit(turn("", calls=[edit]), None)[0]
    assert not checks.no_manual_edit(turn("", "sed -i 's/pl/cs/' /wd/a/spec.json"), None)[0]
    assert checks.no_manual_edit(turn("", "python3 w/wikitrends.py spec edit /wd/a/spec.json --add-lang sk"), None)[0]
    assert not checks.report_via_script(turn("", "python3 -c 'import matplotlib'"), None)[0]
    assert not checks.report_via_script(turn(""), None, required=True)[0]
    assert checks.report_via_script(turn("", "uv run /s/scripts/report.py /wd/a/spec.json"), None, required=True)[0]


def test_answer_language():
    assert checks.answer_language(turn("Інтерес до астрономії росте."), None, lang="uk")[0]
    assert checks.answer_language(turn("Zainteresowanie rośnie."), None, lang="pl")[0]
    assert not checks.answer_language(turn("Interest grows."), None, lang="uk")[0]
