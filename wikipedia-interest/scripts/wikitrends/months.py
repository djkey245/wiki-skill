"""Calendar month arithmetic on 'YYYY-MM' strings."""
from __future__ import annotations

import re
from typing import List, Tuple

_MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


def parse(month: str) -> Tuple[int, int]:
    match = _MONTH_RE.match(month)
    if not match:
        raise ValueError(f"invalid month {month!r}, expected YYYY-MM")
    return int(match.group(1)), int(match.group(2))


def to_index(month: str) -> int:
    year, mon = parse(month)
    return year * 12 + (mon - 1)


def from_index(index: int) -> str:
    year, mon0 = divmod(index, 12)
    return f"{year:04d}-{mon0 + 1:02d}"


def add(month: str, n: int) -> str:
    return from_index(to_index(month) + n)


def diff(a: str, b: str) -> int:
    """Number of months from b to a (a - b)."""
    return to_index(a) - to_index(b)


def span(start: str, end: str) -> List[str]:
    """All months from start to end inclusive."""
    first, last = to_index(start), to_index(end)
    if last < first:
        raise ValueError(f"end {end} is before start {start}")
    return [from_index(i) for i in range(first, last + 1)]
