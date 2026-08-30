# Data sources and local provenance

## Official files

- OFFICIAL_FACT: Reviews — https://mcauleylab.ucsd.edu/public_datasets/data/amazon_2023/raw/review_categories/Appliances.jsonl.gz
- OFFICIAL_FACT: Metadata — https://mcauleylab.ucsd.edu/public_datasets/data/amazon_2023/raw/meta_categories/meta_Appliances.jsonl.gz
- OFFICIAL_FACT: Dataset documentation — https://amazon-reviews-2023.github.io/

## Required provenance fields

P0 records HTTP status, Content-Range, ETag, Last-Modified, content type, remote byte size, timestamp, and the local capacity gate.

P1 records, for each final local file:

- source role and declared official URL;
- acquisition method: project downloader or manual external download;
- final byte size and SHA-256;
- full gzip-stream validation result and time;
- only remote identity fields actually observed by that acquisition path;
- registration receipt and immutable dataset snapshot membership.

The registration record must distinguish OFFICIAL_FACT, OBSERVED_DATA, OBSERVED_USER_ACTION, REASONABLE_INFERENCE, and OPEN_QUESTION.

## Observed during P0

- OBSERVED_DATA at 2026-08-02T15:02:42+00:00: review source returned HTTP 206, application/gzip, 270,209,794 total bytes, ETag "101b1302-62bda803121d9", and Last-Modified Thu, 16 Jan 2025 22:41:53 GMT.
- OBSERVED_DATA at 2026-08-02T15:02:42+00:00: metadata source returned HTTP 206, application/gzip, 66,406,659 total bytes, ETag "3f54903-62bd9ee3ef7b5", and Last-Modified Thu, 16 Jan 2025 22:01:05 GMT.
- OBSERVED_DATA: both sources accepted a one-byte range request. Their compressed total is 336,616,453 bytes.
- OBSERVED_DATA: the project drive had 523,501,719,552 free bytes. The P0 capacity rule required 2,989,024,780 bytes.

## Observed local files during P1

Validation snapshot time: 2026-08-02T15:50:41+00:00.

| Role | Local ignored path | Bytes | SHA-256 | Full gzip validation |
|---|---|---:|---|---|
| reviews | data/raw/Appliances.jsonl.gz | 270,209,794 | 150f209befceaa6f837abc997065b2d251034bbbda19bebc4ad56dac779730c2 | passed; decompressed stream 929,451,412 bytes |
| metadata | data/raw/meta_Appliances.jsonl.gz | 66,406,659 | 5a94cffb9ec3be23e42b99643fcab5f48160b3c7a53835451c4457aadcdf9365 | passed; decompressed stream 285,298,475 bytes |

- OBSERVED_USER_ACTION: the user reported downloading the two files from the official direct links into data/raw.
- OBSERVED_DATA: both final local files match the byte sizes observed from the official endpoints during P0 and pass complete local gzip/SHA-256 verification.
- REASONABLE_INFERENCE: the declared direct URLs, matching sizes, and valid gzip contents support treating these as the intended source objects for local work.
- NOT_VERIFIED: the manual browser transfer did not produce a project-controlled HTTP receipt, independently supplied server checksum, or captured final-response identity. Matching size alone is not cryptographic proof that a local file equals a remote object.

An obsolete 409,347,588-byte data/raw/Appliances.jsonl.gz.part and its identity file also exist. They are not valid final inputs, must not be resumed against the already verified final file, and require explicit quarantine or user-authorised deletion.

## Frozen P1 snapshot

P1 registration time: 2026-08-02T16:07:30+00:00.

Frozen dataset snapshot ID:

    44eface81cccdceb36c3fd61f93736267e63b84ba63902fac026659540518846

Normative formula:

    sha256("reviews:<review_sha256>\nmetadata:<metadata_sha256>\n")

The trailing newline is part of the input. The P1 manifest and report were atomically written after each local file passed exact byte count, SHA-256, and complete gzip-stream validation. The independently probed ETag/Last-Modified values matched the P0 observations. P2 must consume artifacts/manifests/phase-1-download.json, not discover arbitrary files from data/raw.

## Observed P2 stream audit and selected scope

- OBSERVED_DATA: the frozen P1 snapshot was streamed with code revision ebe9803425931c9594ed4729550fba752fae8e6f; the P2 audit SHA-256 is recorded in artifacts/audits/phase-2-audit.json.
- OBSERVED_DATA: metadata contained 94,327 valid JSON objects and reviews contained 2,128,605 valid JSON objects; malformed JSON count was zero in both streams.
- OBSERVED_DATA: 2,100,939 review rows joined exactly to metadata parent_asin values; 27,666 did not join and are excluded from scope-level evidence work.
- OBSERVED_DATA: Ice Makers has 3,278 metadata products and 136,604 joined reviews, with review-to-metadata coverage 1.000000, non-empty-text coverage 0.998675, valid-rating coverage 1.000000, and timestamp coverage 1.000000.
- DESIGN_DECISION: the automatic quality/comparability score recommended Water Filters. Ice Makers was explicitly selected from eligible candidates because its appliance-product experience is a closer fit to the three target workflows. This is a scope-fit decision, not a claim that Ice Makers has higher business impact.
- DESIGN_DECISION: P3–P5 consume artifacts/manifests/phase-2-scope.json and the selected Ice Makers category, not the raw directory or the automatic recommendation alone.

## Observed P3 evidence contract

- OBSERVED_DATA: P3 revalidated the P1 snapshot and P2 audit hash, then profiled 3,278 Ice Makers products and 136,604 selected review rows with Git revision e856a064b6d8b4a82b48083344e806b1d94bee3b.
- OBSERVED_DATA: all eight frozen aspects met the 100-review enablement gate. Their coverage ranged from cleaning_maintenance at 0.058263 to ice_quality at 0.608584; this is retrieval vocabulary coverage, not prevalence of a defect or product attribute.
- OBSERVED_DATA: selected-product review-count quantiles were q10=1, q25=1, q50=4, q75=19, and q90=68.
- DESIGN_DECISION: evidence strength is INSUFFICIENT for zero records, WEAK for one, MODERATE for two to four, and STRONG for five or more. Lexical polarity is supporting/opposing/conflicting evidence only; it is not a sentiment, safety, defect-rate, or business-impact label.
- DESIGN_DECISION: automated/test-derived labels are silver, manual-review records are reviewed, and gold is prohibited until an independent adjudication protocol exists.

## Observed P4 local evidence store

- OBSERVED_DATA: P4 revalidated the frozen P1/P2/P3 lineage and materialized 3,278 Ice Makers products, 136,604 exact-joined reviews, and 136,604 FTS rows using Git revision ccd53cec511a9fc611183d659eff299a1697b2ec.
- DESIGN_DECISION: the local SQLite store, raw review fields, product identifiers, and evidence locators remain ignored. The committed P4 build receipt contains aggregate counts, lineage hashes, and policy statements only.

## Observed P5 fixed-case evaluation

- OBSERVED_DATA: P5 froze 27 local-only cases: 8 diagnosis, 8 comparison, 7 triage, and 4 no-answer. All are silver/unreviewed, not human-adjudicated labels.
- OBSERVED_DATA: with limit 5, lexical retrieval recorded citation integrity 1.0, zero product-join errors, zero no-answer false positives, and Recall@5 1.0; SQLite FTS5/BM25 recorded the same three integrity gates and Recall@5 0.532710.
- LIMITATION: expected evidence locators are deterministically derived from the lexical baseline. These measurements support reproducibility and regression detection, not an independent relevance comparison, business effect, or claim that either retriever is generally superior.
