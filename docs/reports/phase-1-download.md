# P1 download and raw-data registration report

- State: PASSED
- Observed at (UTC): 2026-08-02T16:07:30+00:00
- Dataset snapshot ID: 44eface81cccdceb36c3fd61f93736267e63b84ba63902fac026659540518846

## OFFICIAL_FACT

- Each record identifies an official source URL and the downloaded object version.

## OBSERVED_DATA

| Source | Filename | Bytes | SHA-256 | ETag | Last modified | Resumed bytes | Acquisition |
|---|---|---:|---|---|---|---:|---|
| reviews | Appliances.jsonl.gz | 270209794 | 150f209befceaa6f837abc997065b2d251034bbbda19bebc4ad56dac779730c2 | "101b1302-62bda803121d9" | Thu, 16 Jan 2025 22:41:53 GMT | 270209794 | manual_direct_download |
| metadata | meta_Appliances.jsonl.gz | 66406659 | 5a94cffb9ec3be23e42b99643fcab5f48160b3c7a53835451c4457aadcdf9365 | "3f54903-62bd9ee3ef7b5" | Thu, 16 Jan 2025 22:01:05 GMT | 66406659 | manual_direct_download |

## DESIGN_DECISION

- Raw files remain local-only. This report omits local absolute paths and review text.

## PROVENANCE_LIMITATION

- manual_direct_download means local size, SHA-256, and gzip validation passed after a user-confirmed direct download. It does not claim a browser HTTP receipt or a server-provided checksum.

## OPEN_QUESTION

- Public display or redistribution of review-derived text requires a separate data-use review.

## BLOCKERS

- None
