# Spec format

A spec is `<workdir>/<slug>/spec.json`. Never edit it by hand: `quick` and `suggest` create
it, `spec edit` changes it, `analyze` fills in the window on the first run. This file is for
reading a spec and for choosing the right `spec edit` flags.

## Example

```json
{
  "version": 1,
  "display_lang": "uk",
  "langs": ["uk", "pl", "cs"],
  "period": {"relative": "24m", "start": "2024-09", "end": "2026-08"},
  "options": {"include_redirects": true, "max_redirects": 10, "metric": "share",
              "criteria": {"min_monthly_views": 50000, "min_growth_pct_yr": 15, "size_axis": "monthly_views"}},
  "topics": [
    {"id": "astronomy", "label": "астрономія", "seeds": ["Q333"], "items": ["Q333", "Q525", "Q318"]},
    {"id": "biology", "label": "біологія", "seeds": ["Q420"], "items": ["Q420", "Q7239"]}
  ]
}
```

- `display_lang`: the user's language; labels and text use it (templates exist for `uk` and
  `en`; other languages get English text and `text_lang: "en"`).
- `langs`: Wikipedia editions, up to 10.
- `period`: `relative` is N months including the end month; explicit `start`/`end` win.
  `analyze` writes the computed `start`/`end` on its first run so reruns repeat the analysis.
- `options.metric`: the main metric of the trend, `share` (of the edition's traffic, the
  default) or `views` (raw views). The other one is shown as a control with its own interval.
- `options.criteria`: optional user criteria; they never change groups or confidence. The
  growth criterion uses the main metric. `size_axis` (`monthly_views` or `level_ppm`) is the size
  axis of the matrix: volume of views, or the topic's share of the edition.
- `topics`: up to 5; each has up to 50 Wikidata items, seeds among them.

## Editing

```
spec edit <spec> --add Q42 Q43                      # one-topic spec
spec edit <spec> --topic astronomy --drop Q318
spec edit <spec> --add-topic "біологія" --seed Q420
spec edit <spec> --drop-topic biology
spec edit <spec> --add-lang sk --drop-lang cs
spec edit <spec> --period 36m
spec edit <spec> --start 2020-01 --end 2024-12
spec edit <spec> --refresh-window                   # relative period: move to the latest data
spec edit <spec> --criteria min_monthly_views=50000,min_growth_pct_yr=15
spec edit <spec> --clear-criteria
spec edit <spec> --metric views                     # trend of raw views instead of the share
spec edit <spec> --criteria size_axis=level_ppm     # the matrix splits by share, not by volume
```

A repeated `quick` or `suggest` with the same seeds finds a one-topic spec on its own: the same
languages and period reuse it (`reused_spec: true`); other languages or another period stop with
`existing_spec` and two ready commands in `next_steps` (`spec edit` to continue it, or the same
command with `--new` for a separate spec). A spec with a relative period keeps its fixed window;
when newer months are published, `analyze` warns `newer_data_available` and suggests
`spec edit <spec> --refresh-window`.

`--add`/`--drop` without `--topic` work only for a spec with one topic (otherwise
`topic_required` lists the ids). `--period` and `--start` exclude each other. After any edit,
run `analyze <spec>` and explain its `changes`.
