# Data use and publication boundary

## Current rule

DESIGN_DECISION: Amazon Reviews 2023 Appliances data is used locally for development and evaluation. The public repository may contain:

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

## Local handling

- Raw and derived record-level data stays under ignored local paths.
- The registration manifest may record file role, byte size, SHA-256, validation result, acquisition method, source URL, and timestamps, but must not invent unobserved remote headers.
- Raw user_id can be read only where required for local audit or deduplication and must not be used as a public-facing evidence identifier.
- Public evidence examples use synthetic identifiers and synthetic text unless separately authorised.
- Model calls and traces must omit raw user identifiers and avoid sending raw data to external services unless a later explicit data-governance review authorises it.

## Online demonstration boundary

OPEN_QUESTION: The dataset provider has not supplied a simple data license equivalent to the processing code’s MIT license. Before a public demonstration, separately review whether displaying, caching, transmitting, or storing any review-derived excerpt is permitted.

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
