# Methodology

How `wikitrends.py` turns pageviews into trends, groups and recommendations. Every threshold
lives in `scripts/wikitrends/thresholds.py` and is listed at the end of this file. Values are
starting points; the calibration log records the values that replaced them.

## Data and API limits

- Views: Wikimedia Analytics API (AQS), `agent=user`, `all-access`, monthly. A month missing
  from a `per-article` answer is zero; a 404 gives zeros only for the requested range.
- The share's denominator is the edition's `aggregate` minus the views of its main page.
  `aggregate` includes every namespace (special pages, search, files); AQS has no namespace
  breakdown.
- Limits (checked 2026-09-27, [Wikimedia APIs/Rate limits](https://www.mediawiki.org/wiki/Wikimedia_APIs/Rate_limits)):
  without authentication and with a proper User-Agent, 200 requests a minute across all
  Wikimedia APIs (10 without a User-Agent; 2000 for an authenticated experienced editor);
  at most 3 parallel requests; above that, HTTP 429 with `Retry-After`. The skill keeps
  `RATE_LIMIT_PER_MINUTE` and `AQS_PARALLELISM` whatever `WIKIMEDIA_API_TOKEN` says: the
  class of an authenticated client depends on the account.
- Consequence: about 300 uncached requests fit into the 90 s deadline. `analyze` may make 600
  (about two calls), `suggest` 250; bigger jobs run with `--allow-large` over several calls
  through the cache.
- MediaWiki Action API: sequential, batches of 50, `maxlag=5` (API:Etiquette). Wikidata:
  sequential and batched, without `maxlag` (Wikidata adds the Query Service lag, which often
  exceeds 5 s and does not affect reads).
- Redirect ranking (`prop=pageviews`, 60 days) returns data for only 5 titles a request when
  the titles are not in the PageViewInfo cache, so the budget counts ⌈redirects / 5⌉ requests.
- Cache: months older than 2 before the window end never change; `aggregate` is requested
  again at every `analyze`, and a change above 0.5 % in a settled month invalidates the
  edition's article cache (`data_revised`). A recalculation of single articles without a change
  in `aggregate` goes unnoticed.

## Window and history

The window ends at the latest month published in every language both in `aggregate` and in
`per-article`. It is 24 months by default and at least 12. History of up to 36 months before
the window start (never before 2015-07) is loaded in the same requests; it gives the seasonal
profile and the autocorrelation. Metrics and the trend use the window only.

## Baskets

A topic is a basket of Wikidata items. An article is "alive" from the first month with at least
`ALIVE_MIN_VIEWS` views (article plus its redirects) that is not followed by more than
`ALIVE_MAX_ZERO_RUN` zero months. Articles alive from the window start form three baskets:

- `common`: items present and alive in every language of the spec. The trend, the group,
  `level_ppm` and the pairwise tests use it, so every language is measured on the same items.
- `local`: items present and alive in one language. `monthly_views` (size and the views
  criterion) uses it, so adding a language never changes the size of the others.
- `seed`: the seeds only.

If `common` is empty, each language uses `local` (`no_common_basket`) and languages are not
tested against each other. Excluded items carry a reason: `missing_in_lang`, `late_start`,
`below_alive_threshold`, `duplicate_of`, `fetch_failed`. Articles created inside the window are
not in the trend: growth through new subtopics is not counted.

## Metrics

- `share_ppm`: basket views / (edition views − main page views) × 10⁶, the default main
  metric. `options.metric: views` (`spec edit --metric views`) makes raw views the main metric.
- `level_ppm`: mean share over the last 12 months.
- `index`: the main metric / its mean over the first 12 months of the window × 100.
- `monthly_views`: mean views over the last 12 months. These are views of the basket's
  articles, not people: a reader of three articles counts three times.
- `growth_pct_yr`: the slope of the deseasonalized log of the main metric, as
  `(exp(12·slope) − 1) × 100`; `growth_ci` is its 95 % interval. `raw_growth_pct_yr` and
  `raw_growth_ci` are the same on the other metric (raw views for the share, the share for
  views), a control with the same uncertainty.
- `recent_yoy_pct`: the last 6 months of the main metric against the same months a year earlier.
- `contributors`: each item's part of the change in views.

## Trend

1. Seasonal profile from history and window: log share minus a centred 12-month moving
    average, the median deviation of each calendar month, centred at zero.
2. Deseasonalize the window.
3. Theil–Sen slope on the window.
4. AR(1) φ on the residuals of local trends in the history and in the window, with a bias
    correction, clamped to [0, 0.99]; the Hamed–Rao variance factor with ρ_k = φ^k, clamped to
    [1, `MAX_VARIANCE_INFLATION`] (`autocorr_capped` at the ceiling).
5. Mann–Kendall test and 6. Sen interval with the same corrected variance, so the test and the
    interval never disagree.

With less than `MIN_HISTORY_FOR_PROFILE` months of history the estimate gets `short_history`
and, in this version, low confidence. The skill says that a change happened in the window; it
never forecasts that it continues.

## Direction and confidence

| Condition | `direction` |
|---|---|
| The whole interval within ±`FLAT_PCT` | `stable` (`small_effect` if p < 0.05) |
| p < `ALPHA` and \|growth\| ≥ `FLAT_PCT` | `growing` / `declining` |
| p < `ALPHA`, \|growth\| < `FLAT_PCT`, interval beyond the corridor | `small_effect` |
| otherwise | `unclear` (`not_significant`) |

A blocking reason makes the direction `unclear` and the confidence `low`. `high`: a clear
direction, no reason of high severity, at most one medium. `medium`: one high reason and no
medium, or two medium. `low`: everything else. Windows under 24 months cap it at `medium`.
Reasons: `references/reasons.md`.

Checks run on the main metric and use the category of the point estimate (up ≥ +`FLAT_PCT`,
down ≤ −`FLAT_PCT`, flat between), never the p-value:

- `step_like`: for a significant trend in a window of 24+ months, a step with a break point
  between month `STEP_MIN_SEGMENT` and n − `STEP_MIN_SEGMENT` beats the line by more than
  `BIC_STEP_MARGIN` in BIC, both fitted on the Cochrane–Orcutt prewhitened series.
- `recent_only`: without the last `RECENT_ONLY_MONTHS` months (isolated spikes masked) the
  category changes, or for up/down the slope halves.
- `trend_reversal`: up/down while `recent_yoy_pct` goes the other way by more than
  `TREND_REVERSAL_PCT`.
- `seed_divergence`: the seeds' own trend is significant and of the opposite category.
- `redirect_heavy`: redirects give more than `REDIRECT_HEAVY_SHARE` of the views.
- `metric_disagreement`: the main and the other metric have opposite categories;
  `project_trend` gives the edition's traffic trend whenever their categories differ.
- `data_break`, `data_revised`: see "Data breaks and revisions".

## Groups, matrix and comparisons

| `direction` | high / medium | low | only `not_significant` blocks | `low_volume` blocks |
|---|---|---|---|---|
| growing | `promising` | `emerging` | | |
| stable, small_effect | `steady` | `no_clear_trend` | | |
| declining | `weakening` | `no_clear_trend` | | |
| unclear | | | `no_clear_trend` | `insufficient_data` |

- Size: `monthly_views` at or above the median of the compared items is "high volume"
  (`criteria.size_axis: level_ppm` splits by the share instead: "high share");
  the matrix needs at least `MIN_MATRIX_ITEMS` items, otherwise a table without quadrants.
- Pairwise tests: the trend method on log(A / B) of the main metric for every pair of languages
  within a topic and of topics within a language, Benjamini–Hochberg within the family. A
  non-significant pair (`difference_not_significant`) never supports "A grows faster than B".
- User criteria never change groups or confidence. The growth criterion is `met` only for
  `promising` (or `steady` when the threshold is ≤ 0); `emerging` and `no_clear_trend` above the
  threshold are `unconfirmed`.
- Recommendations are ordered lexicographically: quadrant (high volume before a niche), then
  criteria status, then growth. "Explore next" takes up to `EXPLORE_MAX` `promising`/`emerging`
  items; "postpone" takes `weakening`, `insufficient_data` and items that fail a criterion.

## Output and report

- Stdout shows at most `MAX_REASONS_SHOWN` reasons a cell and `MAX_EXCLUDED_SHOWN` excluded
  items. Above `MAX_DETAILED_CELLS` cells it summarises groups and details only `promising`,
  `emerging`, `weakening` and cells meeting the criteria; high-severity reasons of the other
  cells are grouped by code, never dropped. `result.json` keeps everything.
- The report (`report.py`) reads only `result.json`. Charts per topic: `index` over time (at
  most `CHART_MAX_SERIES` coloured languages, known data breaks marked), growth with its 95 %
  CI coloured by group, and the size × dynamics matrix (size axis on a log scale, the median
  line, hollow markers for cells failing the criteria; only with at least `MIN_MATRIX_ITEMS`
  items). `report.md` is the untruncated answer with every chart; the one-page PDF shows the
  three charts of the best topic and the same text.

## Data breaks and revisions

- Known structural changes of the data are listed in `references/data-breaks.md`. For each
  break inside the window the level shift of the deseasonalized log metric at the break (median
  of `BREAK_SIDE_MONTHS` months after minus the same before) is compared with the typical shift
  in the history before the window; above `BREAK_SHIFT_FACTOR` typical shifts the cell gets
  `data_break` (medium). On clean simulated series (σ = 0.15) 1× flags about half of them, 3×
  about 6 %, while a 30 % step at the break is found in about 80 %.
- `aggregate` is requested again at every `analyze`; a settled month that changed by more than
  `REVISION_TOLERANCE` invalidates the edition's cached article months, adds the `data_revised`
  warning and an info reason to the edition's cells, and `changes` says that the previous result
  used the old data.

## Limitations

- A language edition is not a market: speakers read other editions (often English), and the
  English edition is global.
- Views are not people.
- AI answers in search engines and chat bots take some traffic away from Wikipedia, unevenly
  by kind of topic (practical and health questions more than, say, astronomy). A falling share
  may reflect a change of channel, not of interest.
- One topic may be split into a different number of articles in different editions, so
  `level_ppm` is only partly comparable between languages.

## Thresholds

| Name | Value | Meaning |
|---|---|---|
| `EARLIEST_MONTH` | 2015-07 | first month of AQS pageviews |
| `PROVISIONAL_MONTHS` | 2 | last months of the window that may still change |
| `PROVISIONAL_TTL_SECONDS` | 2 days | cache lifetime of provisional months |
| `NOT_FOUND_TTL_SECONDS` | 7 days | cache lifetime of a 404 |
| `METADATA_TTL_SECONDS` | 7 days | cache lifetime of metadata |
| `REVISION_TOLERANCE` | 0.5 % | change of a settled `aggregate` month that counts as a revision |
| `AQS_PARALLELISM` | 3 | parallel AQS requests |
| `RATE_LIMIT_PER_MINUTE` | 200 | request starts a minute, all APIs together |
| `ACTION_API_BATCH` | 50 | titles or items per Action API / Wikidata request |
| `REQUEST_TIMEOUT_SECONDS` | 20 | timeout of one request |
| `MAX_RETRIES` | 4 | retries on 429 / 5xx |
| `BACKOFF_BASE_SECONDS` | 1 | first retry delay |
| `BACKOFF_MAX_SECONDS` | 30 | longest retry delay |
| `MAXLAG_SECONDS` | 5 | `maxlag` for Wikipedia editions |
| `DEFAULT_MAX_SECONDS` | 90 | deadline of a network command |
| `ALPHA` | 0.05 | significance level |
| `MAX_VARIANCE_INFLATION` | 4 | ceiling of the autocorrelation correction |
| `MIN_HISTORY_FOR_PROFILE` | 24 months | history needed for the main method |
| `ALIVE_MIN_VIEWS` | 30 | views a month that make an article alive |
| `ALIVE_MAX_ZERO_RUN` | 2 | zero months allowed after that |
| `BASE_MONTHS` | 12 | base of `index` |
| `LEVEL_MONTHS` | 12 | months of `level_ppm` and `monthly_views` |
| `YOY_MONTHS` | 6 | months of `recent_yoy_pct` |
| `MIN_WINDOW_FOR_YOY` | 18 months | window needed for `recent_yoy_pct` |
| `FLAT_PCT` | 5 %/yr | corridor of practical significance |
| `LOW_VOLUME_HIGH` | 1000 | views a month below which `low_volume` is high |
| `LOW_VOLUME_BLOCKING` | 100 | views a month below which `low_volume` blocks |
| `MAX_ZERO_SHARE` | 20 % | zero months above which `low_volume` blocks |
| `SPIKE_Z` | 3.5 | robust z of a spike |
| `SPIKE_SLOPE_DROP` | 50 % | slope drop without spikes that gives `spike_driven` |
| `CONCENTRATED_CONTRIBUTION` | 60 % | share of the change from one article (`concentrated`) |
| `SHORT_WINDOW_MONTHS` | 24 | window below which `short_window` applies |
| `THIN_BASKET_MAX` | 3 | basket size up to which `thin_basket` applies |
| `HISTORY_BEFORE_WINDOW` | 36 months | history loaded before the window |
| `AVAILABILITY_PROBE_MONTHS` | 3 | months probed for the latest published month |
| `MAX_REDIRECTS` | 10 | redirects taken per article |
| `REDIRECT_RANK_DAYS` | 60 | days of recent views that rank redirects |
| `PAGEVIEWS_TITLES_PER_REQUEST` | 5 | titles with data per `prop=pageviews` request |
| `SEARCH_LIMIT` | 10 | results of a topic search |
| `MORELIKE_LIMIT` | 20 | results of `morelike` |
| `MAX_LINK_PAGES` | 3 | pages of outgoing links per seed |
| `MAX_REQUESTS` | 600 | request budget of `analyze` |
| `MAX_SUGGEST_REQUESTS` | 250 | request budget of `suggest` |
| `DEFAULT_PERIOD_MONTHS` | 24 | default window |
| `MIN_PERIOD_MONTHS` | 12 | shortest window |
| `MAX_QIDS_PER_TOPIC` | 50 | items per topic |
| `MAX_TOPICS` | 5 | topics per spec |
| `MAX_LANGS` | 10 | languages per spec |
| `SLUG_MAX_LENGTH` | 40 | length of a spec directory name |
| `FIND_CANDIDATES` | 5 | candidates shown by `find` |
| `DRAFT_CANDIDATES` | 8 | candidates in the draft basket |
| `CANDIDATES_FOR_METADATA` | 60 | candidates that get Wikidata data and a backlink check |
| `SUGGEST_LIMIT` | 30 | candidates listed by `suggest` |
| `CANDIDATE_WEIGHTS` | 1 / 1 / 0.5 / 1 | weights of morelike, links, backlinks, Wikidata relations |
| `EXCLUDED_TYPES` | 14 types | direct `P31` values never suggested (people, countries, cities, years, languages, lists…) |
| `DISAMBIGUATION` | Q4167410 | disambiguation pages, never a topic |
| `MIN_MATRIX_ITEMS` | 3 | items needed for the size × dynamics matrix |
| `EXPLORE_MAX` | 3 | items in "explore next" |
| `MAX_REASONS_SHOWN` | 5 | reasons per cell in stdout |
| `MAX_EXCLUDED_SHOWN` | 10 | excluded items in stdout |
| `MAX_DETAILED_CELLS` | 9 | cells above which stdout summarises |
| `PDF_MAX_CHARTS` | 3 | charts on the PDF page |
| `PDF_TABLE_ROWS` | 14 | table rows on the PDF page |
| `PDF_HEADLINE_LINES` | 6 | headline lines on the PDF page |
| `PDF_FONT_SIZES` | 7 → 5 pt | text sizes tried on the PDF page |
| `PDF_LINE_SPACING` | 1.3 | line spacing of the PDF text |
| `PDF_WRAP_AT_7PT` | 125 | characters a line at 7 pt |
| `PDF_TABLE_ROW_HEIGHT` | 1.6 % | table row height, of the page |
| `CHART_MAX_SERIES` | 4 | coloured lines per chart |
| `BIC_STEP_MARGIN` | 2 | BIC gain of the step over the line that gives `step_like` |
| `STEP_MIN_SEGMENT` | 6 months | shortest level on either side of a step |
| `RECENT_ONLY_MONTHS` | 3 | last months dropped by the `recent_only` check |
| `TREND_REVERSAL_PCT` | 10 % | opposite `recent_yoy_pct` that gives `trend_reversal` |
| `REDIRECT_HEAVY_SHARE` | 50 % | share of views from redirects (`redirect_heavy`) |
| `BREAK_SIDE_MONTHS` | 6 | months on each side of a data break |
| `BREAK_SHIFT_FACTOR` | 3 | typical 6-month shifts that make a break's shift notable (`data_break`) |
