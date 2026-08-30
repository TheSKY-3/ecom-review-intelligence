"""Strict framework-neutral contracts for the constrained P6 Agent."""

from __future__ import annotations

import json
from enum import Enum
from typing import Annotated, Any, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Workflow(str, Enum):
    DIAGNOSIS = "diagnosis"
    COMPARISON = "comparison"
    TRIAGE = "triage"


class Aspect(str, Enum):
    ICE_QUALITY = "ice_quality"
    NOISE = "noise"
    RELIABILITY = "reliability"
    CLEANING_MAINTENANCE = "cleaning_maintenance"
    CAPACITY_SPEED = "capacity_speed"
    EASE_SETUP = "ease_setup"
    LEAKAGE = "leakage"
    SIZE_PORTABILITY = "size_portability"


class ClaimType(str, Enum):
    ASPECT_SUMMARY = "aspect_summary"
    CROSS_PRODUCT_CONTRAST = "cross_product_contrast"
    VALIDATION_PRIORITY = "validation_priority"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class EvidenceStrength(str, Enum):
    INSUFFICIENT = "INSUFFICIENT"
    WEAK = "WEAK"
    MODERATE = "MODERATE"
    STRONG = "STRONG"


class Polarity(str, Enum):
    SUPPORTING = "supporting"
    OPPOSING = "opposing"
    CONFLICTING = "conflicting"
    INSUFFICIENT = "insufficient"


class FollowUpActionCode(str, Enum):
    VALIDATE_LOCALLY = "VALIDATE_LOCALLY"
    CHECK_MORE_REVIEWS = "CHECK_MORE_REVIEWS"
    REVIEW_CONFLICTS = "REVIEW_CONFLICTS"


class LimitationCode(str, Enum):
    SILVER_LABELS = "SILVER_LABELS"
    LIMITED_SAMPLE = "LIMITED_SAMPLE"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    REVIEW_EVIDENCE_ONLY = "REVIEW_EVIDENCE_ONLY"


class ErrorCode(str, Enum):
    INVALID_INPUT = "INVALID_INPUT"
    INSUFFICIENT_INPUT = "INSUFFICIENT_INPUT"
    PRODUCT_NOT_FOUND = "PRODUCT_NOT_FOUND"
    PRODUCT_AMBIGUOUS = "PRODUCT_AMBIGUOUS"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    POLICY_REJECTED = "POLICY_REJECTED"
    MODEL_OUTPUT_INVALID = "MODEL_OUTPUT_INVALID"
    MODEL_BUDGET_EXCEEDED = "MODEL_BUDGET_EXCEEDED"
    API_UNAVAILABLE = "API_UNAVAILABLE"
    STATE_ERROR = "STATE_ERROR"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"


class AgentStatus(str, Enum):
    RUNNING = "RUNNING"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    VERIFIED_RESULT = "VERIFIED_RESULT"
    DEGRADED_SAFE_RESULT = "DEGRADED_SAFE_RESULT"
    FAILED = "FAILED"


class ValidationIssueCode(str, Enum):
    UNKNOWN_ALIAS = "UNKNOWN_ALIAS"
    UNKNOWN_CARD = "UNKNOWN_CARD"
    VALUE_MISMATCH = "VALUE_MISMATCH"
    WORKFLOW_RULE_VIOLATION = "WORKFLOW_RULE_VIOLATION"
    PROHIBITED_FIELD = "PROHIBITED_FIELD"
    CITATION_INVALID = "CITATION_INVALID"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class AgentRequest(_StrictModel):
    question: str = Field(min_length=1, max_length=2000)
    exact_product_ids: tuple[str, ...] = Field(default=(), max_length=5)
    product_title_keywords: tuple[str, ...] = Field(default=(), max_length=5)
    aspects: tuple[Aspect, ...] = Field(default=(), max_length=8)
    workflow_hint: Workflow | None = None


class AgentPlan(_StrictModel):
    workflow: Workflow
    product_terms: tuple[str, ...] = Field(min_length=1, max_length=5)
    aspects: tuple[Aspect, ...] = Field(min_length=1, max_length=8)


