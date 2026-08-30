# P7 B2B operations workbench validation

Verified on 2026-08-15 in synthetic browser mode and controlled local-data mode.

## Outcome

The loopback-only FastAPI/Jinja workbench completed the three intended operations
tasks and one clarification/resume flow. Desktop `1440x900` and mobile `390x844`
browser checks passed with no horizontal overflow, console errors, failed network
responses, prohibited value shapes, or invalid element relationships. The browser
summary is bound to source commit `8b94deccf10fa4c8f0749f0f6e43e111d11e11f9`,
source SHA-256 `951399fbe790fd786b811595fc47f2fbec600d99e42b306a82d2c1d8540fa27c`,
and `source_dirty=false`. Screenshots are stored in
`docs/assets/p7-workbench-desktop.png` and `docs/assets/p7-workbench-mobile.png`.

| Task | Workflow | Completion | First evidence | Status | Evidence |
|---|---|---:|---:|---|---:|
| SKU diagnosis | diagnosis | 21 ms | 21 ms | VERIFIED_RESULT | 1 |
| Competitive evidence comparison | comparison | 24 ms | 24 ms | VERIFIED_RESULT | 2 |
| Operations issue triage | triage | 28 ms | 28 ms | VERIFIED_RESULT | 2 |

These timings are one automated developer browser run against an in-process local
synthetic service. They are useful only as execution evidence, not as a latency SLA or
proof that an operator works faster.

## Security and state checks

- The server binds only to `127.0.0.1`; `0.0.0.0` and ports below 1024 are rejected.
  HTTP Host values outside loopback/test-client allowlists are rejected before routing.
- Public response DTOs are closed Pydantic models. A recursive scanner rejects Windows,
  UNC, or general POSIX absolute paths, ASIN-shaped values, evidence-like 64-character
  hashes, credentials, and prohibited keys before serialization.
- Validation, unknown token, repeated resume, and unknown server failures return fixed
  error DTOs without echoing submitted values or exceptions.
- HTML, CSS, and JavaScript are local. The page applies a restrictive CSP and renders
  response values through DOM `textContent`.
- Synthetic product and evidence values are intentionally fictional. Public screenshots
  do not contain the local P4 catalog, review text, evidence IDs, paths, or API key.
- Browser validation invalidates prior PASS artifacts before loading Playwright. A real
  missing-module bootstrap probe published a fixed `FAILED` summary and removed both stale
  screenshots. Success publishes two screenshot hashes and the summary from temporary files,
  with the summary renamed last.

## Real-store regression

The explicit local adapter maps public product codes to private local keys inside the
service boundary, then invokes the existing P6 LangGraph, `ProductResolver`,
`EvidenceToolService`, structured verifier, and citation verifier. It returns only
aggregate P7 DTO fields. The reproducible private command completed diagnosis,
comparison, and triage against the ignored real P4 SQLite store with
`VERIFIED_RESULT`; aggregate evidence counts were 5, 10, and 5. The ignored receipt
binds the run to a clean Git revision, the same 23-path source hash used by browser
validation, and the dataset snapshot, evidence contract, and P6 config hashes. It records
both the P6 graph/SQLite gate and public-response scan as passed. The adapter also wrote
three ignored local sensitive trace records while validating the corresponding aggregate
public traces through the existing P6 serializer. These counts describe returned review
evidence, not defects, prevalence, or business impact.

## Business observation boundary

The observer was an automated developer browser check. Adoption and business impact
were not assessed. No target operations user completed a task, so this phase cannot
claim reduced review time, improved decision quality, higher adoption, or earlier issue
escalation. A target-user session remains the sole P7 completion blocker. It should use
`docs/templates/p7-user-trial-record.md` and compare the same task with the current
manual workflow.

## Reproduction

~~~powershell
.\.venv\Scripts\python.exe scripts\run_phase7_workbench.py --port 8765
$env:NODE_PATH = '<bundled node_modules>'
$env:PLAYWRIGHT_EXECUTABLE_PATH = '<local Edge or Chrome executable>'
node scripts\validate_phase7_workbench.cjs http://127.0.0.1:8765 docs\assets artifacts\evaluation-summaries\phase-7-workbench.json
.\.venv\Scripts\python.exe scripts\run_phase7_local_regression.py --project-root .
.\.venv\Scripts\python.exe scripts\run_phase7_workbench.py --mode local --acknowledge-local-data --project-root . --port 8765
~~~

The public JSON summary is the machine-readable authority for browser timings and
gates. The local regression detail remains under ignored `artifacts/evals/`.
