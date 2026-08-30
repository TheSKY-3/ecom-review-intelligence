import gc
import json
import sqlite3
import weakref
from pathlib import Path
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from ecom_review_intelligence.agent_contracts import AgentDraft, AgentPlan, AgentRequest
from ecom_review_intelligence.agent_graph import (
    AgentGraphDependencies,
    build_agent_graph,
    new_run,
    open_sqlite_agent_graph,
)
from ecom_review_intelligence.agent_tools import (
    LocalEvidenceItem,
    ProductCandidate,
    WorkflowToolResult,
)
from ecom_review_intelligence.agent_trace import serialize_local_trace, serialize_public_trace
from ecom_review_intelligence.engine import Evidence
from ecom_review_intelligence.model_gateway import FakeModelGateway, GatewayError


class Resolver:
    def by_exact_id(self, product_id: str):
        return ProductCandidate(product_id, f"Title {product_id}") if product_id in {"B001", "B002"} else None

    def by_title_substring(self, keyword: str, *, limit: int = 5):
        del limit
        return {
            "one": (ProductCandidate("B001", "Only one"),),
            "many": (ProductCandidate("B001", "First"), ProductCandidate("B002", "Second")),
            "none": (),
        }.get(keyword, ())


class Tool:
    def __init__(self, ledger):
        self.ledger = ledger

    def _result(self, workflow, products, aspects):
        self.ledger.consume()
        items = []
        for product in products:
            for aspect in aspects:
                items.append(
                    LocalEvidenceItem(
                        product_id=product,
                        aspect=aspect,
                        strength="MODERATE",
                        evidence=(
                            Evidence(
                                evidence_id=f"ev-{product}-{aspect}", source_line=1,
                                parent_asin=product, text="quiet good", rating=5.0,
                                timestamp=1, verified_purchase=True, helpful_vote=1,
                                polarity="supporting",
                            ),
                        ),
                    )
                )
        return WorkflowToolResult(workflow, tuple(items))

    def diagnose_product(self, product, aspects):
        return self._result("diagnosis", (product,), aspects)

    def compare_products(self, products, aspects):
        return self._result("comparison", products, aspects)

    def triage_issues(self, products, aspects):
        return self._result("triage", products, aspects)


class EmptyTool(Tool):
    def _result(self, workflow, products, aspects):
        self.ledger.consume()
        return WorkflowToolResult(
            workflow,
            tuple(
                LocalEvidenceItem(product, aspect, "INSUFFICIENT", ())
                for product in products
                for aspect in aspects
            ),
        )


class RaisingResolver(Resolver):
    def by_exact_id(self, product_id: str):
        raise RuntimeError("local store unavailable")


class RaisingTool(Tool):
    def diagnose_product(self, product, aspects):
        raise RuntimeError("sqlite read failed")


class StateBudgetGateway:
    def __init__(self, budget, *, fail_draft=False):
        self.call_kinds = []
        self.fail_draft = fail_draft
        self.budget = budget

    def plan(self, question):
        self.budget.consume()
        self.call_kinds.append("plan")
        return plan()

    def draft(self, plan_value, cards, *, validation_errors=()):
        del plan_value, cards, validation_errors
        self.budget.consume()
        self.call_kinds.append("draft")
        if self.fail_draft:
            raise GatewayError("API_UNAVAILABLE", "failed")
        return draft(card="CARD_99")


class ExplodingBudgetGateway:
    def __init__(self, budget, actual_attempts):
        self.budget = budget
        self.actual_attempts = actual_attempts

    def plan(self, question):
        del question
        self.budget.consume()
        self.actual_attempts.append("plan")
        raise RuntimeError("provider leaked authorization body")

    def draft(self, plan_value, cards, *, validation_errors=()):
        del plan_value, cards, validation_errors
        self.budget.consume()
        self.actual_attempts.append("draft")
        raise RuntimeError("provider leaked authorization body")


def plan(workflow="diagnosis", terms=("untrusted title",), aspects=("noise",)):
    return AgentPlan(workflow=workflow, product_terms=terms, aspects=aspects)


