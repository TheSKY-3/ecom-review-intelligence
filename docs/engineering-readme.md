# ECom Review Intelligence Agent

An evidence-governed decision-support project for ecommerce product and category
operators. It turns product-review questions into traceable supporting, opposing,
conflicting, or insufficient evidence.

## Current status

P0 (environment, official-source reachability, disk capacity, and
data-publication boundary preflight) passed on 2026-08-02. The recorded
evidence is in [docs/reports/phase-0-preflight.md](docs/reports/phase-0-preflight.md).

P1 (download, integrity, and raw-data registration) passed on 2026-08-03.
Both final gzip files passed independent full-stream gzip and SHA-256 checks,
and the manual-source receipts, immutable snapshot manifest, per-target write
locking, recovery handling, and full test suite are recorded. P2 may now audit
only the frozen snapshot; do not rerun a live download for already verified
files.

P2 (stream audit and scope selection) passed with an explicitly selected Ice
Makers scope. P3 must consume the P2 scope manifest and freeze the evidence and
evaluation contract before deterministic retrieval work begins.

P3 passed with a local Ice Makers evidence contract. P4 also passed: the
ignored local store contains 3,278 products, 136,604 exact-joined reviews, and
the same number of FTS rows, all bound to the frozen P1/P3 lineage. P5 passed
with 27 local-only silver fixed cases (8 diagnosis, 8 comparison, 7 triage, and
4 no-answer) and aggregate-only evaluation reports. Both retrievers reached
citation integrity 1.0 with zero product-join errors and zero no-answer false
positives. The lexical baseline's Recall@5 of 1.0 is not an independent win:
its silver expected evidence was derived from that deterministic baseline;
FTS5/BM25 measured 0.53271 Recall@5 on those same constraints.

P6 is `PASSED`. The constrained LangGraph runtime, deterministic tools, one-time
clarification/resume, safe evidence cards, local verifier, fake/replay/live gateways,
and redacted traces are implemented. The 30-case offline gate passed on the real
local P4 store. The real DeepSeek gate then ran 15 frozen cases twice: 26 verified
results and 4 expected insufficient-evidence degradations, with status and citation
expectations passing 30/30 and zero unauthorized tools, successful injections,
fabricated identifiers, or public leaks. The fail-closed completion audit returned
`READY_TO_CLOSE_P6`. These results are bounded engineering evidence, not a production
SLA or measured business impact. P7 engineering validation now covers the synthetic
browser workbench and a controlled P6-backed local adapter; P7 remains in progress
because target-user business validation is still unverified.

The current execution record is [docs/current-stage-status.md](docs/current-stage-status.md).
The design stage map remains [docs/master-plan.md](docs/master-plan.md).

## Local setup

~~~
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe scripts/run_preflight.py --project-root .
~~~

Raw Amazon data, credentials, large indexes, and model caches are local-only and
must not be committed.

## P6 local replay

Replay is the safe default and makes no provider request. Select a product identifier
from the private local store without writing it to the repository:

~~~powershell
$localProductId = Read-Host "Local product ID"
.\.venv\Scripts\python.exe scripts\run_phase6_offline.py --mode replay --question "Summarize noise evidence" --product-id $localProductId --aspect noise --workflow diagnosis
Remove-Variable localProductId
~~~

Reproduce the complete offline Agent gate:

~~~powershell
.\.venv\Scripts\python.exe scripts\run_phase6_evaluation.py --project-root .
~~~

## Optional DeepSeek gate

Live execution is never the default. The key must already exist in the process
environment; do not pass it as an argument, add it to `.env`, commit it, or paste it
into logs. Run a one-case compatibility smoke before the bounded 30-run gate:

~~~powershell
.\.venv\Scripts\python.exe scripts\run_phase6_live_evaluation.py --project-root . --case-limit 1 --acknowledge-live-api
.\.venv\Scripts\python.exe scripts\run_phase6_live_evaluation.py --project-root . --repetitions 2 --acknowledge-live-api
~~~

Smoke completion does not publish a PASS. The aggregate live summary/report are
created only when all 15 selected cases run twice with every blocking gate true and
the evaluated source identity clean.

After committing a passing live aggregate pair, run the final fail-closed audit:

~~~powershell
.\.venv\Scripts\python.exe scripts\run_phase6_completion_audit.py --project-root .
~~~

It recomputes the frozen snapshot and contract lineage, checks the local store,
public schemas, private live selection, source identity, replay fixture, tracked-file
boundary, and committed report pair. For the committed P6 live artifact pair, the
audit returns `READY_TO_CLOSE_P6`; any source, evidence, or evaluation change requires
the gate and audit to be rerun.

