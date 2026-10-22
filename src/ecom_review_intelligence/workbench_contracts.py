"""Closed public DTOs for the P7 operations workbench."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ecom_review_intelligence.agent_contracts import Aspect, Workflow


class _PublicModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class WorkbenchRunRequest(_PublicModel):
    workflow: Workflow
    product_codes: tuple[str, ...] = Field(default=(), max_length=5)
    product_query: str | None = Field(default=None, min_length=2, max_length=80)
    aspect: Aspect
    question: str = Field(min_length=3, max_length=500)

    @model_validator(mode="after")
    def validate_product_shape(self) -> WorkbenchRunRequest:
        count = len(set(self.product_codes))
        if count != len(self.product_codes):
            raise ValueError("Product choices must be unique.")
        if not self.product_codes and self.product_query:
            return self
        if self.workflow is Workflow.DIAGNOSIS and count != 1:
            raise ValueError("Diagnosis requires one product.")
        if self.workflow is Workflow.COMPARISON and count != 2:
            raise ValueError("The synthetic comparison requires two products.")
        if self.workflow is Workflow.TRIAGE and not 1 <= count <= 5:
            raise ValueError("Triage requires one to five products.")
        return self


class WorkbenchResumeRequest(_PublicModel):
    selected_product_codes: tuple[str, ...] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def validate_unique_products(self) -> WorkbenchResumeRequest:
        if len(set(self.selected_product_codes)) != len(self.selected_product_codes):
            raise ValueError("Product choices must be unique.")
        return self


class ProductView(_PublicModel):
    code: str
    label: str
    segment: str


class EvidenceView(_PublicModel):
    evidence_alias: str
    product_code: str
    aspect: Aspect
    polarity: Literal["supporting", "opposing", "conflicting"]
    strength: Literal["WEAK", "MODERATE", "STRONG"]
    excerpt: str


class ComparisonRow(_PublicModel):
    product_code: str
    signal: str
    direction: Literal["advantage", "watch", "mixed", "insufficient"]
    strength: Literal["INSUFFICIENT", "WEAK", "MODERATE", "STRONG"]
    matched_count: int = Field(ge=0)
    evidence_count: int = Field(ge=0)
    comparable: bool
    insufficient_reason: str | None = None


class ProductSignalSummary(_PublicModel):
    product_code: str
    matched_count: int = Field(ge=0)
    evidence_count: int = Field(ge=0)
    signal_totals: dict[str, int] = Field(default_factory=dict)


class TriageItem(_PublicModel):
    rank: int = Field(ge=1, le=10)
    issue: str
    priority: Literal["high", "medium", "monitor"]
    owner: Literal["Product", "Quality", "Customer support"]
    evidence_count: int = Field(ge=0)


class ClarificationView(_PublicModel):
    prompt: str
    choices: tuple[ProductView, ...] = Field(min_length=2, max_length=5)


class LexiconBlindSpotView(_PublicModel):
    """Aggregate counts only; it never carries review text or identifiers."""

    matched_count: int = Field(ge=0)
    insufficient_count: int = Field(ge=0)
    insufficient_low_rating_count: int = Field(ge=0)
    note: str
    model_direction_count: int | None = Field(default=None, ge=0)
    model_off_topic_count: int | None = Field(default=None, ge=0)
    model_unclear_count: int | None = Field(default=None, ge=0)
    model_labeler: str | None = Field(default=None, max_length=80)


class EvidenceRowView(_PublicModel):
    evidence_ref: str
    locator_line: int = Field(ge=1)
    rating: float | None = None
    polarity: Literal["supporting", "opposing", "conflicting", "insufficient"]
    helpful_votes: int | None = None
    text: str = Field(min_length=1, max_length=600)


class EvidenceDetail(_PublicModel):
    schema_version: Literal["p7-evidence-detail-v1"] = "p7-evidence-detail-v1"
    run_token: str
    card_alias: str
    product_code: str
    aspect: Aspect
    rows: tuple[EvidenceRowView, ...] = ()


class InsightRequest(_PublicModel):
    run_token: str = Field(min_length=1, max_length=64)
    card_alias: str = Field(min_length=1, max_length=16)


Verdict = Literal["confirmed", "doubtful", "rejected"]


class VerdictRequest(_PublicModel):
    """One human check on one evidence card. It carries no review text."""

    run_token: str = Field(min_length=1, max_length=64)
    card_alias: str = Field(min_length=1, max_length=16)
    verdict: Verdict


class VerdictRecord(_PublicModel):
    """Durable record of one human decision, self-describing so it exports alone."""

    schema_version: Literal["p7-verdict-v1"] = "p7-verdict-v1"
    run_token: str
    run_alias: str
    card_alias: str
    verdict: Verdict
    recorded_at: str = Field(min_length=10, max_length=40)
    workflow: Workflow
    aspect: Aspect
    product_labels: tuple[str, ...] = ()
    polarity: str | None = None
    strength: str | None = None
    matched_count: int = Field(default=0, ge=0)
    evidence_count: int = Field(default=0, ge=0)


class VerdictList(_PublicModel):
    schema_version: Literal["p7-verdict-list-v1"] = "p7-verdict-list-v1"
    run_token: str
    records: tuple[VerdictRecord, ...] = ()


class InsightPoint(_PublicModel):
    text: str
    evidence_aliases: tuple[str, ...]
    quote: str | None = None


class InsightRejected(_PublicModel):
    evidence_alias: str
    quote: str


class InsightView(_PublicModel):
    """Public result of one opt-in AI reading step; it carries no decision fields."""

    schema_version: Literal["p7-insight-v1"] = "p7-insight-v1"
    run_token: str
    card_alias: str
    status: Literal["READY", "UNAVAILABLE", "VALIDATION_FAILED"]
    reason: str | None = None
    supported: str = ""
    supported_aliases: tuple[str, ...] = ()
    specifics: tuple[InsightPoint, ...] = ()
    rejected: tuple[InsightRejected, ...] = ()
    not_supported: str = ""
    review_count: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)


class WorkbenchRun(_PublicModel):
    schema_version: Literal["p7-workbench-run-v1"] = "p7-workbench-run-v1"
    run_token: str
    run_alias: str
    status: Literal[
        "VERIFIED_RESULT",
        "DEGRADED_SAFE_RESULT",
        "CLARIFICATION_REQUIRED",
        "PROVIDER_ERROR",
    ]
    workflow: Workflow
    title: str
    summary: str
    evidence_count: int = Field(ge=0)
    matched_count: int = Field(default=0, ge=0)
    signal_totals: dict[str, int] = Field(default_factory=dict)
    product_summaries: tuple[ProductSignalSummary, ...] = ()
    degraded: bool
    next_action: str
    products: tuple[ProductView, ...] = ()
    evidence: tuple[EvidenceView, ...] = ()
    comparison_rows: tuple[ComparisonRow, ...] = ()
    triage_items: tuple[TriageItem, ...] = ()
    clarification: ClarificationView | None = None
    lexicon_blind_spot: LexiconBlindSpotView | None = None