def draft(workflow="diagnosis", products=("PRODUCT_1",), aspects=("noise",), card="CARD_1"):
    return AgentDraft(
        workflow=workflow,
        selected_product_aliases=products,
        selected_aspects=aspects,
        claims=({
            "claim_type": "aspect_summary", "product_aliases": (products[0],),
            "aspect": aspects[0], "polarity": "supporting",
            "evidence_strength": "MODERATE", "safe_evidence_card_refs": (card,),
        },),
        limitation_codes=("REVIEW_EVIDENCE_ONLY",),
    )


def deps(gateways, traces):
    scripted = iter(gateways)
    return AgentGraphDependencies(
        resolver=Resolver(),
        gateway_factory=lambda budget: next(scripted),
        tool_factory=lambda ledger: Tool(ledger),
        trace_writer=lambda local, public: traces.append(
            (serialize_local_trace(local), serialize_public_trace(public))
        ),
        citation_verifier=lambda evidence_id: evidence_id.startswith("ev-"),
        snapshot_hash="a" * 64,
        contract_hash="b" * 64,
        uuid_factory=lambda: str(uuid4()),
    )


def invoke(graph, request, thread_id=None):
    state, config = new_run(request, thread_id=thread_id)
    return graph.invoke(state, config), config


def test_dependencies_inject_fresh_run_and_thread_identifiers():
    values = iter(
        (
            "11111111-1111-4111-8111-111111111111",
            "22222222-2222-4222-8222-222222222222",
        )
    )
    dependencies = deps([], [])
    dependencies.uuid_factory = lambda: next(values)

    state, config = dependencies.new_run(AgentRequest(question="safe"))

    assert state["run_id"] == "11111111-1111-4111-8111-111111111111"
    assert state["thread_id"] == "22222222-2222-4222-8222-222222222222"
    assert config["configurable"]["thread_id"] == state["thread_id"]


def test_normal_diagnosis_uses_authorized_local_alias_and_one_tool():
    traces = []
    gateway = FakeModelGateway(plans=[plan()], drafts=[draft()])
    graph = build_agent_graph(deps([gateway], traces), checkpointer=InMemorySaver())

    result, _ = invoke(graph, AgentRequest(question="B001 noise", exact_product_ids=("B001",), aspects=("noise",)))

    assert result["response"]["status"] == "VERIFIED_RESULT"
    assert result["authorized_plan"]["product_terms"] == ["PRODUCT_1"]
    assert result["tool_execution_count"] == 1
    assert result["model_call_count"] == 2
    assert result["graph_path"] == [
        "validate_input", "parse_request", "resolve_products", "route_workflow",
        "execute_evidence_tool", "build_safe_evidence", "draft_answer",
        "verify_answer", "render_local", "write_redacted_trace", "END",
    ]
    assert len(traces) == 1
    json.dumps(result)


def test_model_product_term_is_locally_resolved_but_never_directly_authorized():
    traces = []
    gateway = FakeModelGateway(plans=[plan(terms=("one",))], drafts=[draft()])
    graph = build_agent_graph(deps([gateway], traces), checkpointer=InMemorySaver())

    result, _ = invoke(graph, AgentRequest(question="Which one is noisy?", aspects=("noise",)))

    assert result["response"]["status"] == "VERIFIED_RESULT"
    assert result["plan"]["product_terms"] == ["one"]
    assert result["authorized_plan"]["product_terms"] == ["PRODUCT_1"]
    assert result["local_product_map"] == {"PRODUCT_1": "B001"}


def test_resolver_and_store_exceptions_fail_closed_through_trace():
    for resolver, tool_factory in (
        (RaisingResolver(), lambda ledger: Tool(ledger)),
        (Resolver(), lambda ledger: RaisingTool(ledger)),
    ):
        traces = []
        dependencies = deps([FakeModelGateway(plans=[plan()], drafts=[draft()])], traces)
        dependencies.resolver = resolver
        dependencies.tool_factory = tool_factory
        graph = build_agent_graph(dependencies, checkpointer=InMemorySaver())

        result, _ = invoke(
            graph,
            AgentRequest(question="B001", exact_product_ids=("B001",), aspects=("noise",)),
        )

        assert result["response"]["status"] == "FAILED"
        assert result["error_code"] == "STATE_ERROR"
        assert result["graph_path"][-2:] == ["write_redacted_trace", "END"]
        assert len(traces) == 1


