from pathlib import Path

import pytest
from pydantic import ValidationError

from ecom_review_intelligence.agent_contracts import (
    AgentClaim,
    AgentDraft,
    AgentPlan,
    AgentRequest,
    AgentResponse,
    AgentState,
    AgentStatus,
    ClaimType,
    ErrorCode,
    SafeEvidenceCard,
    ToolArguments,
    TraceMetrics,
    VerificationResult,
    Workflow,
    assert_json_serializable_state,
)


def test_agent_request_accepts_only_bounded_known_fields() -> None:
    request = AgentRequest(
        question="Compare noise for these two products",
        exact_product_ids=("B001", "B002"),
        aspects=("noise",),
        workflow_hint="comparison",
    )

    assert request.workflow_hint is Workflow.COMPARISON
    assert request.aspects == ("noise",)

    with pytest.raises(ValidationError):
        AgentRequest(question=" ")
    with pytest.raises(ValidationError):
        AgentRequest(question="x" * 2001)
    with pytest.raises(ValidationError):
        AgentRequest(question="valid", hidden_instruction="execute")


def test_agent_plan_and_tool_arguments_reject_unknown_workflows_aspects_and_aliases() -> None:
    plan = AgentPlan(
        workflow="diagnosis",
        product_terms=("PRODUCT_1",),
        aspects=("reliability",),
    )
    arguments = ToolArguments(
        workflow=plan.workflow,
        product_aliases=plan.product_terms,
        aspects=plan.aspects,
    )

    assert arguments.workflow is Workflow.DIAGNOSIS

    with pytest.raises(ValidationError):
        AgentPlan(workflow="shopping", product_terms=("PRODUCT_1",), aspects=("noise",))
    with pytest.raises(ValidationError):
        AgentPlan(
            workflow="diagnosis",
            product_terms=("PRODUCT_1",),
            aspects=("made_up_aspect",),
        )
    with pytest.raises(ValidationError):
        ToolArguments(
            workflow="comparison",
            product_aliases=("LOCAL-B001", "PRODUCT_2"),
            aspects=("noise",),
        )


def test_comparison_tool_arguments_require_two_to_five_products() -> None:
    with pytest.raises(ValidationError):
        ToolArguments(
            workflow="comparison",
            product_aliases=("PRODUCT_1",),
            aspects=("noise",),
        )

    value = ToolArguments(
        workflow="comparison",
        product_aliases=tuple(f"PRODUCT_{number}" for number in range(1, 6)),
        aspects=("noise", "reliability"),
    )
    assert len(value.product_aliases) == 5


def test_safe_evidence_card_counts_must_match_returned_count() -> None:
    card = SafeEvidenceCard(
        card_alias="CARD_1",
        product_alias="PRODUCT_1",
        aspect="noise",
        evidence_strength="MODERATE",
        supporting_count=1,
        opposing_count=1,
        conflicting_count=0,
        insufficient_count=0,
        returned_evidence_count=2,
        quality_limitation_codes=("SILVER_LABELS",),
    )
    assert card.returned_evidence_count == 2

    with pytest.raises(ValidationError):
        SafeEvidenceCard(
            card_alias="CARD_1",
            product_alias="PRODUCT_1",
            aspect="noise",
            evidence_strength="MODERATE",
            supporting_count=1,
            opposing_count=1,
            conflicting_count=0,
            insufficient_count=0,
            returned_evidence_count=3,
        )


def test_agent_draft_has_no_free_form_fact_or_identifier_fields() -> None:
    claim = AgentClaim(
        claim_type="aspect_summary",
        product_aliases=("PRODUCT_1",),
        aspect="noise",
        polarity="opposing",
        evidence_strength="WEAK",
        safe_evidence_card_refs=("CARD_1",),
    )
    draft = AgentDraft(
        workflow="diagnosis",
        selected_product_aliases=("PRODUCT_1",),
        selected_aspects=("noise",),
        claims=(claim,),
        follow_up_action_codes=("VALIDATE_LOCALLY",),
        limitation_codes=("REVIEW_EVIDENCE_ONLY",),
    )

    assert draft.claims[0].claim_type is ClaimType.ASPECT_SUMMARY

    with pytest.raises(ValidationError):
        AgentClaim(
            claim_type="aspect_summary",
            product_aliases=("PRODUCT_1",),
            aspect="noise",
            polarity="opposing",
            evidence_strength="WEAK",
            safe_evidence_card_refs=("CARD_1",),
            defect_rate=0.75,
        )
    with pytest.raises(ValidationError):
        AgentDraft(
            workflow="diagnosis",
            selected_product_aliases=("PRODUCT_1",),
            selected_aspects=("noise",),
            claims=(claim,),
            winner="PRODUCT_1",
        )


def test_verification_response_and_trace_use_frozen_codes() -> None:
    verification = VerificationResult(
        passed=False,
        error_code="VERIFICATION_FAILED",
        error_details=("UNKNOWN_CARD",),
    )
    response = AgentResponse(
        run_id="run-1",
        status="DEGRADED_SAFE_RESULT",
        workflow="triage",
        rendered_text="本地确定性降级结果",
        error_code="API_UNAVAILABLE",
    )
    metrics = TraceMetrics(
        model_call_count=2,
        clarification_count=0,
        tool_execution_count=1,
        input_tokens=10,
        output_tokens=5,
        latency_ms=20.0,
    )

    assert verification.error_code is ErrorCode.VERIFICATION_FAILED
    assert response.status is AgentStatus.DEGRADED_SAFE_RESULT
    assert metrics.model_call_count == 2

    with pytest.raises(ValidationError):
        VerificationResult(passed=False, error_code="MADE_UP")
    with pytest.raises(ValidationError):
        TraceMetrics(model_call_count=4)


def test_agent_state_accepts_json_values_and_rejects_runtime_objects() -> None:
    state: AgentState = {
        "run_id": "run-1",
        "status": "RUNNING",
        "model_call_count": 0,
        "clarification_count": 0,
        "trace_metrics": {"node_path": ["validate_input"]},
    }
    assert_json_serializable_state(state)

    with pytest.raises(TypeError, match="JSON-serializable"):
        assert_json_serializable_state({**state, "store_path": Path("local.sqlite")})
