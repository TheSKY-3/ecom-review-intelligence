# P7 model role: the model reads review text, it does not decide sufficiency

- Date: 2026-10-22 (decided and implemented the same day)
- Decision owner: project owner (rejected the sufficiency gate after two falsified experiments)
- Supersedes: the 2026-10-22 sufficiency-gate design note (never implemented)
- Implemented in: `src/ecom_review_intelligence/agent_insight.py`, `workbench_service.run_insight`, `POST /api/v1/insights`

## Question

The project could only claim the model was "participating" if one of its outputs changed
what the operator sees. Two candidates were on the table:

- **E (rejected):** the model answers "is this evidence enough", and a low answer withholds
  the conclusion.
- **F (shipped):** the model reads the sampled review text and reports what it says; the
  deterministic tiers keep deciding whether the evidence is enough.

## Why E was rejected: both parts measured negative

Hand labels on 20 cases, then repeated on a second batch of 20 (definition A: "can state a
direction" counts as enough):

| Rule / model | Batch 1 | Batch 2 | Combined | Verdict |
|---|---|---|---|---|
| Rule A: matched count ≥ 5 | 65% | 35% | 0.50 | overfit to batch 1 |
| Rule B: matched count ≥ 3 | 95% | 55% | 0.75 | overfit to batch 1 |
| Model probe, neutral prompt | 11/20 (0.55) | — | — | no signal |
| Model probe, permissive prompt | 16/20 (0.80) | — | — | 37.5% false positives, above the 10% gate |

The root cause is the question, not the rules: "够不够" had no operational definition and
no payoff in the product, so the labels drifted between batches (batch 1 called high counts
enough, batch 2 called them not enough). A question that cannot be answered consistently by
a human cannot be used to score a model.

A second result closed the same door: with polarity, rating, strength, and counts in the
prompt, the model echoed the rule (`tau = 0.931`) instead of reading the text. Its agreement
was a copy of the label, not independent judgment.

## Why F was selected

Review text is the one place the lexicon is measurably weak (`Not easy to clean` is counted
as supporting), so reading it adds information the rule does not have, and the result can be
checked line by line. F also keeps the honest product shape: the default path is offline,
costs nothing, and works without a key; one click costs one call and changes what is shown.

## Verified outcome (2026-10-22, real API)

- 15 live calls (5 aspects × 3 rounds): 14 usable, 1 `EVIDENCE_NOT_CITED`, retry passed.
- 3 samples read line by line: every quote verbatim, no polarity upgrade, each
  `not_supported` line states the 5-review limit.
- Machine gates reject: a dropped review, a relevant review never cited, a cited irrelevant
  review, and any non-verbatim quote. A single violation discards the whole result.
- Cost: one call per click, output capped at 900 tokens, temperature 0, thinking disabled,
  retries disabled; successful views cached per (run token, card alias).

## Not established

- The 15-call number is a small-sample reliability check, not a quality benchmark.
- Clicking in a real browser was not exercised (the Electron host was unavailable); the
  page flag, the JS/CSS delivery, and the HTTP responses were verified instead.
- Operator value is still unmeasured; three real trials remain open.

## Follow-on fix recorded here

The public-response scanner matched `B` + 9 characters case-insensitively, so ordinary words
such as `background` and `batteries` were treated as product identifiers (27 such words in
20,000 real reviews). Requiring a digit keeps every real identifier caught (all 3,278 ASINs
contain one; 31 pasted identifiers in the same sample) while ordinary words pass. The frozen
scanner tests stay green.