def test_comparison_and_triage_route_to_their_only_business_tool():
    for workflow, products in (("comparison", ("B001", "B002")), ("triage", ("B001", "B002"))):
        aliases = ("PRODUCT_1", "PRODUCT_2")
        traces = []
        scripted_draft = draft(workflow, aliases, card="CARD_1")
        if workflow in {"comparison", "triage"}:
            scripted_draft = AgentDraft(
                workflow=workflow, selected_product_aliases=aliases,
                selected_aspects=("noise",),
                claims=tuple({
                    "claim_type": (
                        "validation_priority" if workflow == "triage" else "aspect_summary"
                    ), "product_aliases": (alias,),
                    "aspect": "noise", "polarity": "supporting",
                    "evidence_strength": "MODERATE", "safe_evidence_card_refs": (f"CARD_{index}",),
                } for index, alias in enumerate(aliases, 1)),
                limitation_codes=("REVIEW_EVIDENCE_ONLY",),
            )
        gateway = FakeModelGateway(
            plans=[plan(workflow, aspects=("noise",))],
            drafts=[scripted_draft],
        )
        graph = build_agent_graph(deps([gateway], traces), checkpointer=InMemorySaver())
        result, _ = invoke(graph, AgentRequest(
            question="compare", exact_product_ids=products, aspects=("noise",), workflow_hint=workflow,
        ))
        assert result["response"]["status"] == "VERIFIED_RESULT"
        assert result["tool_name"] == {"comparison": "compare_products", "triage": "triage_issues"}[workflow]
        assert result["tool_execution_count"] == 1


def test_rejected_draft_repairs_once_then_verifies():
    traces = []
    bad = draft(card="CARD_99")
    gateway = FakeModelGateway(plans=[plan()], drafts=[bad, draft()])
    graph = build_agent_graph(deps([gateway], traces), checkpointer=InMemorySaver())

    result, _ = invoke(graph, AgentRequest(question="B001", exact_product_ids=("B001",), aspects=("noise",)))

    assert result["response"]["status"] == "VERIFIED_RESULT"
    assert result["repair_count"] == 1
    assert result["model_call_count"] == 3
    assert result["graph_path"].count("verify_answer") == 2


def test_model_failure_and_second_rejection_take_traced_fallback():
    for gateway in (
        FakeModelGateway(plans=[plan()], drafts=[]),
        FakeModelGateway(plans=[plan()], drafts=[draft(card="CARD_99"), draft(card="CARD_99")]),
    ):
        traces = []
        graph = build_agent_graph(deps([gateway], traces), checkpointer=InMemorySaver())
        result, _ = invoke(graph, AgentRequest(question="B001", exact_product_ids=("B001",), aspects=("noise",)))
        assert result["response"]["status"] == "DEGRADED_SAFE_RESULT"
        assert result["graph_path"][-2:] == ["write_redacted_trace", "END"]
        assert len(traces) == 1
        assert result["model_call_count"] <= 3


def test_insufficient_evidence_skips_draft_and_returns_traced_degraded_result():
    traces = []
    gateway = FakeModelGateway(plans=[plan()], drafts=[])
    dependencies = deps([gateway], traces)
    dependencies.tool_factory = lambda ledger: EmptyTool(ledger)
    graph = build_agent_graph(dependencies, checkpointer=InMemorySaver())

    result, _ = invoke(
        graph,
        AgentRequest(question="B001", exact_product_ids=("B001",), aspects=("noise",)),
    )

    assert result["response"]["status"] == "DEGRADED_SAFE_RESULT"
    assert result["error_code"] == "INSUFFICIENT_EVIDENCE"
    assert gateway.call_kinds == ("plan",)
    assert result["tool_execution_count"] == 1
    assert len(traces) == 1


