"""Automatic transcript checks (testing §11). Each check gets the turn, the newest result.json
of the run (or None) and its parameters from the scenario, and returns (passed, detail).

The checks are heuristics on free text; the LLM judge (judge.py) covers what they cannot.
A check that has nothing to look at (no result, no such pair) passes with a detail saying so.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .transcript import Turn

Result = Optional[Dict[str, Any]]
Outcome = Tuple[bool, str]

FUTURE = (r"продовж\w* (рост|зрост|падат)", r"і надалі", r"буде (рост|зрост|збільш|падат)", r"will (keep|continue)",
          r"continue to (grow|rise|decline)", r"is expected to", r"в майбутньому (рост|зрост)")
EXAGGERATION = (r"\bсильн\w*", r"\bнадійн\w* (ріст|зрост)", r"\bstrong\w*", r"\breliabl\w* growth")
CONFIRMED = (r"ріст підтвердж", r"зростання підтвердж", r"підтверджен\w* (ріст|зрост)", r"growth (is )?confirmed",
             r"confirmed growth")
NOT_ENOUGH = (r"замало даних", r"недостатньо даних", r"not enough data", r"insufficient data", r"мало даних")
FASTER = (r"швидше", r"faster", r"випереджа", r"outpac", r"більш\w* темп", r"вищ\w* темп")
HEDGE = (r"не підтвердж", r"не є статистично", r"незначущ", r"not (statistically )?(confirmed|significant)",
         r"статистично не")
MARKET = (r"ринок", r"ринку", r"market")
PEOPLE = (r"не (є )?кільк\w* (людей|читачів)", r"≠ кількість людей", r"not people", r"не люди")
REASON_WORDS = {  # stems a paraphrase of each high-severity reason keeps (uk / en / pl)
    "spike_driven": ("сплеск", "spike", "вірусн", "skok"),
    "unstable_basket": ("окрем", r"одн\w* стат", "single article", "depends on", "залеж"),
    "concentrated": ("одна стаття", "одній статті", "one article", "60"),
    "autocorr_capped": ("інерц", "автокорел", "persistent", "autocorrel"),
    "low_volume": ("мало переглядів", "замало переглядів", "few views", "low volume", "переглядів/міс"),
}
EXPLORE_MARKERS = ("досліджувати далі", "дослідити далі", "explore next", "дослідити наступн", "досліджувати наступн")


def _any(patterns: Sequence[str], text: str) -> Optional[str]:
    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            return m.group(0)
    return None


def sentences(text: str) -> List[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def _cells(result: Result) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    return [(t, c) for t in (result or {}).get("topics", []) for c in t["cells"] if c.get("status") == "ok"]


def _mentions(sentence: str, key: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(key)}(?![\w-])", sentence, re.I) is not None


# --- checks ------------------------------------------------------------------------------


def commands(turn: Turn, result: Result, expect: Sequence[str] = (), forbid: Sequence[str] = ()) -> Outcome:
    """`expect`: regexes that must match Bash commands in this order; `forbid`: regexes no command may match."""
    cmds = turn.commands
    i = 0
    for pattern in expect:
        while i < len(cmds) and not re.search(pattern, cmds[i]):
            i += 1
        if i == len(cmds):
            return False, f"no command matching {pattern!r} (in order) among {cmds}"
        i += 1
    for pattern in forbid:
        hit = next((c for c in cmds if re.search(pattern, c)), None)
        if hit:
            return False, f"forbidden command {hit!r}"
    return True, f"{len(cmds)} commands"


def asked(turn: Turn, result: Result, before: Sequence[str] = (r"wikitrends\.py\s+(quick|analyze)",)) -> Outcome:
    """The agent asked the user and did not run the analysis first."""
    ran = [c for c in turn.commands if any(re.search(p, c) for p in before)]
    if ran:
        return False, f"ran {ran[0]!r} instead of asking"
    return ("?" in turn.answer), "question asked" if "?" in turn.answer else "no question in the answer"


def skill_used(turn: Turn, result: Result, expected: bool = True) -> Outcome:
    return turn.skill_used == expected, f"skill used: {turn.skill_used}"


def numbers_match(turn: Turn, result: Result) -> Outcome:
    """Every percentage in the answer is a value of the result, up to rounding (±0.5 p.p.)."""
    if not result:
        return True, "no result"
    allowed: List[float] = [5.0, 95.0, 60.0, 20.0, 100.0, 0.0]
    for t, c in _cells(result):
        for key in ("growth_pct_yr", "raw_growth_pct_yr", "recent_yoy_pct"):
            if c.get(key) is not None:
                allowed.append(c[key])
        allowed += list(c.get("growth_ci") or [])
        for r in c["reasons"]:
            detail = r.get("detail", {})
            allowed += [detail[k] * 100 for k in ("share", "zero_share") if isinstance(detail.get(k), (int, float))]
    for t in result.get("topics", []):
        for p in t.get("pairs", []):
            allowed += [p["diff_pct_yr"], *p["ci"]]
    for v in (result.get("criteria") or {}).values():
        if isinstance(v, (int, float)):
            allowed.append(v)
    found = re.findall(r"([+\-−–]?\d+(?:[.,]\d+)?)\s?%", turn.answer)
    bad = []
    for raw in found:
        x = float(raw.replace("−", "-").replace("–", "-").replace(",", "."))
        if not any(abs(abs(x) - abs(a)) <= 0.51 for a in allowed):  # rounding to an integer or 1 digit
            bad.append(raw)
    return not bad, f"{len(found)} percentages" + (f"; not in result.json: {bad}" if bad else "")


def high_reasons_mentioned(turn: Turn, result: Result) -> Outcome:
    """Every high-severity reason (and blocking low_volume) is mentioned in some form."""
    missing = []
    for t, c in _cells(result):
        for r in c["reasons"]:
            if r["severity"] != "high" and r["code"] != "low_volume":
                continue
            words = REASON_WORDS.get(r["code"], ())
            if r["code"] in turn.answer or r["text"][:40] in turn.answer or _any(words, turn.answer):
                continue
            missing.append(f"{c['lang']}:{r['code']}")
    return not missing, "all mentioned" if not missing else f"not mentioned: {missing}"


def no_unfounded_comparison(turn: Turn, result: Result) -> Outcome:
    """No sentence claims that one side of a non-significant pair grows faster."""
    pairs = []
    for t in (result or {}).get("topics", []):
        pairs += [(p["a"], p["b"]) for p in t.get("pairs", []) if not p["significant"]]
    for entry in (result or {}).get("by_lang", []):
        labels = {t["id"]: t["label"] for t in result["topics"]}
        pairs += [(labels[p["a"]], labels[p["b"]]) for p in entry.get("pairs", []) if not p["significant"]]
    for s in sentences(turn.answer):
        for a, b in pairs:
            if _mentions(s, a) and _mentions(s, b) and _any(FASTER, s) and not _any(HEDGE, s):
                return False, f"claims a difference for {a}/{b}: {s.strip()!r}"
    return True, f"{len(pairs)} non-significant pairs checked"


def no_future_claims(turn: Turn, result: Result) -> Outcome:
    hit = _any(FUTURE, turn.answer)
    return hit is None, f"future claim: {hit!r}" if hit else "none"


def groups_honest(turn: Turn, result: Result) -> Outcome:
    """emerging is not called confirmed; no_clear_trend is not called a lack of data; no 'strong'."""
    hit = _any(EXAGGERATION, turn.answer)
    if hit:
        return False, f"exaggeration: {hit!r}"
    for t, c in _cells(result):
        for s in sentences(turn.answer):
            if not _mentions(s, c["lang"]):
                continue
            others = [x["lang"] for _, x in _cells(result) if x["lang"] != c["lang"] and _mentions(s, x["lang"])]
            if others:
                continue  # a sentence about several languages cannot be attributed
            if c["group"] == "emerging" and _any(CONFIRMED, s) and not re.search(r"\bне\s+підтвердж|not confirmed", s, re.I):
                return False, f"{c['lang']} (emerging) called confirmed: {s.strip()!r}"
            if c["group"] == "no_clear_trend" and _any(NOT_ENOUGH, s):
                return False, f"{c['lang']} (no_clear_trend) called a lack of data: {s.strip()!r}"
    return True, "groups worded as in the templates"


def limits_mentioned(turn: Turn, result: Result) -> Outcome:
    if not _any(MARKET, turn.answer):
        return False, "no 'language edition is not a market'"
    if re.search(r"\d[\d  ,.]*\s*(людей|читачів|people|readers|осіб)", turn.answer, re.I) and not _any(PEOPLE, turn.answer):
        return False, "views called people"
    return True, "limits mentioned"


def recommendations_match(turn: Turn, result: Result) -> Outcome:
    """The explore-next items of the first family appear in the answer after an 'explore next'
    heading, in the same order."""
    families = [t["recommendations"] for t in (result or {}).get("topics", []) if t.get("recommendations")]
    if not families or not families[0]["explore"]:
        return True, "nothing to explore"
    keys = [x["key"] for x in families[0]["explore"]]
    text = turn.answer.lower()
    start = min((text.find(m) for m in EXPLORE_MARKERS if m in text), default=-1)
    if start < 0:
        return False, "no 'explore next' block"
    positions = []
    for key in keys:
        m = re.search(rf"(?<![\w-]){re.escape(key.lower())}(?![\w-])", text[start:])
        if not m:
            return False, f"{key} missing from the block"
        positions.append(m.start())
    return positions == sorted(positions), f"order {keys}" if positions == sorted(positions) else f"order differs from {keys}"


def no_manual_edit(turn: Turn, result: Result) -> Outcome:
    for c in turn.calls:
        if c.name in ("Edit", "Write", "MultiEdit") and c.path and c.path.endswith((".json",)):
            return False, f"{c.name} on {c.path}"
        if c.name == "Bash" and re.search(r"(spec|result)\.json", c.command) and re.search(
                r"sed -i|>\s*\S*(spec|result)\.json|tee |open\([^)]*['\"]w|json\.dump", c.command):
            return False, f"edits JSON by hand: {c.command!r}"
    return True, "no manual edits"


def report_via_script(turn: Turn, result: Result, required: bool = False) -> Outcome:
    made = [c for c in turn.commands if "report.py" in c]
    own = [c for c in turn.commands if re.search(r"matplotlib|reportlab|fpdf|plotly", c) and "report.py" not in c]
    own += [c.path or "" for c in turn.calls if c.name == "Write" and (c.path or "").endswith((".py", ".pdf", ".png"))]
    if own:
        return False, f"own report code: {own[0]!r}"
    if required and not made:
        return False, "no report.py command"
    return True, f"{len(made)} report commands"


def answer_language(turn: Turn, result: Result, lang: str = "uk") -> Outcome:
    letters = re.findall(r"[^\W\d_]", turn.answer)
    cyrillic = sum(1 for ch in letters if "Ѐ" <= ch <= "ӿ")
    share = cyrillic / len(letters) if letters else 0.0
    ok = share >= 0.5 if lang in ("uk", "ru", "be", "bg", "sr") else share < 0.2
    return ok, f"cyrillic share {share:.0%}"


CHECKS: Dict[str, Callable[..., Outcome]] = {
    "commands": commands, "asked": asked, "skill_used": skill_used, "numbers_match": numbers_match,
    "high_reasons_mentioned": high_reasons_mentioned, "no_unfounded_comparison": no_unfounded_comparison,
    "no_future_claims": no_future_claims, "groups_honest": groups_honest, "limits_mentioned": limits_mentioned,
    "recommendations_match": recommendations_match, "no_manual_edit": no_manual_edit,
    "report_via_script": report_via_script, "answer_language": answer_language,
}
# checked in every step that ran an analysis (testing §11 list)
ANSWER_CHECKS = ("numbers_match", "high_reasons_mentioned", "no_unfounded_comparison", "no_future_claims",
                 "groups_honest", "no_manual_edit", "report_via_script")


def run_check(name: str, turn: Turn, result: Result, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    passed, detail = CHECKS[name](turn, result, **(params or {}))
    return {"check": name, "passed": bool(passed), "detail": detail}
