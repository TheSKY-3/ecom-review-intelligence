# P6 Offline Agent Evaluation

## Verified result

- Frozen cases: 30 (silver expectations)
- Blocking offline gates: PASS
- Workflow routing accuracy: 1.000
- Clarification accuracy: 1.000
- Citation integrity: 1.000
- Public leak count: 0

## Lineage

- Dataset snapshot: `44eface81cccdceb36c3fd61f93736267e63b84ba63902fac026659540518846`
- P3 contract: `b93053ce55ab7d47e865de53fe02cc076f8a1eeaa1f19487ec3c41a5f6f647fa`
- P6 config: `2278a2ebe72cf86c68b6edd50a9a490f73de8834ece76150520e5bfc9fda8d5b`
- Case corpus: `d2a6f9e1cfe174f7eb26baab7fb63ebfb3744c3dff0d22c0f3ff2fa65f36c90b`
- Git revision: `b234007d63756c8211a2f7b2614ddd72452fd973`
- Source SHA-256: `541b011856418656d51041db30f61ff332b18945745f1460bc12dad6f5f2d0dc`
- Git dirty during evaluation: `False`

## Interpretation limits

- Expectations are deterministic silver labels, not gold or human-reviewed labels.
- Fake provider behavior does not establish live DeepSeek availability.
- Metrics describe engineering behavior, not product quality or business impact.

Provider-failure cases verify fail-closed behavior only; they do not prove live-provider availability.