def test_citation_failure_becomes_a_traced_failed_terminal_state():
    traces = []
    gateway = FakeModelGateway(plans=[plan()], drafts=[draft()])
    dependencies = deps([gateway], traces)
    dependencies.citation_verifier = lambda evidence_id: False
    graph = build_agent_graph(dependencies, checkpointer=InMemorySaver())

    result, _ = invoke(
        graph,
        AgentRequest(question="B001", exact_product_ids=("B001",), aspects=("noise",)),
    )

    assert result["response"]["status"] == "FAILED"
    assert result["error_code"] == "POLICY_REJECTED"
    assert result["trace_written"] is True
    assert len(traces) == 1


def test_invalid_clarification_resume_never_executes_or_interrupts_twice():
    traces = []
    gateway = FakeModelGateway(plans=[plan()], drafts=[draft()])
    graph = build_agent_graph(deps([gateway], traces), checkpointer=InMemorySaver())
    _, config = invoke(graph, AgentRequest(question="choose", product_title_keywords=("many",), aspects=("noise",)))

    result = graph.invoke(Command(resume={"product_ids": ["B999"]}), config)

    assert result["response"]["status"] == "FAILED"
    assert result["error_code"] == "PRODUCT_AMBIGUOUS"
    assert result["clarification_count"] == 1
    assert result["tool_execution_count"] == 0
    # The interrupted attempt has no state update; only the completed resume is recorded.
    assert result["graph_path"].count("clarify_once") == 1
    assert len(traces) == 1


def test_zero_candidate_interrupts_once_then_rejects_unvalidated_resume_without_replan():
    traces = []
    gateway = FakeModelGateway(plans=[plan(terms=("none",))], drafts=[draft()])
    graph = build_agent_graph(deps([gateway], traces), checkpointer=InMemorySaver())
    interrupted, config = invoke(graph, AgentRequest(question="unknown", aspects=("noise",)))
    assert "__interrupt__" in interrupted

    result = graph.invoke(Command(resume={"product_ids": ["B999"]}), config)

    assert result["response"]["status"] == "FAILED"
    assert result["error_code"] == "PRODUCT_NOT_FOUND"
    assert result["clarification_count"] == 1
    assert gateway.call_kinds == ("plan",)
    assert result["tool_execution_count"] == 0
    assert len(traces) == 1


def test_reconstructed_dependencies_cannot_exceed_checkpointed_three_call_budget(tmp_path: Path):
    traces = []
    db = tmp_path / "budget.sqlite"
    request = AgentRequest(question="choose", product_title_keywords=("many",), aspects=("noise",))
    state, config = new_run(request)
    first_gateways = []
    dependencies = deps([], traces)
    dependencies.gateway_factory = lambda budget: first_gateways.append(StateBudgetGateway(budget)) or first_gateways[-1]
    with SqliteSaver.from_conn_string(str(db)) as saver:
        graph = build_agent_graph(dependencies, checkpointer=saver)
        interrupted = graph.invoke(state, config)
        assert "__interrupt__" in interrupted
    second_gateways = []
    dependencies = deps([], traces)
    dependencies.gateway_factory = lambda budget: second_gateways.append(StateBudgetGateway(budget)) or second_gateways[-1]
    with SqliteSaver.from_conn_string(str(db)) as saver:
        graph = build_agent_graph(dependencies, checkpointer=saver)
        result = graph.invoke(Command(resume={"product_ids": ["B001"]}), config)

    assert result["response"]["status"] == "DEGRADED_SAFE_RESULT"
    assert result["model_call_count"] == 3
    assert first_gateways[0].call_kinds == ["plan"]
    assert second_gateways[0].call_kinds == ["draft", "draft"]
    assert result["model_http_attempt_count"] == 3
    assert result["tool_execution_count"] == 1


