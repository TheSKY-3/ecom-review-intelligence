# P3 lexicon amendment: remove "water" from leakage aspect terms

- Date: 2026-09-26 (executed and verified same day)
- Decision owner: project owner (拍板 via C-① decision, option A)
- New contract SHA-256: b93053ce55ab7d47... (full value in artifacts/contracts/phase-3-evidence-policy.json)
- Supersedes: the leakage aspect term list in phase-3-evidence-policy only; every other contract field is unchanged.

## Verified outcome (measured after P3/P4/P5 re-run)

- leakage unjudged pool: 6,791 → 1,127 (-5,664, exactly the water-only count)
- Total unjudged pool: 55,567 → 49,903
- Gates re-run green: P3 PASSED, P4 PASSED (store rebuilt, 3,278 products / 136,604
  reviews), P5 PASSED, preflight PASSED, pytest full suite green.
- Note: the first P3/P4/P5 re-run silently imported the sibling directory's code
  (the workspace .venv editable install pointed to D:\eng_project\电商AGENT\src);
  fixed by reinstalling the package editable from this workspace, then re-running
  all gates. Any future source edit must be followed by checking
  `python -c "import ecom_review_intelligence.contract as c; print(c.__file__)"`.

## Observed facts (measured, from the 2026-09-25 proxy-labeling campaign)

- 33,709 lexicon-unjudged review×aspect pairs were labeled by an external model
  (Qwen flash via Qoder agents) under a code-verified acceptance loop
  (verbatim-span, id-integrity, star-agreement checks; no model self-report).
- Per-term health check over the labeled data (tmp/lexicon-word-evidence.json):
  - leakage "water": 5,664 pool items matched ONLY via "water" (83% of the
    6,791-item leakage unjudged pool); 95% of labeled water-only items were
    off-topic (bottled water, water taste, refills), 4% yielded direction.
  - leakage "leak"/"leaking"/"drip": direction rate ≈ 80% on labeled items.
  - Other aspects' noisy-but-productive terms (ice 18% direction, small/size
    13–18%) were retained: removal would cost more real signal than it saves.

## Decision

- `_ASPECT_TERMS["leakage"]` becomes `("leak", "leaking", "drip")`.
- The P3 contract JSON is regenerated (new contract SHA-256), and the local
  store plus downstream phase gates are rebuilt/re-run from it.
- Expected effect → verified: leakage unjudged pool 6,791 → 1,127; total
  unjudged pool 55,567 → 49,903 (both measured after rebuild). Existing model
  labels remain valid data; water-only leakage labels are no longer part of the
  leakage accounting.

## Accepted costs

- ≈125 labeled real-leak reviews expressed only via "water" (e.g. water on the
  counter with no leak wording) leave the leakage pool (3.4% of labeled).
- P3 contract regeneration, local store rebuild, and P4/P5 gate re-run are
  required by the frozen-contract rule; P6 re-verification follows.

## Provenance

- Evidence: tmp/lexicon-decision-20260925.md, tmp/lexicon-word-evidence.json,
  tmp/label-full/ACCEPTANCE-partial-20260925.md.
- Campaign narrative: docs/explained/main.tex 篇 5–8.
