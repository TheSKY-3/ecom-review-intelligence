# P0 preflight report

- State: PASSED
- Observed at (UTC): 2026-10-29T04:56:37+00:00

## OFFICIAL_FACT

- The source set is the Amazon Reviews'23 Appliances review and metadata files.
- Review validation expects parent_asin, text, and rating fields.
- Metadata validation expects parent_asin and title fields.

## OBSERVED_DATA

- Python: 3.12.14; supported: True
- Operating system: Windows
- Free bytes: 237601595392
- Required bytes: 2989024780

| URL | Status | Bytes | Range | Content type | ETag | Last modified | Error |
|---|---:|---:|---|---|---|---|---|
| https://mcauleylab.ucsd.edu/public_datasets/data/amazon_2023/raw/review_categories/Appliances.jsonl.gz | 206 | 270209794 | True | application/gzip | "101b1302-62bda803121d9" | Thu, 16 Jan 2025 22:41:53 GMT | - |
| https://mcauleylab.ucsd.edu/public_datasets/data/amazon_2023/raw/meta_categories/meta_Appliances.jsonl.gz | 206 | 66406659 | True | application/gzip | "3f54903-62bd9ee3ef7b5" | Thu, 16 Jan 2025 22:01:05 GMT | - |

## DESIGN_DECISION

- This preflight uses only one-byte range probes; it downloads no raw dataset.
- Raw data, bulk raw review text, secrets, indexes, and model caches remain untracked.

## OPEN_QUESTION

- Any future public display of review-derived text requires a separate data-use review.

## BLOCKERS

- None
