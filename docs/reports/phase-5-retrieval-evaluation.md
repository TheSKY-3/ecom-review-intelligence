# P5 fixed-case retrieval evaluation

- State: PASSED
- Evaluation Git revision: 1a94c2feccab8ce8d6811a8978a2e25f6aee49f4
- P4 store build revision: 1a94c2feccab8ce8d6811a8978a2e25f6aee49f4
- Dataset snapshot ID: 44eface81cccdceb36c3fd61f93736267e63b84ba63902fac026659540518846
- P3 contract SHA-256: b93053ce55ab7d47e865de53fe02cc076f8a1eeaa1f19487ec3c41a5f6f647fa
- Fixed cases: 27 ({'comparison': 8, 'diagnosis': 8, 'no_answer': 4, 'triage': 7})
- Labels: {'silver': 27}

## Aggregate results

| Retriever | Recall@k | Case hit rate | Citation integrity | Join errors | No-answer false positives | Mean ms | P95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| lexical | 1.0 | 1.0 | 1.0 | 0 | 0 | 31.182581 | 52.8918 |
| fts5_bm25 | 0.515152 | 0.826087 | 1.0 | 0 | 0 | 33.156296 | 49.6746 |

## Interpretation boundary

Silver expected evidence is deterministically derived from the lexical baseline; this is a reproducibility and regression check, not an independent relevance or business-impact benchmark.

Aggregate-only; no product IDs, evidence IDs, review text, or local paths.