def test_arbitrary_gateway_exception_checkpoints_consumed_budget_and_never_exceeds_three(
    tmp_path: Path,
):
    traces = []
    actual_attempts = []
    db = tmp_path / "gateway-errors.sqlite"
    request = AgentRequest(question="one", aspects=("noise",))
    state, config = new_run(request)
    results = []
    for _ in range(5):
        dependencies = deps([], traces)
        dependencies.gateway_factory = lambda budget: ExplodingBudgetGateway(
            budget, actual_attempts
        )
        with SqliteSaver.from_conn_string(str(db)) as saver:
            graph = build_agent_graph(dependencies, checkpointer=saver)
            results.append(graph.invoke(state, config))

    assert actual_attempts == ["plan", "plan", "plan"]
    assert all(result["response"]["status"] == "FAILED" for result in results)
    assert all(result["error_code"] in {"STATE_ERROR", "MODEL_BUDGET_EXCEEDED"} for result in results)
    assert results[-1]["model_http_attempt_count"] == 3
    assert results[-1]["model_call_count"] <= 3
    assert all(result["graph_path"][-2:] == ["write_redacted_trace", "END"] for result in results)
    assert len(traces) == 5


def test_citation_storage_exceptions_are_stable_traced_failures():
    for gateway in (
        FakeModelGateway(plans=[plan(terms=("one",))], drafts=[draft()]),
        FakeModelGateway(plans=[plan(terms=("one",))], drafts=[]),
    ):
        traces = []
        dependencies = deps([gateway], traces)

        def unavailable(evidence_id):
            del evidence_id
            raise RuntimeError("sqlite path and review text must not escape")

        dependencies.citation_verifier = unavailable
        graph = build_agent_graph(dependencies, checkpointer=InMemorySaver())
        result, _ = invoke(graph, AgentRequest(question="one", aspects=("noise",)))

        assert result["response"]["status"] == "FAILED"
        assert result["error_code"] == "STATE_ERROR"
        assert "sqlite" not in result["response"]["rendered_text"].lower()
        assert result["graph_path"][-2:] == ["write_redacted_trace", "END"]
        assert len(traces) == 1


def test_sqlite_close_reopen_resumes_same_thread_without_replanning_or_cross_talk(tmp_path: Path):
    traces = []
    first_gateway = FakeModelGateway(plans=[plan()], drafts=[draft()])
    second_gateway = FakeModelGateway(plans=[plan()], drafts=[draft()])
    dependencies = deps([first_gateway, second_gateway], traces)
    db = tmp_path / "checkpoints.sqlite"
    request = AgentRequest(question="choose", product_title_keywords=("many",), aspects=("noise",))
    state1, config1 = new_run(request)
    state2, config2 = new_run(request)
    with SqliteSaver.from_conn_string(str(db)) as saver:
        graph = build_agent_graph(dependencies, checkpointer=saver)
        interrupted1 = graph.invoke(state1, config1)
        interrupted2 = graph.invoke(state2, config2)
        assert "__interrupt__" in interrupted1 and "__interrupt__" in interrupted2
    with SqliteSaver.from_conn_string(str(db)) as saver:
        graph = build_agent_graph(dependencies, checkpointer=saver)
        result1 = graph.invoke(Command(resume={"product_ids": ["B001"]}), config1)
        result2 = graph.invoke(Command(resume={"product_ids": ["B002"]}), config2)

    assert first_gateway.call_kinds == ("plan", "draft")
    assert second_gateway.call_kinds == ("plan", "draft")
    assert result1["local_product_map"] == {"PRODUCT_1": "B001"}
    assert result2["local_product_map"] == {"PRODUCT_1": "B002"}
    assert all(result["clarification_count"] == 1 for result in (result1, result2))


def test_production_sqlite_graph_uses_project_root_closes_and_resumes(
    tmp_path: Path, monkeypatch
):
    project_root = tmp_path / "project"
    unrelated_cwd = tmp_path / "elsewhere"
    unrelated_cwd.mkdir()
    monkeypatch.chdir(unrelated_cwd)
    traces = []
    first_gateway = FakeModelGateway(plans=[plan()], drafts=[])
    first_dependencies = deps([first_gateway], traces)
    request = AgentRequest(
        question="choose", product_title_keywords=("many",), aspects=("noise",)
    )
    state, config = new_run(request)

    with open_sqlite_agent_graph(first_dependencies, project_root=project_root) as graph:
        interrupted = graph.invoke(state, config)
        assert "__interrupt__" in interrupted
        saver = graph.checkpointer

    checkpoint_path = project_root / "data" / "processed" / "p6_agent_checkpoints.sqlite"
    assert checkpoint_path.is_file()
    assert not (unrelated_cwd / "data").exists()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        saver.conn.execute("SELECT 1")

    second_gateway = FakeModelGateway(plans=[], drafts=[draft()])
    second_dependencies = deps([second_gateway], traces)
    with open_sqlite_agent_graph(second_dependencies, project_root=project_root) as graph:
        result = graph.invoke(Command(resume={"product_ids": ["B001"]}), config)

    assert first_gateway.call_kinds == ("plan",)
    assert second_gateway.call_kinds == ("draft",)
    assert result["response"]["status"] == "VERIFIED_RESULT"
    assert result["tool_execution_count"] == 1
    assert "checkpoint_id" not in config["configurable"]


