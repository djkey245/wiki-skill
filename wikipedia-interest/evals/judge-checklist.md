# Judge checklist

Binary items for every answer of the agent (calibration §4). Each item passes or fails; an item
that does not apply to the answer passes. The facts come from the run's `result.json`.

1. **exaggeration** — "confirmed" growth only for `promising`; `emerging` is not presented as
   confirmed growth; the words "strong" or "reliable" are not used for growth; no claim that a
   trend will continue. The reverse is also a failure: `no_clear_trend` presented as a lack of
   data.
2. **reasons** — every reason of high severity in the facts is mentioned in plain words.
3. **comparison** — no claim of a difference between languages or topics listed as
   `not_significant_pairs`, neither when they are in different groups nor when they are in one
   group with different estimates.
4. **limits** — says that a language edition is not a market; does not call views people;
   names the basket and the period.
5. **action** — gives a concrete next step.

The judge itself is checked: 30 answers are labelled by people; agreement with people on each
item must be at least 85 %, otherwise the item is reworded.
