"""Report from result.json: report.md, PNG charts and a one-page A4 PDF (report part of the spec).

Imported only by report.py: it needs matplotlib. No network; the same result.json gives the
same bytes (no creation dates or software versions in the files, fixed order of everything).
Text comes from templates.py, so the report and answer_md never diverge.
"""
from __future__ import annotations

import copy
import json
import re
import sys
import textwrap
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, TextIO, Tuple

import matplotlib

matplotlib.use("Agg")
from matplotlib.figure import Figure  # noqa: E402

from . import compare, fs, templates  # noqa: E402
from . import thresholds as T  # noqa: E402
from .cli import SCRIPT, SCHEMA_VERSION, JsonArgumentParser  # noqa: E402
from .errors import WikitrendsError  # noqa: E402

REPORT_DIR = "report"
FORMATS = ("pdf", "png", "md")
# The validated reference categorical palette (light surface); slot = the language's position
# in the spec, so a filter never repaints the remaining languages.
PALETTE = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948")
INK, INK_SECONDARY, INK_MUTED, GRID = "#0b0b0b", "#52514e", "#8a8984", "#e4e3df"
RC = {
    "font.family": "DejaVu Sans",  # ships with matplotlib: Cyrillic without system fonts
    "pdf.fonttype": 42,
    "svg.hashsalt": "wikitrends",
    "axes.edgecolor": INK_MUTED,
    "axes.labelcolor": INK_SECONDARY,
    "xtick.color": INK_SECONDARY,
    "ytick.color": INK_SECONDARY,
    "text.color": INK,
}
A4 = (8.27, 11.69)
CHART_TITLE = {"uk": "{topic}: індекс частки (перші 12 міс. вікна = 100)",
               "en": "{topic}: share index (first 12 months of the window = 100)"}
INDEX_NOTE = {"uk": "Графіки: індекс частки теми в розділі, перші 12 міс. вікна = 100.",
              "en": "Charts: the topic's share index in each edition, first 12 months of the window = 100."}
OTHERS = {"uk": "інші", "en": "others"}
CHARTS_HEADING = {"uk": "### Графіки", "en": "### Charts"}
DATA_LINE = {"uk": "Дані: Wikimedia Pageviews, {start} – {end}; повний текст — report.md, числа — result.json.",
             "en": "Data: Wikimedia Pageviews, {start} – {end}; full text in report.md, numbers in result.json."}


# --- input -----------------------------------------------------------------------------


def result_path(target: Path) -> Path:
    """spec.json, its directory, result.json or a results/<version>.json -> the result file."""
    target = Path(target)
    path = target / "result.json" if target.is_dir() else target
    if path.name == "spec.json":
        path = path.parent / "result.json"
    if not path.exists():
        spec = path.parent / "spec.json"
        raise WikitrendsError(
            "invalid_arguments", f"no analysis result at {path}",
            hint=f"Run the analysis first: python3 {SCRIPT} analyze {spec}",
        )
    return path.resolve()


def spec_dir(path: Path) -> Path:
    return path.parent.parent if path.parent.name == "results" else path.parent


def filter_result(result: Dict[str, Any], topic: Optional[str], lang: Optional[str]) -> Dict[str, Any]:
    """Keep one topic and/or one language. Only whole comparison families survive a filter:
    --topic drops the comparison of topics within languages, --lang the comparison of languages."""
    out = copy.deepcopy(result)
    if topic is not None:
        ids = [t["id"] for t in out["topics"]]
        if topic not in ids:
            raise WikitrendsError("invalid_arguments", f"no topic {topic!r}", hint=f"Use --topic with one of: {', '.join(ids)}")
        out["topics"] = [t for t in out["topics"] if t["id"] == topic]
        out["by_lang"] = []
    if lang is not None:
        langs = [x["lang"] for x in out["langs"]]
        if lang not in langs:
            raise WikitrendsError("invalid_arguments", f"no language {lang!r}", hint=f"Use --lang with one of: {', '.join(langs)}")
        out["langs"] = [x for x in out["langs"] if x["lang"] == lang]
        for t in out["topics"]:
            t["cells"] = [c for c in t["cells"] if c["lang"] == lang]
            t.update(pairs=[], pairs_skipped=None, recommendations=None, ranking=[lang], matrix=False)
            t["excluded"] = [e for e in t["excluded"] if e["lang"] == lang]
            t["headline"] = templates.headline(t["label"], t["cells"], out["text_lang"])
        out["by_lang"] = [e for e in out["by_lang"] if e["lang"] == lang]
    return out