def test_trace_storage_error_is_safe_honest_and_does_not_create_gateway_runtime():
    gateway_creations = []

    def forbidden_gateway(budget):
        gateway_creations.append(budget)
        raise AssertionError("terminal trace must not create a gateway")

    dependencies = AgentGraphDependencies(
        resolver=Resolver(),
        gateway_factory=forbidden_gateway,
        tool_factory=lambda ledger: Tool(ledger),
        trace_writer=lambda local, public: (_ for _ in ()).throw(
            OSError("C:\\private\\review.txt /home/user/key sk-secret123")
        ),
        citation_verifier=lambda evidence_id: True,
        snapshot_hash="a" * 64,
        contract_hash="b" * 64,
    )
    run_id = str(uuid4())
    thread_id = str(uuid4())
    invalid_state = {
        "request": {}, "run_id": run_id, "thread_id": thread_id,
        "status": "RUNNING", "clarification_count": 0, "repair_count": 0,
        "tool_execution_count": 0, "model_call_count": 0,
        "model_http_attempt_count": 0, "input_tokens": 0, "output_tokens": 0,
        "graph_path": [], "node_outcomes": {}, "terminal": False,
        "trace_written": False,
    }
    graph = build_agent_graph(dependencies, checkpointer=InMemorySaver())

    result = graph.invoke(invalid_state, {"configurable": {"thread_id": thread_id}})

    assert result["response"]["status"] == "FAILED"
    assert result["error_code"] == "STATE_ERROR"
    assert result["trace_written"] is False
    assert result["node_outcomes"]["write_redacted_trace"] == "failed"
    serialized = json.dumps(result)
    assert "private" not in serialized
    assert "/home/" not in serialized
    assert "sk-secret" not in serialized
    assert gateway_creations == []
    assert dependencies._runs == {}


def test_terminal_runs_release_runtime_and_do_not_grow_registry():
    traces = []
    gateways = [
        FakeModelGateway(plans=[plan(terms=("one",))], drafts=[draft()])
        for _ in range(30)
    ]
    dependencies = deps(gateways, traces)
    first_runtime_ref = None
    for index in range(30):
        state, config = dependencies.new_run(
            AgentRequest(question="one", aspects=("noise",))
        )
        if index == 0:
            first_runtime_ref = weakref.ref(dependencies.runtime(state["run_id"]))
        result = build_agent_graph(
            dependencies, checkpointer=InMemorySaver()
        ).invoke(state, config)
        assert result["trace_written"] is True
        assert dependencies._runs == {}

    gc.collect()
    assert first_runtime_ref is not None
    assert first_runtime_ref() is None


def test_interrupt_retains_runtime_until_resume_terminal_then_releases_it():
    traces = []
    gateway = FakeModelGateway(plans=[plan()], drafts=[draft()])
    dependencies = deps([gateway], traces)
    graph = build_agent_graph(dependencies, checkpointer=InMemorySaver())
    state, config = dependencies.new_run(
        AgentRequest(
            question="choose", product_title_keywords=("many",), aspects=("noise",)
        )
    )

    interrupted = graph.invoke(state, config)
    assert "__interrupt__" in interrupted
    assert state["run_id"] in dependencies._runs

    result = graph.invoke(Command(resume={"product_ids": ["B001"]}), config)
    assert result["response"]["status"] == "VERIFIED_RESULT"
    assert state["run_id"] not in dependencies._runs