## Optional local model endpoint

The two model-backed workbench steps speak the OpenAI chat-completions protocol, so a
local server can stand in for the provider without touching application code:

~~~powershell
$env:ECOM_MODEL_PROVIDER="ollama"
$env:ECOM_MODEL_BASE_URL="http://127.0.0.1:11435/v1"
$env:ECOM_MODEL_NAME="qwen3.5-2b"
$env:ECOM_MODEL_API_KEY="local"
~~~

`scripts/serve_local_model.py` wraps a Hugging Face checkpoint in exactly that
endpoint. It must run under that checkpoint's own torch environment, because this
repository never imports torch. Measured on 2026-10-31 with a 2B checkpoint: 3.6 s to
load, 4.2 GB of GPU memory, 15–27 s per forced tool call. Both local checkpoints tried
(Qwen3.5-2B and a 4B Ollama model) failed the verifier on at least one of: schema field
names, the 200-character quote limit, or citing a review the same answer had marked
irrelevant. The verifier was not relaxed, so the local path stays a measurement aid
rather than a supported production path.

## P7 operations workbench

The P7 workbench is a loopback-only B2B operations prototype. It opens directly on
SKU diagnosis, competitive comparison, and issue-triage tasks. The default mode is
deterministic synthetic replay; it does not read the DeepSeek key or publish local
Amazon identifiers and review text.

~~~powershell
.\.venv\Scripts\python.exe scripts\run_phase7_workbench.py --check
.\.venv\Scripts\python.exe scripts\run_phase7_workbench.py --port 8765
~~~

Open <http://127.0.0.1:8765>. The server rejects non-loopback host bindings. API
validation errors, unknown exceptions, stale resume tokens, and sensitive response
shapes fail with fixed public error DTOs.

Controlled local mode is explicit and still returns only aggregate public DTOs. It
reuses the P6 graph, SQLite evidence tool, verifier, and citation checks:

~~~powershell
.\.venv\Scripts\python.exe scripts\run_phase7_local_regression.py --project-root .
.\.venv\Scripts\python.exe scripts\run_phase7_workbench.py --mode local --acknowledge-local-data --project-root . --port 8765
~~~

The local adapter exposes the Tier-A slice of the frozen catalog through
`GET /api/v1/catalog`: 244 products with at least 100 reviews, 74.3% of all ice-maker
reviews in the full local store. `GET /api/v1/category` returns whole-category aspect
counts (136,604 reviews, eight frozen aspects, support/oppose/conflict/insufficient per
aspect) as the baseline a single product's five-card sample cannot provide. Neither
endpoint returns ASINs, review text, evidence IDs, or paths, and every listed product
runs through the same P6 graph and verifier. The bundled three-product demo store
therefore reports three products, not 244.

The regression detail stays under ignored `artifacts/evals/`; stdout contains only a
fixed status and workflow count.

The README documents the rebuild path for the full local store: register the two
Appliances gzip files under `data/raw/`, then run `run_phase4_build.py` against the
frozen P2 scope and P3 contract shipped in `artifacts/`. Rebuilding the contract
itself (rerunning P2/P3) produces a new contract id even from identical input,
because the contract records its audit hash, revision, and observation time; that
invalidation is deliberate and requires re-signing the P6/P7 configuration. Local mode does not call DeepSeek or display private
product keys, evidence IDs, paths, or credentials. The aggregate run response stays
free of review text; an explicit per-card detail request (local mode only) returns
the underlying review rows on demand so the store owner can perform the review step.

![P7 operations workbench on the local store](docs/assets/p7-workbench-desktop.png)

The reproducible browser check uses local Edge/Chrome with Playwright and exercises
all three workflows plus clarification/resume on desktop and mobile. Its public
aggregate is `artifacts/evaluation-summaries/phase-7-workbench.json`; the report is
`docs/reports/phase-7-workbench-validation.md`. These are developer synthetic checks,
not target-user adoption or business-impact measurements. The summary records the
source revision/hash, source dirty state, screenshot hashes, and actual element
relationship checks; failed reruns invalidate old PASS artifacts.

## Data and evidence boundary

- The model receives only run-scoped aliases and aggregate safe evidence cards, not
  raw reviews, local product/evidence identifiers, paths, or credentials.
- Local citations are stable project evidence IDs, not official Amazon review IDs.
- `artifacts/evals/`, raw data, SQLite stores, checkpoints, and case-level live output
  remain ignored. Public reports contain aggregate metrics and lineage only.
- Offline silver-case metrics demonstrate bounded engineering behavior; they do not
  establish business impact, causal product quality, production reliability, or live
  provider availability.