# --- charts ----------------------------------------------------------------------------


def lang_colors(result: Dict[str, Any]) -> Dict[str, str]:
    """Colour follows the language (its slot in the spec), never its rank; past 8 languages no colour."""
    return {x["lang"]: PALETTE[i] for i, x in enumerate(result["langs"]) if i < len(PALETTE)}


def _spread(ys: List[float], gap: float) -> List[float]:
    """Label positions at least `gap` apart, keeping their order (direct labels must not collide)."""
    order = sorted(range(len(ys)), key=lambda i: (ys[i], i))
    placed = [0.0] * len(ys)
    last = float("-inf")
    for i in order:
        last = max(ys[i], last + gap)
        placed[i] = last
    return placed


def highlighted(topic: Dict[str, Any]) -> List[str]:
    """Languages drawn in colour: all when there are at most CHART_MAX_SERIES, otherwise the
    detailed cells (promising, emerging, weakening, criteria met) or the top of the ranking."""
    langs = [c["lang"] for c in topic["cells"] if c["status"] == "ok" and c.get("series")]
    if len(langs) <= T.CHART_MAX_SERIES:
        return langs
    key = [c["lang"] for c in topic["cells"] if c["lang"] in langs and templates.is_key_cell(c)]
    chosen = key or [x for x in topic["ranking"] if x in langs]
    return [x for x in langs if x in chosen[: T.CHART_MAX_SERIES]]


def draw_index(ax: Any, result: Dict[str, Any], topic: Dict[str, Any], colors: Dict[str, str], *, compact: bool) -> None:
    lang = result["text_lang"]
    months = result["period"]["months"]
    x = list(range(len(months)))
    cells = [c for c in topic["cells"] if c["status"] == "ok" and c.get("series")]
    shown = [x_ for x_ in highlighted(topic) if x_ in colors]
    ax.axhline(100, color=INK_MUTED, linewidth=0.8, linestyle=(0, (3, 3)), zorder=1)
    ends = []
    others = False
    for c in cells:  # the rest first, in grey, under the coloured lines
        if c["lang"] not in shown:
            ys = [float("nan") if v is None else v for v in c["series"]["index"]]
            ax.plot(x, ys, color=GRID, linewidth=0.8, zorder=1.5, label=None if others else OTHERS[lang])
            others = True
    for c in cells:
        if c["lang"] not in shown:
            continue
        ys = [float("nan") if v is None else v for v in c["series"]["index"]]
        ax.plot(x, ys, color=colors[c["lang"]], linewidth=1.5 if compact else 2.0, label=c["lang"], zorder=2)
        last = max((i for i, v in enumerate(ys) if v == v), default=None)
        if last is not None:
            ends.append((c["lang"], x[last], ys[last]))
    if 0 < len(ends) <= 4:  # direct labels: identity is never colour alone
        low, high = ax.get_ylim()
        for (name, xe, _), y in zip(ends, _spread([e[2] for e in ends], (high - low) * 0.06)):
            ax.annotate(name, (xe, y), xytext=(4, 0), textcoords="offset points", va="center",
                        fontsize=6 if compact else 8, color=INK_SECONDARY)
    step = 6 if len(months) > 12 else 3
    ticks = list(range(0, len(months), step))
    ax.set_xticks(ticks)
    ax.set_xticklabels([months[i] for i in ticks], fontsize=5.5 if compact else 7)
    ax.tick_params(axis="y", labelsize=5.5 if compact else 7)
    ax.set_xlim(0, len(months) - 1 + (3 if len(ends) <= 4 else 0))
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    title = topic["label"] if compact else CHART_TITLE[lang].format(topic=topic["label"])
    ax.set_title(title, fontsize=7 if compact else 9, loc="left", color=INK)
    if len(cells) >= 2:
        ax.legend(fontsize=5.5 if compact else 7, frameon=False, ncol=len(shown) + others, loc="upper left")


