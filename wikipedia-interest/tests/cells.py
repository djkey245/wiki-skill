"""Build LangData / cells from synthetic article series (tests only)."""
from synthetic import make_share_series
from wikitrends import metrics

FIRST = "2020-01"
DENOMINATOR = 100_000_000  # constant project traffic: share_ppm = views / 100


def article(qid, values, redirect_fraction=0.0):
    views = [int(round(v)) for v in values]
    return metrics.Article(qid, views, [int(round(v * redirect_fraction)) for v in views])


def lang_data(articles, *, window_start=36, lang="pl", denominator=None, exclusions=None):
    n = len(next(iter(articles.values())).views)
    return metrics.LangData(
        lang=lang,
        first_month=FIRST,
        window_start=window_start,
        denominator=denominator or [DENOMINATOR] * n,
        articles=articles,
        exclusions=exclusions or {},
    )


def series(level, seed, *, n_history=36, n_window=24, **kwargs):
    return make_share_series(n_history=n_history, n_window=n_window, level=level, seed=seed, **kwargs)


def cell_of(articles, seeds=(), **kwargs):
    """(data, baskets, cell) for one language."""
    data = lang_data(articles, **kwargs)
    qids = list(articles)
    baskets = metrics.build_baskets(qids, list(seeds), {data.lang: data})[data.lang]
    return data, baskets, metrics.compute_cell(data, baskets)
