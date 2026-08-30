# P3 Ice Makers evidence and evaluation contract

- Dataset snapshot ID: 44eface81cccdceb36c3fd61f93736267e63b84ba63902fac026659540518846
- P2 audit SHA-256: 23f001beb0c856b840b9d37b97a43dcdd35ba0051a2c8fe0a858c1fb93284f40
- Selected category: Ice Makers
- Script Git revision: e856a064b6d8b4a82b48083344e806b1d94bee3b
- Contract SHA-256: 180d1e8667c4ae0f7e459dca1bee658e7be7b03eafaea48c0da12ea62d44934e

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
| leakage | 22669 | 0.165947 | true |
| size_portability | 18308 | 0.134022 | true |

## Evidence and evaluation policy

- Strength: INSUFFICIENT 0, WEAK 1, MODERATE 2–4, STRONG 5 or more matching evidence records.
- Polarity is lexical supporting/opposing/conflicting evidence, not a defect, sentiment, or business-impact estimate.
- Labels: silver is deterministic; reviewed requires a manual reviewer record; gold is prohibited.
- P5 fixed cases must record case ID, workflow, expected evidence/no-answer behavior, label provenance, and freeze time.
