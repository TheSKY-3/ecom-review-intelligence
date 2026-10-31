# Data use and publication boundary

## Current rule

DESIGN_DECISION (2026-10-22, owner-approved): Amazon Reviews 2023 Appliances data is used locally for development and evaluation, **with one bounded exception** — the published demo subset described below. The public repository may contain:

- source code, schemas, and reproducible processing instructions;
- synthetic fixtures and synthetic or manually authored demo cases;
- aggregate metrics that do not expose individual review/user records;
- reviewed redacted traces or pre-generated replay traces;
- source URLs, local-file checksums, and dataset snapshot identifiers.

It must not contain or transmit:

- raw dataset files or decompressed copies;
- raw user_id values, reviewer profiles, or other record-level user identifiers;
- bulk review text or collections of excerpts that reconstruct a material part of the source;
- copied product images or other provider assets;
- generated indexes, embeddings, caches, or row maps that reconstruct source content;
- secrets, tokens, local absolute paths, or raw content in telemetry and client logs.

Short review excerpts may be used only in local evidence output. Any public use requires a separate rights and disclosure review; until then, public demos use synthetic content or reviewed redacted replay.

### The published demo subset (2026-10-22)

The project owner decided to ship a bounded subset so that a downloaded copy runs
without the 711 MB raw dataset. Its exact shape:

| Property | Value | Note |
| --- | --- | --- |
| Products | 3 (the three the workbench uses) | not a category dump |
| Reviews | 11,958 | 2.94 M characters of text |
| File | `data/public/ice_makers_public.sqlite`, 10.9 MB | SQLite, no raw JSONL |
| User identifiers | none | the schema has no `user_id` column |
| Provenance | `store_meta` copied verbatim | snapshot id, contract hash, vocabularies |

Conditions that make this acceptable, and that must stay true:

- it is a fixed demo subset, not a data distribution channel; no bulk export
  endpoint exists and none may be added;
- the page states the source dataset, category, and sampling scope;
- a contact route for take-down requests is published (`docs/data-use-and-publication.md`);
- reviewer-level identifiers, images, and provider assets stay out;
- the full category store and the raw gzip files remain local-only.

### Screenshots of the local store (2026-10-31)

The published README screenshots are now captured from the full local store (244
Tier-A products), so the catalog column shows public product titles and per-product
review counts. The owner approved this on 2026-10-31 as product metadata rather than
review content. Review text, evidence IDs, ASINs, and reviewer identifiers still stay
out: the chat image keeps the question and the four tool calls and crops the answer,
and the insight image carries only the model's summary and citation labels.

Anything beyond this exception still needs a separate rights review.

## Local handling

- Raw and derived record-level data stays under ignored local paths.
- The registration manifest may record file role, byte size, SHA-256, validation result, acquisition method, source URL, and timestamps, but must not invent unobserved remote headers.
- Raw user_id can be read only where required for local audit or deduplication and must not be used as a public-facing evidence identifier.
- Public evidence examples use synthetic identifiers and synthetic text unless separately authorised.
- Model calls and traces must omit raw user identifiers and avoid sending raw data to external services unless a later explicit data-governance review authorises it.

## Online demonstration boundary

RESOLVED 2026-10-22: The dataset provider has not supplied a simple data license equivalent to the processing code’s MIT license. The owner reviewed the trade-off and approved the bounded subset above for public demonstration; the residual risk is recorded here rather than treated as cleared. Any expansion beyond this subset reopens the question.

Until that review is resolved:

- the demonstration remains local or uses synthetic/pre-generated replay data;
- live-model keys, if later used, stay in server-side secrets;
- the browser receives no raw data files, source indexes, credentials, or hidden record identifiers.

## Publication check

Before any Git push, release, hosted demo, or portfolio export:

1. inspect tracked files and generated artifacts for raw records, excerpts, user IDs, indexes, secrets, and local paths;
2. verify demo traces are synthetic or explicitly reviewed/redacted;
3. publish only aggregate evaluation results whose cell sizes and examples do not expose individuals;
4. record unresolved rights questions instead of interpreting absence of a simple license as permission.