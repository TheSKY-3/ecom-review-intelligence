# P5 fixed-case retrieval evaluation

- State: PASSED
- Evaluation Git revision: 17feacbd14acbddc09879814294fbf9fe9e50f4d
- P4 store build revision: 17feacbd14acbddc09879814294fbf9fe9e50f4d
- Dataset snapshot ID: 44eface81cccdceb36c3fd61f93736267e63b84ba63902fac026659540518846
- P3 contract SHA-256: 180d1e8667c4ae0f7e459dca1bee658e7be7b03eafaea48c0da12ea62d44934e
- Fixed cases: 27 ({'comparison': 8, 'diagnosis': 8, 'no_answer': 4, 'triage': 7})
- Labels: {'silver': 27}

## Aggregate results

| Retriever | Recall@k | Case hit rate | Citation integrity | Join errors | No-answer false positives | Mean ms | P95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| lexical | 1.0 | 1.0 | 1.0 | 0 | 0 | 26.646259 | 44.2079 |
| fts5_bm25 | 0.53271 | 0.869565 | 1.0 | 0 | 0 | 35.999522 | 49.7039 |

## Interpretation boundary

Silver expected evidence is deterministically derived from the lexical baseline; this is a reproducibility and regression check, not an independent relevance or business-impact benchmark.

Aggregate-only; no product IDs, evidence IDs, review text, or local paths.
