"""Request budget (data §4): estimate uncached requests before collecting, refuse above the limit."""
from __future__ import annotations

from . import thresholds as T
from .errors import WikitrendsError


def metadata_requests(n_items: int, n_langs: int, kinds: int) -> int:
    """Batched Action API / Wikidata requests: ceil(items / 50) x languages x metadata kinds."""
    return -(-n_items // T.ACTION_API_BATCH) * n_langs * kinds


def check(estimate: int, *, limit: int, allow_large: bool, command: str = "analyze") -> None:
    if allow_large or estimate <= limit:
        return
    hint = (
        "Narrow the basket or lower max_redirects, or rerun with --allow-large"
        if command == "analyze"
        else "Use fewer seeds or languages, or rerun with --allow-large"
    )
    raise WikitrendsError(
        "budget_exceeded",
        f"{command} would make about {estimate} uncached requests, the limit is {limit}",
        hint=hint,
        estimate=estimate,
        limit=limit,
    )
