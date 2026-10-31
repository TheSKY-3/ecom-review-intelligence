# P7 B2B operations workbench validation

Verified on 2026-10-31 in synthetic browser mode and controlled local-data mode
(earlier runs: 2026-08-15).

## Outcome

The loopback-only FastAPI/Jinja workbench completed the three intended operations
tasks and one clarification/resume flow. Desktop `1440x900` and mobile `390x844`
browser checks passed with no horizontal overflow, console errors, failed network
responses, prohibited value shapes, or invalid element relationships. The browser
summary is bound to source commit `457dea7405a529b4d7e2f9f4135883b93269fbf8`,
source SHA-256 `06b5e388249df4a6f45e9a1ce5cd4cb38f0abf2eb8e58aa4ece429dadb433197`,
and a clean tracked worktree. Screenshots are stored in
`docs/assets/p7-workbench-desktop.png` and `docs/assets/p7-workbench-mobile.png`;
both were regenerated for the catalog / task / result layout.

| Task | Workflow | Completion | First evidence | Status | Evidence |
|---|---|---:|---:|---|---:|
| SKU diagnosis | diagnosis | 48 ms | 48 ms | VERIFIED_RESULT | 1 |
| Competitive evidence comparison | comparison | 26 ms | 26 ms | VERIFIED_RESULT | 2 |
| Operations issue triage | triage | 44 ms | 44 ms | VERIFIED_RESULT | 2 |

These timings are one automated developer browser run against an in-process local
synthetic service. They are useful only as execution evidence, not as a latency SLA or
proof that an operator works faster.

## Real provider spot check (2026-10-31)

Browser validation replays fixtures, so it cannot show whether the two model steps
work against the real provider. Two paid calls were made separately through the local
workbench to close that gap, with the API key read from the process environment only:

- AI reading of one evidence card (p0004, 运行噪音): returned `READY` after reading
  five review texts, with every quote checked verbatim and one model call.
- One chat turn about the same run: returned an answer with ten statistics
  references (matched, conflicting, opposing, insufficient, sample size) plus an
  explicit "不能说什么" paragraph.

This is a spot check, not an evaluation: two calls cannot estimate pass rates, and it
says nothing about adoption or business impact.

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
binds the run to a clean Git revision, the same source hash used by browser
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
