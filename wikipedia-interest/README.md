# wikipedia-interest

An [Agent Skill](https://agentskills.io/specification) that analyses interest in topics from
Wikipedia pageviews across language editions: trends, comparisons of languages and topics, how
reliable the growth is, charts and a one-page PDF report. It helps founders of B2C products decide
which topics to develop next and which languages to launch in.

It is built for a fast, inexpensive model (Claude Haiku 4.5 or similar): the scripts compute every
number and write the skeleton of the answer; the model picks commands from a decision tree in
[`SKILL.md`](SKILL.md) and adapts the answer to the user.

Typical questions:

- "Compare the growth of interest in intermittent fasting in the Polish and Czech Wikipedias over
  the last two years."
- "We are thinking of adding an astronomy course. Is interest growing in the Ukrainian Wikipedia,
  and how far can we trust that growth?"
- "We build a language-learning app. Compare interest in learning English in our chosen editions
  and prepare a short report: which audiences should we explore next, and why?"

## How it works

- **A topic is a basket of Wikidata items** that describes the user's intent ("learning English" →
  English as a second language, IELTS, TOEFL, …), not a single article. The code ranks candidates
  (`morelike:` search, links of seed articles, Wikidata type filter); the agent adds or removes
  concepts; the code builds a coverage matrix across languages.
- **The metric is a share** of the edition's traffic without the main page (views per million),
  so growth of Wikipedia as a whole does not look like growth of the topic. Raw monthly views are
  reported for volume.
- **Statistics:** a long history before the window gives a seasonal profile; the deseasonalised
  series gets a Theil–Sen slope and a Mann–Kendall test with the Hamed–Rao correction for AR(1)
  autocorrelation. Languages and topics are compared with a test on the log ratio of shares, with
  Benjamini–Hochberg correction, not by eyeballing overlapping intervals.
- **Trust:** every result has a direction (`growing`, `falling`, `stable`, `unclear`,
  `small_effect`), a confidence level and reason codes in plain language: spikes, a step instead of
  a trend, known data breaks and bot-detection changes, revised data, thin or unstable baskets,
  disagreement between metrics, a trend of the whole project and more.
- **Recommendations** without a single opaque score: groups from a full decision table and a
  "size × dynamics" matrix; the user's own criteria (minimum views, minimum growth, size axis) mark
  the cells that do not meet them.
- **State between requests:** each study is a spec file; refinements (another language, period,
  metric, basket) edit it in place, results are versioned and the answer explains what changed.
  A repeated question on the same topic finds the existing spec and says when newer data is
  available.
- **Report:** three charts, `report.md`, a PNG and a one-page PDF, byte-for-byte deterministic.