def png_name(topic: Dict[str, Any]) -> str:
    return f"index-{topic['id']}.png"


def write_pngs(result: Dict[str, Any], out: Path) -> List[Path]:
    colors = lang_colors(result)
    paths = []
    with matplotlib.rc_context(RC):
        for topic in result["topics"]:
            fig = Figure(figsize=(7.0, 3.4), dpi=150)
            draw_index(fig.add_axes((0.07, 0.12, 0.9, 0.78)), result, topic, colors, compact=False)
            path = out / png_name(topic)
            _save(fig, path, "png")
            paths.append(path)
    return paths


def _save(fig: Figure, path: Path, fmt: str) -> None:
    """Deterministic bytes: no dates, no software versions; written atomically."""
    metadata = {"CreationDate": None, "Creator": None, "Producer": None} if fmt == "pdf" else {"Software": None}
    tmp = path.with_name(path.name + ".tmp")
    fig.savefig(tmp, format=fmt, metadata=metadata)
    tmp.replace(path)


# --- report.md -------------------------------------------------------------------------


def report_md(result: Dict[str, Any], pngs: Sequence[str]) -> str:
    """answer_md without truncation (== answer_md_full without filters) plus links to the charts."""
    text = templates.answer_md(result, full=True)
    if pngs:
        lang = result["text_lang"]
        links = [f"![{t['label']}]({name})" for t, name in zip(result["topics"], pngs)]
        text += "\n\n" + "\n".join([CHARTS_HEADING[lang], *links])
    return text + "\n"


# --- the PDF page ----------------------------------------------------------------------


Line = Tuple[str, bool]  # (text, bold)


def _plain(line: str) -> Line:
    """A Markdown line of answer_md as page text: headings and bold labels become bold lines."""
    bold = line.startswith("### ") or (line.startswith("**") and line.endswith("**"))
    text = line[4:] if line.startswith("### ") else line
    text = text.replace("**", "").replace("*", "")
    return ("• " + text[2:] if text.startswith("- ") else text), bold


def cell_priority(topic: Dict[str, Any], cell: Dict[str, Any]) -> Tuple[int, int, float]:
    """PDF table order: detailed cells (promising, emerging, weakening, criteria met) first, then by group and growth."""
    growth = cell.get("growth_pct_yr")
    return (0 if templates.is_key_cell(cell) else 1, compare.GROUPS.index(cell["group"]),
            -(growth if growth is not None else float("-inf")))


def topic_priority(topic: Dict[str, Any]) -> int:
    return min(compare.GROUPS.index(c["group"]) for c in topic["cells"])


def page_text(result: Dict[str, Any], *, short: bool = False) -> Tuple[List[Line], List[Line], List[Line]]:
    """(recommendations, optional, limits). Recommendations come first and are kept whole;
    `short` takes them from the truncated answer (only "explore next" per family) for pages where
    the full block cannot fit. Pairs, reasons and the rest of the assumptions — as in the truncated
    answer (significant and neighbouring pairs, grouped reasons) — fill the space left."""
    lang = result["text_lang"]
    s = templates.SENTENCES[lang]
    sections = dict(templates.answer_sections(result, full=False))
    source = sections if short else dict(templates.answer_sections(result, full=True))
    recommendations = [_plain(x) for x in source.get("recommendations", [])]
    limits = [("• " + s["market"], False), ("• " + s["no_forecast"], False),
              (DATA_LINE[lang].format(start=result["period"]["start"], end=result["period"]["end"]), False)]
    optional: List[Line] = []
    for name in ("pairs", "reasons", "assumptions"):
        optional += [x for x in (_plain(y) for y in sections.get(name, [])) if x not in limits]
    return recommendations, optional, limits


