---
name: wikipedia-interest
description: >-
  Analyse interest in topics from Wikipedia pageviews across language editions: trends,
  comparing languages and topics, how reliable the growth is, charts and a one-page PDF
  report. Use it for decisions about topics, courses and localization ("which audiences to
  explore next", "is interest in X growing in Polish and Czech"). Triggers in any language,
  e.g. Ukrainian: Вікіпедія, перегляди, інтерес до теми, мовний розділ, популярність теми;
  Polish: Wikipedia, wyświetlenia, zainteresowanie tematem; English: Wikipedia pageviews,
  topic interest, language edition.
compatibility: >-
  Python >= 3.9 (standard library only for the analysis). uv only for the report (or an
  installed matplotlib). Network access to *.wikipedia.org, wikimedia.org, www.wikidata.org.
---

# Wikipedia interest

Answer questions about interest in topics by pageviews of Wikipedia language editions.
The scripts do all the numbers; you choose commands, show the answer and explain it.

## 1. Setup

- `<skill_dir>` is the absolute path of the directory that holds this file.
- Analysis: `python3 <skill_dir>/scripts/wikitrends.py <command> …`
- Report: `uv run <skill_dir>/scripts/report.py <spec.json> …`
- Every command prints one JSON object. Take the next commands and all paths from its
  `next_steps` and other fields; never guess paths.
- Every command that creates a spec gets `--display-lang <the user's language code>`.

## 2. Decision tree

| Situation | Do |
|---|---|
| The user names no languages ("in our chosen editions") | Ask which languages. Offer 3-5 editions with a one-line reason each. Never pick languages yourself |
| One concept (astronomy, intermittent fasting), languages known | `quick "<query>" --lang <query language> --langs <codes> --display-lang <user language>` |
| An intent no single article covers (learning English, exam preparation) | `find` 2-3 wordings of the intent, choose 1-3 seeds, then `quick "<topic name>" --seed Q.. --seed Q.. --langs … --display-lang …` |
| Several topics, or the user wants to shape the basket | `find` → `suggest --seed Q.. --langs … --display-lang …` → (`spec edit`) → `analyze <spec>`; add topics with `suggest … --spec <spec>` |
| Specific dates ("since 2020", "2023 vs 2024") | `--start YYYY-MM [--end YYYY-MM]` in `quick` or `spec edit`, not `--period` |
| The user states criteria ("from 50k views a month", "growth from 15 %") | `spec edit <spec> --criteria min_monthly_views=50000,min_growth_pct_yr=15`, then `analyze <spec>` |
| A refinement (add a language, change the period or the basket) | `spec edit <spec> …` then `analyze <spec>` on the same file; explain `changes` |
| A new session refers to earlier work ("that astronomy report") | `ls`, then `spec edit` + `analyze` of the spec found |
| The user wants a report, PDF or chart | the `uv run … report.py …` command from `next_steps`; add `--out <dir>` if the user can only see files in one directory; give the absolute path of the file |
| `status: needs_user`, `code: ambiguous_topic` | Show the candidates (labels and descriptions) and ask which one is meant |
| `partial: true` | Run the command in `next_steps` (after `quick` it is `analyze <spec>`); do not give up and do not rerun `quick` |
| `code: network_blocked` | Ask the user to allow the domains listed in the error; do not retry |
| `code: network_unavailable` | Say there is no network access; do not ask to allow domains |
| `code: report_deps_missing` | Run the command from `hint` |
| `workdir_fallback` in `warnings` | Nothing; keep taking paths from the output |
| Any other error | Do what its `hint` says; if it is `internal_error`, tell the user and do not retry in a loop |

Commands:

```
quick "<query>" --langs uk,pl,cs --display-lang uk (--lang uk | --seed Q.. [--seed Q..]) [--period 24m | --start YYYY-MM [--end YYYY-MM]]
find "<query>" --lang uk
suggest --seed Q.. [--seed Q..] --langs uk,pl,cs --display-lang uk [--topic "<name>"] [--spec <spec>]
spec edit <spec> [--topic <id>] [--add Q..] [--drop Q..] [--add-topic "<name>" --seed Q..] [--drop-topic <id>]
                 [--add-lang xx] [--drop-lang xx] [--period 36m | --start YYYY-MM [--end YYYY-MM]]
                 [--criteria min_monthly_views=50000,min_growth_pct_yr=15] [--clear-criteria]
analyze <spec> [--allow-large] [--max-seconds 90]
ls
uv run <skill_dir>/scripts/report.py <spec> [--format pdf,png,md] [--topic <id>] [--lang xx] [--out <dir>]
```

`--lang` is the language the query is written in; `--langs` are the editions to analyse;
`--display-lang` is the language of the user. A Ukrainian user may search an English term
with `--lang en` and still gets `--display-lang uk`.

## 3. A topic is the user's intent

A topic is a basket of Wikidata items, not one article. "Learning English" means seeds like
English as a second language, IELTS, TOEFL, not the article about the English language.
Do not ask the user to confirm the basket each time: take the draft from `quick`/`suggest`,
show it in the answer by labels, and change it only with `spec edit`.

## 4. Hard rules

- Never compute numbers yourself. Copy the formatted values from `answer_md` and the output.
- Never edit spec or result JSON by hand. Change specs only with `spec edit`.
- `emerging` is growth that is **not confirmed**; say so and give its reasons.
  `no_clear_trend` means no trend was detected, not a lack of data.
  Only `promising` is "confirmed growth". Never call growth "strong" or "reliable".
- With `difference_not_significant` never say one language or topic grows faster than the
  other, even when they are in different groups.
- Mention every reason of high severity in plain words (its `text`).
- Always say that a language edition is not a market and that views are not people.
- Never claim that a trend will continue.
- Never build charts or PDFs with your own code: use `report.py`.

## 5. The answer

Take `answer_md` from the output and adapt its style and language without changing numbers,
groups, reasons or the recommendation order. If `text_lang` differs from the user's language,
translate it, keeping every number. Keep this skeleton:

1. The conclusion in one sentence (`headline`).
2. The table (group, growth, CI, share ppm, views/month, confidence, criteria).
3. Recommendations: whom to explore next and why, exactly as in `answer_md`.
4. Reasons in plain words.
5. Assumptions: the basket by labels, the metric, the period.
6. What to check next (a concrete next step).

## 6. References

Read only when needed:

- `references/reasons.md`: what each reason code means, when the user asks "why".
- `references/methodology.md`: metrics, statistics, thresholds, data limits.
- `references/spec-format.md`: the spec file and `spec edit` examples.
- `references/output-schema.json`: the exact output format.
