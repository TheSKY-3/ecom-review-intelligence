# P6 runtime dependency audit

**Observed on:** 2026-08-03  
**Runtime checked:** Python 3.12.13 on Windows  
**Scope:** P6 local LangGraph orchestration and DeepSeek API integration only.

## Installed resolution

| Package | Constraint | Installed | Python support | License | Primary source |
|---|---:|---:|---:|---|---|
| `langgraph` | `>=1.2.9,<1.3` | 1.2.10 | Python >=3.10 | MIT | https://pypi.org/project/langgraph/ |
| `langgraph-checkpoint-sqlite` | `>=3.1,<3.2` | 3.1.1 | Python >=3.10 | MIT | https://pypi.org/project/langgraph-checkpoint-sqlite/ |
| `openai` | `>=2.46,<2.47` | 2.46.0 | Python >=3.10 | Apache-2.0 | https://pypi.org/project/openai/ |
| `pydantic` | `>=2.13,<2.14` | 2.13.4 | Python >=3.9 | MIT | https://pypi.org/project/pydantic/ |

`python -m pip check` reported no broken requirements after editable installation.

## Selection and compatibility decisions

### LangGraph

- PyPI marks the package Production/Stable and publishes it under MIT.
- Version 1.2.3 was yanked for an unintended merging-strategy regression. The
  project therefore starts at 1.2.9 and currently resolves to 1.2.10.
- P6 uses one flat `StateGraph`; domain policy, retrieval, verification, and
  rendering remain framework-neutral Python.
- Official interrupt documentation says resume restarts the interrupted node
  from its beginning. P6 therefore keeps the clarification node idempotent,
  performs no API/tool/file side effect before `interrupt()`, and regression
  tests resume with the same thread ID.
- P6 does not use subgraphs or explicit checkpoint time-travel replay. Dedicated
  fake/replay gateways provide deterministic offline replay instead.

Sources:

- https://docs.langchain.com/oss/python/langgraph/interrupts
- https://docs.langchain.com/oss/python/langgraph/persistence
- https://pypi.org/project/langgraph/

### SQLite checkpointer

- GHSA-9rwj-6rc7-p77c affects versions below 3.0.1 and describes SQL injection
  through untrusted metadata-filter keys in the SQLite checkpointer list method.
- P6 resolves to 3.1.1, does not expose checkpoint metadata filters to users or
  model output, and uses generated thread IDs with normal interrupt/resume only.
- The checkpoint database stays under ignored `data/processed/` storage and is
  never a public replay artifact.

Sources:

- https://github.com/langchain-ai/langgraph/security/advisories/GHSA-9rwj-6rc7-p77c
- https://pypi.org/project/langgraph-checkpoint-sqlite/

### OpenAI Python SDK used with DeepSeek

- DeepSeek documents an OpenAI-compatible base URL and provides Chat Completions,
  JSON output, and tool calls for `deepseek-v4-flash`.
- The SDK is used as a transport/client library only. Compatibility is verified
  first through injected request-capture tests and later through a single
  explicitly acknowledged live smoke run; compatibility is not inferred solely
  from the phrase “OpenAI-compatible.”
- SDK automatic transport retries must be disabled. Every actual outbound request,
  including any project-controlled retry, consumes the three-call run budget.
- DeepSeek thinking is currently enabled by default, so P6 sends
  `extra_body={"thinking": {"type": "disabled"}}` on every live request.

Sources:

- https://api-docs.deepseek.com/quick_start/pricing
- https://api-docs.deepseek.com/guides/thinking_mode
- https://api-docs.deepseek.com/guides/tool_calls
- https://pypi.org/project/openai/

### Pydantic

- Pydantic validates external/API boundaries with `extra="forbid"`; it is not an
  authorization mechanism.
- Local policy still checks workflow, aliases, cards, aspects, counts, codes,
  citations, and tool routing against deterministic state.

Source: https://pypi.org/project/pydantic/

## Dated model and pricing record

The frozen non-secret runtime values are in
`artifacts/manifests/phase-6-agent-config.json`. On 2026-08-03 the official
DeepSeek page listed `deepseek-v4-flash` as model version
`DeepSeek-V4-Flash-0731`, with USD prices per one million tokens of 0.0028 for
cache-hit input, 0.14 for cache-miss input, and 0.28 for output.

The same page warns that prices may change and describes a future peak/off-peak
policy whose effective date is subject to a later announcement. P6 cost output is
therefore a dated estimate, not a provider invoice or future-price claim.

## Verification boundary

- Dependency resolution and `pip check` passing establish local package
  compatibility only.
- Fake/injected tests establish request shape without a network call.
- P6 cannot pass until the required live DeepSeek suite completes under the
  frozen safety and budget gates.