def _wrap(lines: Sequence[Line], width: int) -> List[Line]:
    out: List[Line] = []
    for text, bold in lines:
        out += [(part, bold) for part in textwrap.wrap(text, width=width, subsequent_indent="  ") or [""]]
    return out


def line_height(size: float) -> float:
    """Height of one text line as a fraction of the A4 page."""
    return size * T.PDF_LINE_SPACING / 72 / A4[1]


def fit_text(result: Dict[str, Any], height: float) -> Tuple[float, List[Line]]:
    """(font size, wrapped lines) of the text block: the full recommendations at the largest font
    that fits them with the limits, else the short ones; then as many optional lines as fit, with
    "+N, see report.md" when some are left out. Nothing is drawn past the page."""
    lang = result["text_lang"]
    for short in (False, True):
        recommendations, optional, limits = page_text(result, short=short)
        for size in T.PDF_FONT_SIZES:
            capacity = int(height / line_height(size))
            width = int(T.PDF_WRAP_AT_7PT * 7 / size)
            head, tail, rest = _wrap(recommendations, width), _wrap(limits, width), _wrap(optional, width)
            room = capacity - len(head) - len(tail)
            if room < 0 and not (short and size == T.PDF_FONT_SIZES[-1]):
                continue
            if room < 0:  # even the short block does not fit: cut it, keep the limits
                head, room = head[: capacity - len(tail) - 1] + [(_report_more(len(head) - capacity + len(tail) + 1, lang), False)], 0
            if len(rest) > room:
                kept = rest[: max(room - 1, 0)]
                rest = kept + [(_report_more(len(rest) - len(kept), lang), False)]
            return size, head + rest[: max(room, 0)] + tail
    raise AssertionError("unreachable")


def _report_more(n: int, lang: str) -> str:
    return templates.SENTENCES[lang]["more"].format(n=n).replace("result.json", "report.md")


def _lines(fig: Figure, lines: Sequence[Line], left: float, top: float, size: float, color: str = INK) -> float:
    """Draw lines one by one from `top` down; returns the y below the last line."""
    y = top
    for text, bold in lines:
        fig.text(left, y, text, fontsize=size, fontweight="bold" if bold else "normal", va="top", color=color)
        y -= line_height(size)
    return y


def write_pdf(result: Dict[str, Any], path: Path) -> None:
    """One A4 page laid out top-down: headlines, up to 3 index charts, the top cells, the text block."""
    lang = result["text_lang"]
    colors = lang_colors(result)
    ids = [t["id"] for t in result["topics"]]
    topics = sorted(result["topics"], key=lambda t: (topic_priority(t), ids.index(t["id"])))
    more = templates.SENTENCES[lang]["more"]
    left, width, bottom = 0.06, 0.88, 0.03
    with matplotlib.rc_context(RC):
        fig = Figure(figsize=A4)
        head = _wrap([(t["headline"], True) for t in topics], 118)
        if len(head) > T.PDF_HEADLINE_LINES:
            head = head[: T.PDF_HEADLINE_LINES - 1] + [(more.format(n=len(head) - T.PDF_HEADLINE_LINES + 1).replace("result.json", "report.md"), False)]
        y = _lines(fig, head, left, 0.97, 8)
        y = _lines(fig, [(INDEX_NOTE[lang], False)], left, y - 0.004, 6.5, INK_SECONDARY) - 0.012
        charted = topics[: T.PDF_MAX_CHARTS]
        gap = 0.04
        w = (width - gap * (len(charted) - 1)) / len(charted)
        chart_h = 0.2
        for i, topic in enumerate(charted):
            ax = fig.add_axes((left + i * (w + gap), y - chart_h - 0.02, w, chart_h))
            draw_index(ax, result, topic, colors, compact=True)
        y -= chart_h + 0.06
        multi = len(result["topics"]) > 1
        criteria = bool(result.get("criteria"))
        rows = sorted(((t, c) for t in result["topics"] for c in t["cells"]), key=lambda tc: cell_priority(*tc))
        shown = rows[: T.PDF_TABLE_ROWS]
        table_h = T.PDF_TABLE_ROW_HEIGHT * (len(shown) + 1)
        ax = fig.add_axes((left, y - table_h, width, table_h))
        ax.axis("off")
        table = ax.table(cellText=[templates.table_row(t, c, lang, multi, criteria) for t, c in shown],
                         colLabels=templates.table_header(lang, multi, criteria), cellLoc="left", bbox=(0, 0, 1, 1))
        table.auto_set_font_size(False)
        table.set_fontsize(6)
        table.auto_set_column_width(list(range(len(templates.table_header(lang, multi, criteria)))))
        for (row, _), cell in table.get_celld().items():
            cell.set_edgecolor(GRID)
            cell.set_linewidth(0.5)
            if row == 0:
                cell.set_text_props(fontweight="bold", color=INK)
        y -= table_h + 0.008
        if len(rows) > len(shown):
            y = _lines(fig, [(more.format(n=len(rows) - len(shown)).replace("result.json", "report.md"), False)],
                       left, y, 6, INK_SECONDARY)
        y -= 0.012
        size, lines = fit_text(result, y - bottom)
        _lines(fig, lines, left, y, size)
        _save(fig, path, "pdf")


