"""R1 at level 0a (calibration §3.1, §5): the starting method only, on semi-synthetic series from
the corpus. False growing/declining without a trend, power for +20 %/yr, growing/declining with
high confidence for a step without a trend, false stable at +10 %/yr, test/CI agreement, and the
autocorr_capped rate on the real series. Large editions (primary basket >= 1000 views a month)
and small ones are reported apart. Level 0a has no held-out part: every number is preliminary."""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from wikitrends import confidence, metrics, trend
from wikitrends import thresholds as T

from . import semisynth

CONSTANT = 100_000_000  # the semi-synthetic share is written as views over a constant denominator
CONDITIONS = ("null", "trend20", "trend10", "step")


@dataclass
class Counts:
    n: int = 0
    hits: Dict[str, int] = field(default_factory=dict)

    def add(self, **flags: bool) -> None:
        self.n += 1
        for k, v in flags.items():
            self.hits[k] = self.hits.get(k, 0) + int(v)

    def rate(self, key: str) -> Optional[float]:
        return None if not self.n else round(self.hits.get(key, 0) / self.n, 4)


def primary_share(data: metrics.LangData, qids: Sequence[str], seeds: Sequence[str],
                  others: Dict[str, metrics.LangData]) -> Tuple[Optional[List[Optional[float]]], float]:
    """Share of the primary basket (common across the corpus languages) and its monthly views."""
    baskets = metrics.build_baskets(list(qids), list(seeds), others)[data.lang]
    if not baskets.primary:
        return None, 0.0
    views = metrics.aggregate(data.articles, baskets.primary)
    return metrics.share_ppm(views, data.denominator), metrics.monthly_views(views, data.window_start)


def assess_series(values: Sequence[Optional[float]], first_month: str, window_start: int) -> Optional[confidence.Assessment]:
    """The full check pipeline on one series, as a one-article cell (basket checks do not apply)."""
    views = [0 if v is None else int(round(v * CONSTANT / 1e6)) for v in values]
    data = metrics.LangData("xx", first_month, window_start, [CONSTANT] * len(views), {"Q1": metrics.Article("Q1", views, [0] * len(views))})
    baskets = metrics.build_baskets(["Q1"], [], {"xx": data})["xx"]
    if not baskets.primary:
        return None
    cell = metrics.compute_cell(data, baskets)
    return confidence.assess(data, baskets, cell, [], known_breaks=())


def run(series: Iterable[Tuple[str, List[Optional[float]], str, int, float]], *, shuffles: int = 10,
        seed: int = 20260927) -> Dict[str, Any]:
    """`series`: (key, share series, first month, window start, monthly views of the primary basket)."""
    rng = random.Random(seed)
    counts: Dict[Tuple[str, str], Counts] = {}
    capped = {"large": Counts(), "small": Counts()}
    agree = Counts()
    for key, share, first, start, views in series:
        size = "large" if views >= T.LOW_VOLUME_HIGH else "small"
        try:
            real = trend.estimate(share, first, start)
            capped[size].add(capped=real.autocorr_capped)
        except trend.NotEnoughPoints:
            continue
        for _ in range(shuffles):
            null = semisynth.null_series(share, first, start, rng)
            variants = {"null": null, "trend20": semisynth.with_trend(null, 20.0), "trend10": semisynth.with_trend(null, 10.0),
                        "step": semisynth.with_step(null, start + 12, 0.3)}
            for condition, values in variants.items():
                a = assess_series(values, first, start)
                if a is None:
                    continue
                c = counts.setdefault((condition, size), Counts())
                trended = a.direction in ("growing", "declining")
                c.add(trended=trended, growing=a.direction == "growing", stable=a.direction == "stable",
                      trended_high=trended and a.confidence == "high", step_like=any(r.code == "step_like" for r in a.reasons))
                if condition == "null":
                    est = trend.estimate(values, first, start)
                    low, high = est.growth_ci
                    agree.add(agree=(low > 0 or high < 0) == (est.p < T.ALPHA))
    out: Dict[str, Any] = {"shuffles": shuffles, "seed": seed, "sizes": {}}
    for size in ("large", "small"):
        get = lambda cond, k: counts.get((cond, size), Counts()).rate(k)  # noqa: E731
        out["sizes"][size] = {
            "series": counts.get(("null", size), Counts()).n,
            "false_trend": get("null", "trended"), "power_20": get("trend20", "growing"),
            "step_trended_high": get("step", "trended_high"), "step_like_on_step": get("step", "step_like"),
            "false_stable_at_10": get("trend10", "stable"), "autocorr_capped_real": capped[size].rate("capped"),
        }
    out["test_ci_agreement"] = agree.rate("agree")
    return out


CRITERIA = {  # calibration R1, level 0a: the starting method only
    "false_trend": ("<=", 0.10), "power_20": (">=", 0.70), "step_trended_high": ("<=", 0.10),
    "false_stable_at_10": ("<=", 0.10), "autocorr_capped_real": ("<=", 0.05),
}


def verdicts(report: Dict[str, Any]) -> Dict[str, Dict[str, Optional[bool]]]:
    """Pass / fail of every R1 criterion per size class; None when there is no data."""
    out: Dict[str, Dict[str, Optional[bool]]] = {}
    for size, values in report["sizes"].items():
        out[size] = {}
        for name, (op, limit) in CRITERIA.items():
            v = values.get(name)
            out[size][name] = None if v is None else (v <= limit if op == "<=" else v >= limit)
    return out


def corpus_series(corpus: Dict[str, Any], read: Any, window: Any, end: str) -> Iterable[Tuple[str, List[Optional[float]], str, int, float]]:
    """(key, share, first month, window start, views) of every topic x language for the window ending at `end`."""
    from .corpus import restore

    for topic in corpus["topics"]:
        raw = read(topic["id"])
        full = {lang: restore(lang, d) for lang, d in raw["langs"].items()}
        cut = {lang: window(d, end) for lang, d in full.items()}
        if any(v is None for v in cut.values()):
            continue
        for lang, data in cut.items():
            share, views = primary_share(data, topic["items"], [topic["qid"]], cut)
            if share is not None:
                yield f"{topic['id']}:{lang}", share, data.first_month, data.window_start, views
