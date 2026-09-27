"""When a page becomes "alive", from its pageview series (analysis §1, calibration R23).

No revision requests: the series is already loaded. A page that was a redirect for years
is not alive from the redirect's creation, and a renamed page is alive from the start of
its old title, because its series already sums the old titles.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from . import months
from . import thresholds as T


def _first_after_last_long_zero_run(
    views: Sequence[Optional[int]], min_views: int, max_zero_run: int
) -> Optional[int]:
    """First index with >= min_views views after the last run of more than max_zero_run zero months."""
    start = 0
    run = 0
    for t, value in enumerate(views):
        if not value:
            run += 1
            if run > max_zero_run:
                start = t + 1
        else:
            run = 0
    for t in range(start, len(views)):
        value = views[t]
        if value is not None and value >= min_views:
            return t
    return None


def alive_from(
    views: Sequence[Optional[int]],
    *,
    min_views: int = T.ALIVE_MIN_VIEWS,
    max_zero_run: int = T.ALIVE_MAX_ZERO_RUN,
) -> Optional[int]:
    """Index of the first month with >= min_views views that is never followed by
    more than max_zero_run zero months in a row; None if there is no such month.
    Missing months (None) count as zeros."""
    return _first_after_last_long_zero_run(views, min_views, max_zero_run)


@dataclass(frozen=True)
class AliveStatus:
    alive: bool
    code: Optional[str] = None  # "late_start" | "below_alive_threshold" when not alive
    month: Optional[str] = None  # month the page became alive, for late_start


def status(views: Sequence[Optional[int]], first_month: str, window_start: int) -> AliveStatus:
    """Is the page alive from the window start, and if not, why.

    `late_start` means a new page: before the window start it had no views
    (or only views followed by a long zero run). `below_alive_threshold` means the
    page existed from the window start but was read too little to count as alive.
    """
    start = alive_from(views)
    if start is not None and start <= window_start:
        return AliveStatus(True)
    existed = _first_after_last_long_zero_run(views, 1, T.ALIVE_MAX_ZERO_RUN)
    if start is not None and (existed is None or existed > window_start):
        return AliveStatus(False, "late_start", months.add(first_month, start))
    return AliveStatus(False, "below_alive_threshold")
