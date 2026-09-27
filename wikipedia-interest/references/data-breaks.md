# Known data breaks

Structural changes of Wikimedia pageview data that can shift a series without any change in
interest. `scripts/wikitrends/breaks.py` holds the same list. A break is not flagged just
because the window crosses it: the share partly cancels it, since it hits both the articles and
the edition. For every break inside the window, `analyze` compares the level shift of the
deseasonalized log metric at the break (median of the 6 months from the break minus the median
of the 6 months before) with the typical such shift in the history before the window. Above
`BREAK_SHIFT_FACTOR` typical shifts the cell gets `data_break`; `result.json` keeps every check
(`significant`, `not_significant`, or `not_checked` with fewer than 6 months on a side or too
short a history).

| Id | First month | What changed | Source |
|---|---|---|---|
| `automated_agent` | 2020-04 | AQS started to tag traffic of spammers and bots that pose as users as `agent=automated`; before that part of it counted as `user` | [Bot or Not? (Wikimedia Tech blog, 2020-10-05)](https://techblog.wikimedia.org/2020/10/05/bot-or-not-identifying-fake-traffic-on-wikipedia/), [Wikitech: Bot detection](https://wikitech.wikimedia.org/wiki/Data_Platform/Data_Lake/Traffic/BotDetection) |
| `bot_detection_2025` | 2025-03 | Updated bot detection; traffic of March–August 2025 reclassified, much of the apparent human traffic of May–June 2025 turned out to be bots | [New user trends on Wikipedia (Diff, 2025-10-17)](https://diff.wikimedia.org/2025/10/17/new-user-trends-on-wikipedia/) |

Live verification (plan 09 Part B): comparing monthly `aggregate` pageviews for `user` and
`automated` on `en.wikipedia` and `pt.wikipedia` across 2024-09..2025-12 confirmed that AQS was
reprocessed retroactively. `automated` traffic climbed sharply from 2025-04/05 onwards while
`user` traffic showed no anomalous May–June 2025 spikes. Therefore `bot_detection_2025` is confirmed
at first month `2025-03`.