# --- command ---------------------------------------------------------------------------


def build_parser() -> JsonArgumentParser:
    parser = JsonArgumentParser(prog="report.py")
    parser.add_argument("file")
    parser.add_argument("--format", default=",".join(FORMATS))
    parser.add_argument("--topic")
    parser.add_argument("--lang")
    parser.add_argument("--out")
    return parser


def make_report(argv: Sequence[str]) -> Dict[str, Any]:
    args = build_parser().parse_args(list(argv))
    formats = [f.strip() for f in args.format.split(",") if f.strip()]
    unknown = [f for f in formats if f not in FORMATS]
    if unknown or not formats:
        raise WikitrendsError("invalid_arguments", f"unknown format {','.join(unknown) or '(none)'}",
                              hint="Use --format with any of pdf,png,md")
    path = result_path(Path(args.file))
    result = filter_result(json.loads(path.read_text(encoding="utf-8")), args.topic, args.lang)
    out = Path(args.out).resolve() if args.out else spec_dir(path) / REPORT_DIR
    out.mkdir(parents=True, exist_ok=True)
    files: Dict[str, Any] = {}
    pngs = [png_name(t) for t in result["topics"]] if "png" in formats else []
    if "png" in formats:
        files["png"] = [str(p) for p in write_pngs(result, out)]
    if "md" in formats:
        md = out / "report.md"
        fs.atomic_write_text(md, report_md(result, pngs))
        files["md"] = str(md)
    if "pdf" in formats:
        pdf = out / "report.pdf"
        write_pdf(result, pdf)
        files["pdf"] = str(pdf)
    return {"out": str(out), "files": files, "text_lang": result["text_lang"]}


def run(argv: Sequence[str], stdout: TextIO) -> int:
    try:
        payload: Dict[str, Any] = {"ok": True, **make_report(argv)}
        code = 0
    except WikitrendsError as exc:
        payload, code = exc.to_payload(), exc.exit_code
    except Exception as exc:  # noqa: BLE001 - stdout must stay JSON
        traceback.print_exc(file=sys.stderr)
        payload = {"ok": False, "code": "internal_error", "message": f"{type(exc).__name__}: {exc}",
                   "hint": "Tell the user the report failed; do not retry in a loop"}
        code = 1
    payload["schema_version"] = SCHEMA_VERSION
    stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    stdout.flush()
    return code


def pages(pdf: bytes) -> int:
    """Number of pages of a PDF written by matplotlib (tests)."""
    return len(re.findall(rb"/Type\s*/Page[^s]", pdf))