class ToolArguments(_StrictModel):
    workflow: Workflow
    product_aliases: tuple[str, ...] = Field(min_length=1, max_length=5)
    aspects: tuple[Aspect, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_workflow_shape(self) -> ToolArguments:
        if any(not _is_alias(value, "PRODUCT") for value in self.product_aliases):
            raise ValueError("Tool product references must be run-scoped PRODUCT aliases.")
        product_count = len(self.product_aliases)
        if self.workflow is Workflow.DIAGNOSIS and product_count != 1:
            raise ValueError("Diagnosis requires exactly one product.")
        if self.workflow is Workflow.COMPARISON and not 2 <= product_count <= 5:
            raise ValueError("Comparison requires two to five products.")
        return self


class SafeEvidenceCard(_StrictModel):
    card_alias: str
    product_alias: str
    aspect: Aspect
    evidence_strength: EvidenceStrength
    supporting_count: int = Field(ge=0)
    opposing_count: int = Field(ge=0)
    conflicting_count: int = Field(ge=0)
    insufficient_count: int = Field(ge=0)
    returned_evidence_count: int = Field(ge=0)
    quality_limitation_codes: tuple[LimitationCode, ...] = ()

    @model_validator(mode="after")
    def validate_aliases_and_counts(self) -> SafeEvidenceCard:
        if not _is_alias(self.card_alias, "CARD"):
            raise ValueError("Evidence cards require a run-scoped CARD alias.")
        if not _is_alias(self.product_alias, "PRODUCT"):
            raise ValueError("Evidence cards require a run-scoped PRODUCT alias.")
        counted = (
            self.supporting_count
            + self.opposing_count
            + self.conflicting_count
            + self.insufficient_count
        )
        if counted != self.returned_evidence_count:
            raise ValueError("Polarity counts must equal the returned evidence count.")
        return self


class AgentClaim(_StrictModel):
    claim_type: ClaimType
    product_aliases: tuple[str, ...] = Field(min_length=1, max_length=5)
    aspect: Aspect
    polarity: Polarity
    evidence_strength: EvidenceStrength
    safe_evidence_card_refs: tuple[str, ...] = Field(default=(), max_length=5)

    @model_validator(mode="after")
    def validate_aliases(self) -> AgentClaim:
        if any(not _is_alias(value, "PRODUCT") for value in self.product_aliases):
            raise ValueError("Claims require run-scoped PRODUCT aliases.")
        if any(not _is_alias(value, "CARD") for value in self.safe_evidence_card_refs):
            raise ValueError("Claims require run-scoped CARD aliases.")
        return self


class AgentDraft(_StrictModel):
    workflow: Workflow
    selected_product_aliases: tuple[str, ...] = Field(min_length=1, max_length=5)
    selected_aspects: tuple[Aspect, ...] = Field(min_length=1, max_length=8)
    claims: tuple[AgentClaim, ...] = Field(default=(), max_length=40)
    follow_up_action_codes: tuple[FollowUpActionCode, ...] = Field(default=(), max_length=3)
    limitation_codes: tuple[LimitationCode, ...] = Field(default=(), max_length=5)


class VerificationResult(_StrictModel):
    passed: bool
    error_code: ErrorCode | None = None
    error_details: tuple[ValidationIssueCode, ...] = ()

    @model_validator(mode="after")
    def validate_outcome(self) -> VerificationResult:
        if self.passed and (self.error_code is not None or self.error_details):
            raise ValueError("Passing verification cannot contain errors.")
        if not self.passed and self.error_code is None:
            raise ValueError("Failed verification requires an error code.")
        return self


class AgentResponse(_StrictModel):
    """Local user response; never serialize this object to an external model or public trace."""

    run_id: str = Field(min_length=1, max_length=100)
    status: AgentStatus
    workflow: Workflow | None = None
    rendered_text: str = Field(max_length=20_000)
    error_code: ErrorCode | None = None


class TraceMetrics(_StrictModel):
    model_call_count: int = Field(ge=0, le=3)
    clarification_count: int = Field(ge=0, le=1)
    tool_execution_count: int = Field(ge=0, le=1)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    latency_ms: float = Field(ge=0)


def _max_int(left: int, right: int) -> int:
    """LangGraph reducer that prevents stale resume input from lowering counters."""

    return max(left, right)


class AgentState(TypedDict, total=False):
    request: dict[str, Any]
    run_id: str
    thread_id: str
    status: str
    sanitized_question: str
    workflow: str
    requested_aspects: list[str]
    plan: dict[str, Any]
    authorized_plan: dict[str, Any]
    unresolved_product_terms: list[str]
    product_candidates: list[dict[str, Any]]
    resume_selection: list[str]
    product_aliases: dict[str, str]
    local_product_map: dict[str, str]
    clarification_count: Annotated[int, _max_int]
    clarification_request: dict[str, Any]
    repair_count: Annotated[int, _max_int]
    tool_execution_count: Annotated[int, _max_int]
    tool_name: str
    tool_arguments: dict[str, Any]
    tool_result: dict[str, Any]
    safe_evidence_cards: list[dict[str, Any]]
    local_evidence_items: list[dict[str, Any]]
    draft: dict[str, Any]
    verification: dict[str, Any]
    model_call_count: Annotated[int, _max_int]
    model_http_attempt_count: Annotated[int, _max_int]
    input_tokens: Annotated[int, _max_int]
    output_tokens: Annotated[int, _max_int]
    error_code: str
    trace_metrics: dict[str, Any]
    response: dict[str, Any]
    graph_path: list[str]
    node_outcomes: dict[str, str]
    terminal: bool
    trace_written: bool


def assert_json_serializable_state(state: AgentState | dict[str, Any]) -> None:
    """Fail before checkpointing when graph state contains a runtime object."""

    try:
        json.dumps(state, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise TypeError("Agent state must contain only JSON-serializable values.") from error


def _is_alias(value: str, prefix: str) -> bool:
    head, separator, suffix = value.partition("_")
    return head == prefix and separator == "_" and suffix.isdigit() and int(suffix) > 0
