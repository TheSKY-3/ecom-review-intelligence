# P3 Ice Makers evidence and evaluation contract

- Dataset snapshot ID: 44eface81cccdceb36c3fd61f93736267e63b84ba63902fac026659540518846
- P2 audit SHA-256: 23f001beb0c856b840b9d37b97a43dcdd35ba0051a2c8fe0a858c1fb93284f40
- Selected category: Ice Makers
- Script Git revision: 1a94c2feccab8ce8d6811a8978a2e25f6aee49f4
- Contract SHA-256: b93053ce55ab7d47e865de53fe02cc076f8a1eeaa1f19487ec3c41a5f6f647fa

## Field and identifier contract

- Product/review joins use exact parent_asin only.
- evidence_id is local only: sha256 of evidence-v1, snapshot ID, source role, and one-based JSONL line.
- Review text, user identifiers, product identifiers, and excerpts remain local.

## Enabled aspects

| Aspect | Matched reviews | Coverage | Enabled |
|---|---:|---:|---|
| ice_quality | 83135 | 0.608584 | true |
| noise | 17440 | 0.127668 | true |
| reliability | 14355 | 0.105085 | true |
| cleaning_maintenance | 7959 | 0.058263 | true |
| capacity_speed | 21795 | 0.159549 | true |
| ease_setup | 24061 | 0.176137 | true |
| leakage | 2058 | 0.015065 | true |
| size_portability | 18308 | 0.134022 | true |

## Evidence and evaluation policy

- Strength: INSUFFICIENT 0, WEAK 1, MODERATE 2–4, STRONG 5 or more matching evidence records.
- Polarity is lexical supporting/opposing/conflicting evidence, not a defect, sentiment, or business-impact estimate.
- Labels: silver is deterministic; reviewed requires a manual reviewer record; gold is prohibited.
- P5 fixed cases must record case ID, workflow, expected evidence/no-answer behavior, label provenance, and freeze time.
