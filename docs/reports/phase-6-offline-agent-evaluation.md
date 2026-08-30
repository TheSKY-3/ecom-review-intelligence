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
- P3 contract: `180d1e8667c4ae0f7e459dca1bee658e7be7b03eafaea48c0da12ea62d44934e`
- P6 config: `8c6ab5ef51680795b660e92021ed5a5a656b2ccbcd0c9b690313a06a38ddcd22`
- Case corpus: `37fe1c12ad65caed9b6509753e6162cdc16c73d32641292090004b82b49aafb0`
- Git revision: `53ab955b54f241e3b3c9f6bfe29ca1fb53fc1089`
- Source SHA-256: `fafe752c0ade85f33328ce12264be144b3271841dd36d6cd1eb578b11ba3a903`
- Git dirty during evaluation: `False`

## Interpretation limits

- Expectations are deterministic silver labels, not gold or human-reviewed labels.
- Fake provider behavior does not establish live DeepSeek availability.
- Metrics describe engineering behavior, not product quality or business impact.

Provider-failure cases verify fail-closed behavior only; they do not prove live-provider availability.
