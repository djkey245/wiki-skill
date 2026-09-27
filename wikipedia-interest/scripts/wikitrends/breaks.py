"""Known structural breaks of Wikimedia pageview data and the level-shift check (data §6).

A break is not flagged just because the window crosses it: the share partly cancels it, since
it hits both the article and the edition. For every break inside the window the check compares
the level shift of the deseasonalized log metric at the break (median of BREAK_SIDE_MONTHS
months after minus the same before) with the typical such shift in the history before the
window. Sources: references/data-breaks.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Dict, List, Optional, Sequence

from . import months
from . import thresholds as T


@dataclass(frozen=True)
class Break:
    id: str
    month: str  # the first month with the new data
    source: str


BREAKS = (
    # Since April 2020 AQS tags traffic of spammers and evading bots as agent=automated;
    # before that part of it counted as agent=user.
    Break("automated_agent", "2020-04", "https://techblog.wikimedia.org/2020/10/05/bot-or-not-identifying-fake-traffic-on-wikipedia/"),
    # The 2025 bot detection update, traffic of March-August 2025 reclassified
    # (whether AQS was reprocessed: plan 09, Part B).
    Break("bot_detection_2025", "2025-03", "https://diff.wikimedia.org/2025/10/17/new-user-trends-on-wikipedia/"),
)


def _median(values: Sequence[Optional[float]]) -> Optional[float]:
    present = [v for v in values if v is not None]
    return median(present) if len(present) * 2 > len(values) else None  # most months must have a value


def level_shift(series: Sequence[Optional[float]], at: int, side: int = T.BREAK_SIDE_MONTHS) -> Optional[float]:
    """Median of `side` months from `at` minus the median of `side` months before it."""
    if at - side < 0 or at + side > len(series):
        return None
    after, before = _median(series[at : at + side]), _median(series[at - side : at])
    return None if after is None or before is None else after - before


def typical_shift(history: Sequence[Optional[float]], side: int = T.BREAK_SIDE_MONTHS) -> Optional[float]:
    """Median |level_shift| over every position of the history; None when the history is too short."""
    shifts = [level_shift(history, t, side) for t in range(side, len(history) - side + 1)]
    present = [abs(s) for s in shifts if s is not None]
    return median(present) if present else None


def in_window(start: str, end: str, known: Sequence[Break] = BREAKS) -> List[Break]:
    """Breaks whose first month lies inside the window after its first month."""
    return [b for b in known if months.diff(b.month, start) > 0 and months.diff(end, b.month) >= 0]


def check(
    window: Sequence[Optional[float]], history: Sequence[Optional[float]], window_start_month: str,
    known: Sequence[Break] = BREAKS, factor: float = T.BREAK_SHIFT_FACTOR,
) -> List[Dict[str, Any]]:
    """One record per break inside the window: status significant / not_significant / not_checked
    (too few months on a side of the break, or too short a history)."""
    end = months.add(window_start_month, len(window) - 1)
    series = list(history) + list(window)
    typical = typical_shift(history)
    out = []
    for b in in_window(window_start_month, end, known):
        at = len(history) + months.diff(b.month, window_start_month)
        shift = level_shift(series, at)
        record: Dict[str, Any] = {"id": b.id, "month": b.month}
        if shift is None or not typical:
            record["status"] = "not_checked"
        else:
            record.update(shift=round(shift, 4), typical=round(typical, 4),
                          status="significant" if abs(shift) > factor * typical else "not_significant")
        out.append(record)
    return out
