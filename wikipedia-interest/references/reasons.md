# Reasons

Each reason in the output is `{code, severity, text}`; `text` is already a plain sentence in
the user's language. This file explains the codes when the user asks "why". Severity decides
the confidence (`references/methodology.md`): a blocking reason makes the direction `unclear`;
high and medium ones lower the confidence; low and info ones only explain.

## `not_significant`

Severity: blocking. The trend test is not significant and the interval is wider than the
±5 %/yr corridor. It does **not** mean a lack of data: the cell goes to `no_clear_trend`
("no trend detected"), which is common for large editions with noisy series.

## `low_volume`

Severity: high below 1000 views a month on the analysed basket; blocking below 100 or with
more than 20 % zero months (group `insufficient_data`). The text names the basket and its own
value: the table's views/month use the language's whole basket, while the check uses the
basket the trend is computed on, which may be smaller.

## `spike_driven`

Severity: high. Isolated months far above the rest (robust z > 3.5, the series returns to its
level afterwards) move the estimate across a category boundary, or halve the slope. Viral
news, a TV series, an anniversary. The months are listed in the detail.

## `unstable_basket`

Severity: high. Removing a single article changes the category of the estimate (up / flat /
down). The conclusion depends on that article; the detail lists it.

## `concentrated`

Severity: high. One article gives more than 60 % of the basket's change in views, unless it is
a seed whose own trend agrees. The basket's trend is mostly that article's trend.

## `autocorr_capped`

Severity: high. The series is so persistent that the autocorrelation correction hit its
ceiling (4×); the real interval may be wider than the one shown.

## `short_window`

Severity: medium. The window is shorter than 24 months; confidence is at most medium.

## `short_history`

Severity: medium. Less than 24 months of history before the window (a new article, or a window
starting near 2015-07), so the seasonal profile is rough. In this version confidence is low.

## `thin_basket`

Severity: medium. Only 2-3 articles in the basket; basket checks are weak.

## `single_article`

Severity: medium. The basket is one article; basket checks do not apply.

## `no_common_basket`

Severity: medium. No article is present in every language, so each language uses its own
articles and languages are compared only by their separate trends, with no pairwise test.

## `small_effect`

Severity: info. A statistically significant change inside the ±5 %/yr corridor: there is a
change, but it is too small to matter in practice. The group is `steady`.

## `step_like`

Severity: high. Checked for windows of 24 months or more with a significant trend. A step (two
levels with a break point between the 6th and the (n−6)th month) explains the deseasonalized
series better than a line: BIC of the step plus 2 is below BIC of the line, both on the series
prewhitened with the estimated autocorrelation. The change happened once, around the month in
the detail (a data change, a rename, a news event), rather than as an even trend.

## `seed_divergence`

Severity: high. The seeds alone have a significant trend of the opposite category to the
basket. The basket may have drifted to a different topic. When the seeds are absent in a
language the check is `not_checked` in `result.json`.

## `data_break`

Severity: medium. A known structural change of Wikimedia data lies inside the window
(`references/data-breaks.md`) and the level of the series shifts at it by more than 3 typical
6-month shifts of the history. Part of the change may be the data, not the interest.

## `trend_reversal`

Severity: medium. The estimate is up (or down), while the last 6 months differ from the same
months a year earlier by more than 10 % the other way. The direction may have turned recently.

## `recent_only`

Severity: medium. Checked for windows of 24 months or more. Without the last 3 months the
category changes or the slope halves: the conclusion rests on a shift at the very end.
Isolated spikes are `spike_driven` instead, never both.

## `redirect_heavy`

Severity: low. Redirects give more than half of the basket's views. Not a defect, but a
renamed or merged article may explain part of the series.

## `data_revised`

Severity: info. During this `analyze` Wikimedia's numbers for settled months of the edition
changed (a recalculation); the cache was refreshed and the result uses the new data.

## `metric_disagreement`

Severity: info. The share and raw views point in opposite directions (one up, the other
down). Not a defect: the edition's traffic changed. Explain both answers; `spec edit --metric
views` makes raw views the main metric.

## `project_trend`

Severity: info. The edition's own traffic trend, given whenever the share and raw views fall in
different categories: it explains the gap between them.

## `difference_not_significant`

A property of a pair, not of a cell. The pairwise test of two languages (or topics) is not
significant after the Benjamini–Hochberg correction. Never say that one grows faster than the
other, even when they are in different groups or their estimates look far apart.
