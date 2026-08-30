"""Thin, bounded LangGraph orchestration for the P6 local evidence Agent."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import ValidationError

from ecom_review_intelligence.agent_contracts import (
    AgentDraft,
    AgentPlan,
    AgentRequest,
    AgentResponse,
    AgentState,
    ErrorCode,
    SafeEvidenceCard,
    VerificationResult,
    Workflow,
    assert_json_serializable_state,
)
from ecom_review_intelligence.agent_policy import (
    AgentPolicyError,
    ProductAliasMap,
    SafeEvidenceBundle,
    build_safe_evidence,
    deterministic_fallback,
    render_verified_response,
    sanitize_question,
    verify_draft,
)
from ecom_review_intelligence.agent_tools import (
    LocalEvidenceItem,
    ProductCandidate,
    ToolInvocationLedger,
    WorkflowToolResult,
)
from ecom_review_intelligence.engine import Evidence
from ecom_review_intelligence.model_gateway import GatewayError, RequestBudget

TraceWriter = Callable[[dict[str, Any], dict[str, Any]], None]


@dataclass
class _RunRuntime:
    budget: RequestBudget
    gateway: Any
    ledger: ToolInvocationLedger
    tool: Any
    started: float
    input_tokens: int = 0
    output_tokens: int = 0
    records_seen: int = 0


@dataclass
class AgentGraphDependencies:
    """Injected adapters. Mutable provider/tool state is constructed once per run."""

    resolver: Any
    gateway_factory: Callable[[RequestBudget], Any]
    tool_factory: Callable[[ToolInvocationLedger], Any]
    trace_writer: TraceWriter
    citation_verifier: Callable[[str], bool]
    snapshot_hash: str
    contract_hash: str
    uuid_factory: Callable[[], str] = lambda: str(uuid4())
    clock: Callable[[], float] = monotonic
    model_id: str = "deepseek-v4-flash"
    config_id: str = "p6-agent-config-v1"
    _runs: dict[str, _RunRuntime] = field(default_factory=dict, init=False, repr=False)

    def new_run(
        self,
        request: AgentRequest | dict[str, Any],
        *,
        run_id: str | None = None,
        thread_id: str | None = None,
    ) -> tuple[AgentState, dict[str, Any]]:
        return new_run(
            request,
            run_id=run_id,
            thread_id=thread_id,
            uuid_factory=self.uuid_factory,
        )

    def runtime(self, run_id: str) -> _RunRuntime:
        runtime = self._runs.get(run_id)
        if runtime is None:
            budget = RequestBudget(max_requests=3)
            ledger = ToolInvocationLedger()
            runtime = _RunRuntime(
                budget=budget,
                gateway=self.gateway_factory(budget),
                ledger=ledger,
                tool=self.tool_factory(ledger),
                started=self.clock(),
            )
            self._runs[run_id] = runtime
        return runtime

    def release_runtime(self, run_id: str) -> None:
        """Release process-local mutable adapters after a terminal graph path."""

        self._runs.pop(run_id, None)


def new_run(
    request: AgentRequest | dict[str, Any],
    *,
    run_id: str | None = None,
    thread_id: str | None = None,
    uuid_factory: Callable[[], str] = lambda: str(uuid4()),
) -> tuple[AgentState, dict[str, Any]]:
    """Create JSON-only initial state and the required LangGraph thread config."""

    parsed = request if isinstance(request, AgentRequest) else AgentRequest.model_validate(request)
    actual_run_id = run_id or uuid_factory()
    actual_thread_id = thread_id or uuid_factory()
    state: AgentState = {
        "request": parsed.model_dump(mode="json"),
        "run_id": actual_run_id,
        "thread_id": actual_thread_id,
        "status": "RUNNING",
        "clarification_count": 0,
        "repair_count": 0,
        "tool_execution_count": 0,
        "model_call_count": 0,
        "model_http_attempt_count": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "graph_path": [],
        "node_outcomes": {},
        "terminal": False,
        "trace_written": False,
    }
    assert_json_serializable_state(state)
    return state, {"configurable": {"thread_id": actual_thread_id}}


@contextmanager
def open_sqlite_agent_graph(
    dependencies: AgentGraphDependencies,
    *,
    project_root: Path,
    checkpoint_path: Path | None = None,
) -> Iterator[Any]:
    """Compile a production graph with a scoped, project-rooted SQLite saver."""

    root = Path(project_root).resolve()
    target = (
        Path(checkpoint_path).resolve()
        if checkpoint_path is not None
        else root / "data" / "processed" / "p6_agent_checkpoints.sqlite"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    with SqliteSaver.from_conn_string(str(target)) as saver:
        yield build_agent_graph(dependencies, checkpointer=saver)


def build_agent_graph(dependencies: AgentGraphDependencies, *, checkpointer: Any):
    """Compile the single flat P6 graph; it has no open-ended model/tool loop."""

    def mark(state: AgentState, node: str, outcome: str, **updates: Any) -> AgentState:
        result: AgentState = {
            "graph_path": [*state.get("graph_path", []), node],
            "node_outcomes": {**state.get("node_outcomes", {}), node: outcome},
            **updates,
        }
        assert_json_serializable_state(result)
        return result

    def failed(state: AgentState, node: str, code: str, outcome: str = "failed") -> AgentState:
        response = AgentResponse(
            run_id=state["run_id"], status="FAILED", workflow=state.get("workflow"),
            rendered_text="The request could not be completed safely.", error_code=code,
        )
        return mark(
            state, node, outcome, status="FAILED", error_code=code,
            response=response.model_dump(mode="json"), terminal=True,
            verification={"passed": False, "error_code": code, "error_details": []},
        )

    def validate_input(state: AgentState) -> AgentState:
        try:
            request = AgentRequest.model_validate(state["request"])
        except (KeyError, TypeError, ValidationError):
            return failed(state, "validate_input", ErrorCode.INVALID_INPUT.value)
        return mark(state, "validate_input", "accepted", request=request.model_dump(mode="json"))

    def parse_request(state: AgentState) -> AgentState:
        if state.get("terminal"):
            return mark(state, "parse_request", "skipped")
        request = AgentRequest.model_validate(state["request"])
        exact_candidates: list[ProductCandidate] = []
        try:
            for product_id in request.exact_product_ids:
                candidate = dependencies.resolver.by_exact_id(product_id)
                if candidate is not None:
                    exact_candidates.append(candidate)
        except Exception:  # noqa: BLE001 - resolver is an injected local boundary
            return failed(state, "parse_request", ErrorCode.STATE_ERROR.value)
        aliases = (
            ProductAliasMap.from_product_ids(tuple(item.product_id for item in exact_candidates))
            if exact_candidates else None
        )
        sanitized = sanitize_question(request.question, aliases) if aliases else request.question
        runtime = _runtime_from_state(dependencies, state)
        calls = state.get("model_call_count", 0) + 1
        try:
            plan = runtime.gateway.plan(sanitized)
        except Exception as error:  # noqa: BLE001 - gateway is an injected boundary
            code = (
                error.error_code
                if isinstance(error, GatewayError)
                else ErrorCode.STATE_ERROR.value
            )
            result = failed(state, "parse_request", code)
            result["model_call_count"] = min(3, calls)
            result.update(_usage(runtime, state))
            return result
        return mark(
            state, "parse_request", "parsed", sanitized_question=sanitized,
            plan=plan.model_dump(mode="json"), model_call_count=calls, **_usage(runtime, state),
        )

    def resolve_products(state: AgentState) -> AgentState:
        if state.get("terminal"):
            return mark(state, "resolve_products", "skipped")
        request = AgentRequest.model_validate(state["request"])
        selected: list[ProductCandidate] = []
        unresolved: list[str] = []
        candidates: list[ProductCandidate] = []
        resumed = state.get("resume_selection")
        if resumed:
            try:
                for product_id in resumed:
                    candidate = dependencies.resolver.by_exact_id(product_id)
                    if candidate is None:
                        code = (
                            ErrorCode.PRODUCT_AMBIGUOUS.value
                            if state.get("product_candidates")
                            else ErrorCode.PRODUCT_NOT_FOUND.value
                        )
                        return failed(state, "resolve_products", code)
                    candidates.append(candidate)
            except Exception:  # noqa: BLE001 - resolver is an injected local boundary
                return failed(state, "resolve_products", ErrorCode.STATE_ERROR.value)
            allowed = {item["product_id"] for item in state.get("product_candidates", [])}
            if allowed and not set(resumed).issubset(allowed):
                return failed(state, "resolve_products", ErrorCode.PRODUCT_AMBIGUOUS.value)
            selected = candidates
        else:
            raw_plan = AgentPlan.model_validate(state["plan"])
            lookup_terms = request.product_title_keywords
            if not request.exact_product_ids and not lookup_terms:
                lookup_terms = raw_plan.product_terms
            try:
                for product_id in request.exact_product_ids:
                    candidate = dependencies.resolver.by_exact_id(product_id)
                    if candidate is None:
                        unresolved.append(product_id)
                    else:
                        selected.append(candidate)
                for keyword in lookup_terms:
                    matches = dependencies.resolver.by_title_substring(keyword, limit=5)
                    candidates.extend(matches)
                    if len(matches) == 1:
                        selected.append(matches[0])
                    else:
                        unresolved.append(keyword)
            except Exception:  # noqa: BLE001 - resolver is an injected local boundary
                return failed(state, "resolve_products", ErrorCode.STATE_ERROR.value)
        selected = list({item.product_id: item for item in selected}.values())
        candidate_dtos = [_candidate_dto(item) for item in candidates]
        if unresolved:
            if state.get("clarification_count", 0) >= 1:
                return failed(state, "resolve_products", ErrorCode.PRODUCT_AMBIGUOUS.value)
            return mark(
                state, "resolve_products", "clarification_required",
                unresolved_product_terms=unresolved, product_candidates=candidate_dtos,
                status="NEEDS_CLARIFICATION",
            )
        if not selected:
            return failed(state, "resolve_products", ErrorCode.PRODUCT_NOT_FOUND.value)
        alias_map = ProductAliasMap.from_product_ids(tuple(item.product_id for item in selected))
        raw_plan = AgentPlan.model_validate(state["plan"])
        workflow = request.workflow_hint or raw_plan.workflow
        aspects = request.aspects or raw_plan.aspects
        authorized = AgentPlan(
            workflow=workflow,
            product_terms=tuple(alias_map.alias_to_local),
            aspects=aspects,
        )
        return mark(
            state, "resolve_products", "resolved", status="RUNNING",
            workflow=workflow.value, requested_aspects=[item.value for item in aspects],
            product_aliases=alias_map.local_to_alias,
            local_product_map=alias_map.alias_to_local,
            authorized_plan=authorized.model_dump(mode="json"),
            unresolved_product_terms=[], product_candidates=candidate_dtos,
        )

    def clarify_once(state: AgentState) -> AgentState:
        payload = {
            "type": "product_selection",
            "unresolved_terms": state.get("unresolved_product_terms", []),
            "candidates": state.get("product_candidates", []),
        }
        resume = interrupt(payload)
        selection = resume.get("product_ids") if isinstance(resume, dict) else resume
        if isinstance(selection, str):
            selection = [selection]
        valid_shape = (
            isinstance(selection, list) and 1 <= len(selection) <= 5
            and all(isinstance(item, str) and item for item in selection)
            and len(set(selection)) == len(selection)
        )
        updates: dict[str, Any] = {
            "clarification_count": state.get("clarification_count", 0) + 1,
            "clarification_request": payload,
        }
        if not valid_shape:
            response = AgentResponse(
                run_id=state["run_id"], status="FAILED", rendered_text="The product selection is invalid.",
                error_code=ErrorCode.PRODUCT_AMBIGUOUS,
            )
            updates.update(
                status="FAILED", terminal=True, error_code=ErrorCode.PRODUCT_AMBIGUOUS.value,
                response=response.model_dump(mode="json"), resume_selection=[],
            )
            return mark(state, "clarify_once", "failed", **updates)
        updates.update(status="RUNNING", resume_selection=selection)
        return mark(state, "clarify_once", "clarified", **updates)

    def route_workflow(state: AgentState) -> AgentState:
        plan = AgentPlan.model_validate(state["authorized_plan"])
        count = len(plan.product_terms)
        invalid = (
            (plan.workflow is Workflow.DIAGNOSIS and count != 1)
            or (plan.workflow is Workflow.COMPARISON and not 2 <= count <= 5)
            or (plan.workflow is Workflow.TRIAGE and not 1 <= count <= 5)
        )
        if invalid:
            return failed(state, "route_workflow", ErrorCode.INSUFFICIENT_INPUT.value)
        tool_name = {
            Workflow.DIAGNOSIS: "diagnose_product",
            Workflow.COMPARISON: "compare_products",
            Workflow.TRIAGE: "triage_issues",
        }[plan.workflow]
        return mark(
            state, "route_workflow", "routed", tool_name=tool_name,
            tool_arguments={
                "workflow": plan.workflow.value,
                "product_aliases": list(plan.product_terms),
                "aspects": [item.value for item in plan.aspects],
            },
        )

    def execute_evidence_tool(state: AgentState) -> AgentState:
        runtime = _runtime_from_state(dependencies, state)
        plan = AgentPlan.model_validate(state["authorized_plan"])
        product_ids = tuple(state["local_product_map"][alias] for alias in plan.product_terms)
        aspects = tuple(item.value for item in plan.aspects)
        try:
            if plan.workflow is Workflow.DIAGNOSIS:
                result = runtime.tool.diagnose_product(product_ids[0], aspects)
            elif plan.workflow is Workflow.COMPARISON:
                result = runtime.tool.compare_products(product_ids, aspects)
            else:
                result = runtime.tool.triage_issues(product_ids, aspects)
        except Exception:  # noqa: BLE001 - tool service is an injected local boundary
            result_state = failed(state, "execute_evidence_tool", ErrorCode.STATE_ERROR.value)
            result_state["tool_execution_count"] = runtime.ledger.execution_count
            return result_state
        return mark(
            state, "execute_evidence_tool", "executed",
            tool_result=_tool_result_dto(result),
            tool_execution_count=runtime.ledger.execution_count,
        )

    def safe_evidence(state: AgentState) -> AgentState:
        result = _tool_result_from_dto(state["tool_result"])
        aliases = ProductAliasMap(
            local_to_alias=state["product_aliases"], alias_to_local=state["local_product_map"]
        )
        bundle = build_safe_evidence(result, aliases)
        updates = {
            "safe_evidence_cards": [card.model_dump(mode="json") for card in bundle.cards],
            "local_evidence_items": _local_items_dto(bundle.local_items),
        }
        if not bundle.cards or all(card.returned_evidence_count == 0 for card in bundle.cards):
            updates.update(error_code=ErrorCode.INSUFFICIENT_EVIDENCE.value)
            return mark(state, "build_safe_evidence", "built", **updates)
        return mark(state, "build_safe_evidence", "built", **updates)

    def draft_answer(state: AgentState) -> AgentState:
        if state.get("error_code") == ErrorCode.INSUFFICIENT_EVIDENCE.value:
            return mark(state, "draft_answer", "skipped")
        runtime = _runtime_from_state(dependencies, state)
        calls = state.get("model_call_count", 0) + 1
        try:
            result = runtime.gateway.draft(
                AgentPlan.model_validate(state["authorized_plan"]),
                tuple(SafeEvidenceCard.model_validate(item) for item in state["safe_evidence_cards"]),
            )
        except Exception as error:  # noqa: BLE001 - gateway is an injected boundary
            code = (
                error.error_code
                if isinstance(error, GatewayError)
                else ErrorCode.STATE_ERROR.value
            )
            return mark(
                state, "draft_answer", "failed", error_code=code,
                model_call_count=min(3, calls), **_usage(runtime, state),
            )
        return mark(
            state, "draft_answer", "drafted", draft=result.model_dump(mode="json"),
            model_call_count=calls, **_usage(runtime, state),
        )

    def verify_answer(state: AgentState) -> AgentState:
        verification = verify_draft(
            AgentDraft.model_validate(state["draft"]),
            AgentPlan.model_validate(state["authorized_plan"]),
            tuple(SafeEvidenceCard.model_validate(item) for item in state["safe_evidence_cards"]),
        )
        return mark(
            state, "verify_answer", "verified" if verification.passed else "rejected",
            verification=verification.model_dump(mode="json"),
            error_code=None if verification.passed else verification.error_code.value,
        )

    def repair_once(state: AgentState) -> AgentState:
        runtime = _runtime_from_state(dependencies, state)
        calls = state.get("model_call_count", 0) + 1
        verification = VerificationResult.model_validate(state["verification"])
        try:
            result = runtime.gateway.draft(
                AgentPlan.model_validate(state["authorized_plan"]),
                tuple(SafeEvidenceCard.model_validate(item) for item in state["safe_evidence_cards"]),
                validation_errors=tuple(item.value for item in verification.error_details),
            )
        except Exception as error:  # noqa: BLE001 - gateway is an injected boundary
            code = (
                error.error_code
                if isinstance(error, GatewayError)
                else ErrorCode.STATE_ERROR.value
            )
            return mark(
                state, "repair_once", "failed", repair_count=1, error_code=code,
                model_call_count=min(3, calls), **_usage(runtime, state),
            )
        return mark(
            state, "repair_once", "repaired", repair_count=1,
            draft=result.model_dump(mode="json"), model_call_count=calls,
            **_usage(runtime, state),
        )

    def fallback(state: AgentState) -> AgentState:
        bundle = _bundle_from_state(state)
        code = state.get("error_code") or ErrorCode.VERIFICATION_FAILED.value
        try:
            response = deterministic_fallback(
                run_id=state["run_id"], workflow=state.get("workflow", "diagnosis"),
                bundle=bundle, error_code=code,
                citation_verifier=dependencies.citation_verifier,
            )
        except AgentPolicyError:
            return failed(state, "deterministic_fallback", ErrorCode.POLICY_REJECTED.value)
        except Exception:  # noqa: BLE001 - citation storage is an injected boundary
            return failed(state, "deterministic_fallback", ErrorCode.STATE_ERROR.value)
        return mark(
            state, "deterministic_fallback", "fallback", status="DEGRADED_SAFE_RESULT",
            response=response.model_dump(mode="json"), terminal=True,
        )

    def render_local(state: AgentState) -> AgentState:
        try:
            response = render_verified_response(
                run_id=state["run_id"], draft=AgentDraft.model_validate(state["draft"]),
                verification=VerificationResult.model_validate(state["verification"]),
                bundle=_bundle_from_state(state), citation_verifier=dependencies.citation_verifier,
            )
        except AgentPolicyError:
            return failed(state, "render_local", ErrorCode.POLICY_REJECTED.value)
        except Exception:  # noqa: BLE001 - citation storage is an injected boundary
            return failed(state, "render_local", ErrorCode.STATE_ERROR.value)
        return mark(
            state, "render_local", "rendered", status="VERIFIED_RESULT",
            response=response.model_dump(mode="json"), terminal=True,
        )

    def _write_trace_once(state: AgentState) -> AgentState:
        runtime = dependencies._runs.get(state["run_id"])
        path = [*state.get("graph_path", []), "write_redacted_trace", "END"]
        outcomes = {
            **state.get("node_outcomes", {}), "write_redacted_trace": "written", "END": "written"
        }
        error_codes = [state["error_code"]] if state.get("error_code") else []
        evidence_ids = [
            evidence["evidence_id"]
            for item in state.get("local_evidence_items", [])
            for evidence in item["evidence"]
        ]
        local = {
            "run_id": state["run_id"], "thread_id": state["thread_id"],
            "graph_path": path, "node_outcomes": outcomes, "safe_error_codes": error_codes,
            "local_product_map": state.get("local_product_map", {}),
            "local_evidence_ids": evidence_ids,
            "candidate_titles": [
                item["title"] for item in state.get("product_candidates", []) if item.get("title")
            ],
        }
        verification = state.get("verification") or {
            "passed": False, "error_code": state.get("error_code") or ErrorCode.STATE_ERROR.value
        }
        public = {
            "graph_path": path, "node_outcomes": outcomes, "safe_error_codes": error_codes,
            "counts": {
                "model_calls": state.get("model_call_count", 0),
                "tool_executions": state.get("tool_execution_count", 0),
                "clarifications": state.get("clarification_count", 0),
                "input_tokens": state.get("input_tokens", 0),
                "output_tokens": state.get("output_tokens", 0),
            },
            "latency_ms": (
                max(0.0, (dependencies.clock() - runtime.started) * 1000)
                if runtime is not None
                else 0.0
            ),
            "model_id": dependencies.model_id, "config_id": dependencies.config_id,
            "verifier_result": {
                "passed": bool(verification.get("passed", False)),
                "error_code": verification.get("error_code"),
            },
            "snapshot_hash": dependencies.snapshot_hash, "contract_hash": dependencies.contract_hash,
            "aggregate_metrics": {"budget_exceeded": state.get("model_call_count", 0) > 3},
        }
        dependencies.trace_writer(local, public)
        return {
            "graph_path": path,
            "node_outcomes": outcomes,
            "trace_written": True,
            "trace_metrics": public["counts"],
        }

    def write_trace(state: AgentState) -> AgentState:
        try:
            return _write_trace_once(state)
        except Exception:  # noqa: BLE001 - trace storage is the final local boundary
            path = [*state.get("graph_path", []), "write_redacted_trace", "END"]
            failed_outcomes = {
                **state.get("node_outcomes", {}),
                "write_redacted_trace": "failed",
                "END": "failed",
            }
            response = AgentResponse(
                run_id=state["run_id"],
                status="FAILED",
                workflow=state.get("workflow"),
                rendered_text="The local trace could not be written safely.",
                error_code=ErrorCode.STATE_ERROR,
            )
            return {
                "graph_path": path,
                "node_outcomes": failed_outcomes,
                "trace_written": False,
                "trace_metrics": {
                    "model_calls": state.get("model_call_count", 0),
                    "tool_executions": state.get("tool_execution_count", 0),
                    "clarifications": state.get("clarification_count", 0),
                    "input_tokens": state.get("input_tokens", 0),
                    "output_tokens": state.get("output_tokens", 0),
                },
                "status": "FAILED",
                "terminal": True,
                "error_code": ErrorCode.STATE_ERROR.value,
                "response": response.model_dump(mode="json"),
                "verification": {
                    "passed": False,
                    "error_code": ErrorCode.STATE_ERROR.value,
                    "error_details": [],
                },
            }
        finally:
            dependencies.release_runtime(state["run_id"])

    graph = StateGraph(AgentState)
    graph.add_node("validate_input", validate_input)
    graph.add_node("parse_request", parse_request)
    graph.add_node("resolve_products", resolve_products)
    graph.add_node("clarify_once", clarify_once)
    graph.add_node("route_workflow", route_workflow)
    graph.add_node("execute_evidence_tool", execute_evidence_tool)
    graph.add_node("build_safe_evidence", safe_evidence)
    graph.add_node("draft_answer", draft_answer)
    graph.add_node("verify_answer", verify_answer)
    graph.add_node("repair_once", repair_once)
    graph.add_node("deterministic_fallback", fallback)
    graph.add_node("render_local", render_local)
    graph.add_node("write_redacted_trace", write_trace)
    graph.add_edge(START, "validate_input")
    graph.add_conditional_edges("validate_input", lambda s: "write_redacted_trace" if s.get("terminal") else "parse_request")
    graph.add_conditional_edges("parse_request", lambda s: "write_redacted_trace" if s.get("terminal") else "resolve_products")
    graph.add_conditional_edges(
        "resolve_products",
        lambda s: "write_redacted_trace" if s.get("terminal") else (
            "clarify_once" if s.get("status") == "NEEDS_CLARIFICATION" else "route_workflow"
        ),
    )
    graph.add_conditional_edges("clarify_once", lambda s: "write_redacted_trace" if s.get("terminal") else "resolve_products")
    graph.add_conditional_edges("route_workflow", lambda s: "write_redacted_trace" if s.get("terminal") else "execute_evidence_tool")
    graph.add_conditional_edges("execute_evidence_tool", lambda s: "write_redacted_trace" if s.get("terminal") else "build_safe_evidence")
    graph.add_edge("build_safe_evidence", "draft_answer")
    graph.add_conditional_edges(
        "draft_answer",
        lambda s: "deterministic_fallback" if s.get("error_code") else "verify_answer",
    )
    graph.add_conditional_edges(
        "verify_answer",
        lambda s: "render_local" if s["verification"]["passed"] else (
            "repair_once" if s.get("repair_count", 0) == 0 and s.get("model_call_count", 0) < 3
            else "deterministic_fallback"
        ),
    )
    graph.add_conditional_edges("repair_once", lambda s: "deterministic_fallback" if s.get("error_code") not in {None, ErrorCode.VERIFICATION_FAILED.value} else "verify_answer")
    graph.add_edge("deterministic_fallback", "write_redacted_trace")
    graph.add_edge("render_local", "write_redacted_trace")
    graph.add_edge("write_redacted_trace", END)
    return graph.compile(checkpointer=checkpointer)


def _candidate_dto(candidate: ProductCandidate) -> dict[str, Any]:
    return {"product_id": candidate.product_id, "title": candidate.title}


def _tool_result_dto(result: WorkflowToolResult) -> dict[str, Any]:
    return {"workflow": result.workflow, "items": [_item_dto(item) for item in result.items]}


def _item_dto(item: LocalEvidenceItem) -> dict[str, Any]:
    return {
        "product_id": item.product_id, "aspect": item.aspect, "strength": item.strength,
        "evidence": [
            {
                "evidence_id": evidence.evidence_id, "source_line": evidence.source_line,
                "parent_asin": evidence.parent_asin, "text": evidence.text,
                "rating": evidence.rating, "timestamp": evidence.timestamp,
                "verified_purchase": evidence.verified_purchase,
                "helpful_vote": evidence.helpful_vote, "polarity": evidence.polarity,
            }
            for evidence in item.evidence
        ],
    }


def _tool_result_from_dto(value: dict[str, Any]) -> WorkflowToolResult:
    return WorkflowToolResult(
        workflow=value["workflow"],
        items=tuple(
            LocalEvidenceItem(
                product_id=item["product_id"], aspect=item["aspect"], strength=item["strength"],
                evidence=tuple(Evidence(**evidence) for evidence in item["evidence"]),
            )
            for item in value["items"]
        ),
    )


def _local_items_dto(items: dict[str, LocalEvidenceItem]) -> list[dict[str, Any]]:
    return [{"card_alias": alias, **_item_dto(item)} for alias, item in items.items()]


def _bundle_from_state(state: AgentState) -> SafeEvidenceBundle:
    local = {
        item["card_alias"]: LocalEvidenceItem(
            product_id=item["product_id"], aspect=item["aspect"], strength=item["strength"],
            evidence=tuple(Evidence(**evidence) for evidence in item["evidence"]),
        )
        for item in state.get("local_evidence_items", [])
    }
    return SafeEvidenceBundle(
        cards=tuple(SafeEvidenceCard.model_validate(item) for item in state.get("safe_evidence_cards", [])),
        local_items=local,
    )


def _usage(runtime: _RunRuntime, state: AgentState) -> dict[str, int]:
    records = getattr(runtime.gateway, "records", ())
    runtime.input_tokens = max(runtime.input_tokens, state.get("input_tokens", 0))
    runtime.output_tokens = max(runtime.output_tokens, state.get("output_tokens", 0))
    for record in records[runtime.records_seen :]:
        runtime.input_tokens += record.input_tokens
        runtime.output_tokens += record.output_tokens
    runtime.records_seen = len(records)
    return {
        "model_http_attempt_count": runtime.budget.used,
        "input_tokens": runtime.input_tokens,
        "output_tokens": runtime.output_tokens,
    }


def _runtime_from_state(
    dependencies: AgentGraphDependencies, state: AgentState
) -> _RunRuntime:
    """Restore monotonic run budgets from checkpoint JSON before side effects."""

    runtime = dependencies.runtime(state["run_id"])
    runtime.budget.used = max(runtime.budget.used, state.get("model_http_attempt_count", 0))
    runtime.ledger.execution_count = max(
        runtime.ledger.execution_count, state.get("tool_execution_count", 0)
    )
    return runtime