What the skill claims is **detection**: in the analysed window interest changed as stated, beyond
noise, seasonality and known data artefacts. It never forecasts that a trend continues. A language
edition is not a market, and views are not people. See
[`references/methodology.md`](references/methodology.md#limitations).

## Requirements

- Python ≥ 3.9. The analysis (`scripts/wikitrends.py`) uses the standard library only.
- [uv](https://docs.astral.sh/uv/) for the report only: `scripts/report.py` declares matplotlib with
  PEP 723 metadata and pins it in `scripts/report.py.lock`. An installed matplotlib works too.
- Network access to `*.wikipedia.org`, `wikimedia.org` and `www.wikidata.org`.

## Install

Copy this directory into the agent's skills directory, for example
`~/.claude/skills/wikipedia-interest/` or `<project>/.claude/skills/wikipedia-interest/`.
Nothing else to install.

## Commands

The agent runs these; people can too. Every command prints one JSON object with the paths and the
next steps.

```
python3 scripts/wikitrends.py quick "астрономія" --lang uk --langs uk,pl,cs --display-lang uk
python3 scripts/wikitrends.py find "intermittent fasting" --lang en
python3 scripts/wikitrends.py suggest --seed Q333 --langs uk,pl,cs --display-lang uk
python3 scripts/wikitrends.py spec edit <spec.json> --add-lang sk --metric views
python3 scripts/wikitrends.py analyze <spec.json>
python3 scripts/wikitrends.py ls
uv run scripts/report.py <spec.json>
python3 scripts/wikitrends.py cache info
python3 scripts/wikitrends.py cache clear --older-than 90d
```

State lives in `$WIKITRENDS_HOME` (default `~/.local/share/wikipedia-interest/`, or `./.wikitrends/`
in a sandbox that allows writing only there); the SQLite cache in `$WIKITRENDS_CACHE_DIR` (default
`~/.cache/wikipedia-interest/`). Deleting the cache changes speed, never results.
`WIKITRENDS_CONTACT` sets the contact in the User-Agent; `WIKIMEDIA_API_TOKEN` is sent as a bearer
token if set.

Every command fits into about 90 seconds (below a typical agent's tool timeout); longer work
continues on the next call thanks to the cache.

## Development

```
uv sync --dev
uv run pytest                                              # all tests
uv run --python 3.9 --no-dev --with pytest pytest          # the core without dev dependencies
uv run pytest -m live                                      # live smoke tests against Wikimedia
python3 evals/run.py --help                                # agent evals (Claude Code, OpenRouter :free)
python3 evals/calibration/calibrate.py --help              # threshold calibration
```

CI runs the core on Python 3.9 and 3.12, with and without dev dependencies. Statistics are
cross-checked against `scipy` and `pymannkendall`; API behaviour is recorded in `tests/fixtures/`
and replayed without the network.

## Layout

| Path | What |
|---|---|
| `SKILL.md` | the decision tree for the agent |
| `scripts/wikitrends.py`, `scripts/wikitrends/` | the analysis core, standard library only |
| `scripts/report.py` | the report: charts, `report.md`, one-page PDF |
| `references/` | methodology with every threshold, reason codes, spec format, output schema, known data breaks |
| `tests/` | unit, contract, cache, record/replay and report tests; recorded API fixtures |
| `evals/` | agent evals (scenarios, runners, judge checklist) and threshold calibration |

The skill itself (`SKILL.md`, `scripts/`, `references/`) is about 1.5 MB; most of the directory is
recorded API fixtures used by the tests. The calibration corpus (`evals/calibration/data/`, level
0a, 20 topics × 5 languages) is about 500 KB; recorded eval fixtures (`evals/corpus/`) are about
9 MB.

## Roadmap

The skill answers the basic requests end to end. Next iterations, roughly in order:

1. **Refinements of the analysis:** a fallback for short history (Seasonal Sen / Seasonal Kendall
   inside the window), equal-weight growth with a `weighting_sensitive` reason, a penalty for too
   generic candidates, article-share concentration.
2. **Calibration at scale:** a full stratified corpus of topics and languages with a held-out
   part; alternatives of the trend method; the period and corridor on a grid; reason thresholds
   under a total false-alarm budget; retrospective checks (did detected trends hold later?);
   manual labelling of baskets; report reading tests with people; monitoring of data revisions.
3. **Larger volumes:** Wikimedia pageview dumps and a columnar store for hundreds of articles and
   dozens of languages instead of per-article API calls; a cache shared by a team.
4. **Harder studies:** desktop vs mobile; daily data for events and short periods; automatic basket
   growth with control of topic drift; new articles as a signal; external data on the size of
   language audiences; clickstream data for better candidates.
5. **Construct validity:** compare pageview trends with Google Trends on a sample of topics,
   separately for topics that AI answers in search and chat bots take away from Wikipedia.
6. **Presentation:** a detailed visual design of the charts and the PDF, an interactive HTML report.

Each step is added the same way the current skill was built: a design note, tests first (synthetic
series with a known answer, recorded API fixtures), then agent evals on a small model to check that
`SKILL.md` still leads it to the right commands.

## How AI was used and how its work was checked

The design, the code and the tests were written with AI assistants (Claude). The results were
checked independently of the model that wrote them:

- Statistics are compared with reference implementations (`scipy`, `pymannkendall`) and with
  synthetic series whose answer is known, not with expectations written by the same model.
- API behaviour (batching, redirects, rate limits, bot-detection changes) was checked with live
  requests before it went into the code; the answers are recorded as fixtures and replayed in tests.
- Thresholds start from documented values and are replaced by measured ones from calibration;
  both are listed in [`references/methodology.md`](references/methodology.md#thresholds).
- Agent behaviour is checked by runs on Claude Haiku 4.5 and free OpenRouter models, with automatic
  transcript checks and an LLM judge whose [checklist](evals/judge-checklist.md) follows the same
  rules as the answer templates.
- Where the implementation deviated from the first design (for example the autocorrelation
  correction or the data-break threshold), the deviation was made on the evidence of a prototype
  run, not on the model's say-so.
