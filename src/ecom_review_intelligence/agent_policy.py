"""Local aliasing, evidence minimization, verification, and rendering for P6."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from ecom_review_intelligence.agent_contracts import (
    AgentDraft,
    AgentPlan,
    AgentResponse,
    ErrorCode,
    SafeEvidenceCard,
    ValidationIssueCode,
    VerificationResult,
    Workflow,
)
from ecom_review_intelligence.agent_tools import LocalEvidenceItem, WorkflowToolResult


class AgentPolicyError(RuntimeError):
    """Raised when local safety policy cannot produce an authorized response."""


@dataclass(frozen=True)
class ProductAliasMap:
    local_to_alias: dict[str, str]
    alias_to_local: dict[str, str]

    @classmethod
    def from_product_ids(cls, product_ids: tuple[str, ...]) -> ProductAliasMap:
        if not product_ids or len(set(product_ids)) != len(product_ids):
            raise AgentPolicyError("Product IDs must be non-empty and unique before aliasing.")
        local_to_alias = {
            product_id: f"PRODUCT_{index}"
            for index, product_id in enumerate(product_ids, start=1)
        }
        return cls(
            local_to_alias=local_to_alias,
            alias_to_local={alias: local for local, alias in local_to_alias.items()},
        )

    def alias(self, product_id: str) -> str:
        try:
            return self.local_to_alias[product_id]
        except KeyError as error:
            raise AgentPolicyError("Product has no run-scoped alias.") from error

    def local_id(self, alias: str) -> str:
        try:
            return self.alias_to_local[alias]
        except KeyError as error:
            raise AgentPolicyError("Alias has no local product mapping.") from error


@dataclass(frozen=True)
class SafeEvidenceBundle:
    cards: tuple[SafeEvidenceCard, ...]
    local_items: dict[str, LocalEvidenceItem]


def sanitize_question(question: str, aliases: ProductAliasMap) -> str:
    sanitized = question
    for product_id in sorted(aliases.local_to_alias, key=len, reverse=True):
        sanitized = sanitized.replace(product_id, aliases.alias(product_id))
    return sanitized


def build_safe_evidence(
    result: WorkflowToolResult, aliases: ProductAliasMap
) -> SafeEvidenceBundle:
    cards: list[SafeEvidenceCard] = []
    local_items: dict[str, LocalEvidenceItem] = {}
    for index, item in enumerate(result.items, start=1):
        card_alias = f"CARD_{index}"
        counts = Counter(evidence.polarity for evidence in item.evidence)
        limitations = ["SILVER_LABELS", "REVIEW_EVIDENCE_ONLY"]
        if item.strength in {"INSUFFICIENT", "WEAK"}:
            limitations.append("LIMITED_SAMPLE")
        if counts["conflicting"]:
            limitations.append("CONFLICTING_EVIDENCE")
        if not item.evidence:
            limitations.append("INSUFFICIENT_EVIDENCE")
        cards.append(
            SafeEvidenceCard(
                card_alias=card_alias,
                product_alias=aliases.alias(item.product_id),
                aspect=item.aspect,
                evidence_strength=item.strength,
                supporting_count=counts["supporting"],
                opposing_count=counts["opposing"],
                conflicting_count=counts["conflicting"],
                insufficient_count=counts["insufficient"],
                returned_evidence_count=len(item.evidence),
                quality_limitation_codes=tuple(limitations),
            )
        )
        local_items[card_alias] = item
    return SafeEvidenceBundle(cards=tuple(cards), local_items=local_items)


def verify_draft(
    draft: AgentDraft,
    plan: AgentPlan,
    cards: tuple[SafeEvidenceCard, ...],
) -> VerificationResult:
    issues: list[ValidationIssueCode] = []
    card_map = {card.card_alias: card for card in cards}
    covered_card_refs: list[str] = []
    if draft.workflow != plan.workflow:
        issues.append(ValidationIssueCode.WORKFLOW_RULE_VIOLATION)
    if set(draft.selected_product_aliases) != set(plan.product_terms):
        issues.append(ValidationIssueCode.WORKFLOW_RULE_VIOLATION)
    if set(draft.selected_aspects) != set(plan.aspects):
        issues.append(ValidationIssueCode.WORKFLOW_RULE_VIOLATION)
    if len(set(draft.selected_product_aliases)) != len(draft.selected_product_aliases):
        issues.append(ValidationIssueCode.WORKFLOW_RULE_VIOLATION)
    if len(set(draft.selected_aspects)) != len(draft.selected_aspects):
        issues.append(ValidationIssueCode.WORKFLOW_RULE_VIOLATION)
    if len(draft.claims) != len(cards):
        issues.append(ValidationIssueCode.WORKFLOW_RULE_VIOLATION)

    ordered_priority_refs: list[str] = []
    for claim in draft.claims:
        if claim.claim_type == "insufficient_evidence" and not claim.safe_evidence_card_refs:
            matches = [
                card.card_alias
                for card in cards
                if card.returned_evidence_count == 0
                and claim.product_aliases == (card.product_alias,)
                and card.aspect == claim.aspect
                and card.evidence_strength == claim.evidence_strength
                and claim.polarity == "insufficient"
            ]
            if len(matches) == 1:
                covered_card_refs.extend(matches)
            else:
                issues.append(ValidationIssueCode.VALUE_MISMATCH)
            continue
        if not claim.safe_evidence_card_refs:
            issues.append(ValidationIssueCode.UNKNOWN_CARD)
            continue
        if len(claim.safe_evidence_card_refs) != 1:
            issues.append(ValidationIssueCode.WORKFLOW_RULE_VIOLATION)
        for reference in claim.safe_evidence_card_refs:
            card = card_map.get(reference)
            if card is None:
                issues.append(ValidationIssueCode.UNKNOWN_CARD)
                continue
            covered_card_refs.append(reference)
            expected_claim_type = (
                "validation_priority"
                if plan.workflow is Workflow.TRIAGE
                else "aspect_summary"
            )
            if claim.claim_type != expected_claim_type or claim.product_aliases != (
                card.product_alias,
            ):
                issues.append(ValidationIssueCode.WORKFLOW_RULE_VIOLATION)
            if (
                card.aspect != claim.aspect
                or card.evidence_strength != claim.evidence_strength
                or not _polarity_is_supported(claim.polarity, card)
            ):
                issues.append(ValidationIssueCode.VALUE_MISMATCH)
            if claim.claim_type == "validation_priority":
                ordered_priority_refs.append(reference)

    expected_card_refs = [card.card_alias for card in cards]
    if Counter(covered_card_refs) != Counter(expected_card_refs):
        issues.append(ValidationIssueCode.WORKFLOW_RULE_VIOLATION)
    if plan.workflow is Workflow.COMPARISON and len(plan.product_terms) < 2:
        issues.append(ValidationIssueCode.WORKFLOW_RULE_VIOLATION)
    if plan.workflow is Workflow.TRIAGE and ordered_priority_refs != expected_card_refs:
        issues.append(ValidationIssueCode.WORKFLOW_RULE_VIOLATION)
    unique_issues = tuple(dict.fromkeys(issues))
    if unique_issues:
        return VerificationResult(
            passed=False,
            error_code=ErrorCode.VERIFICATION_FAILED,
            error_details=unique_issues,
        )
    return VerificationResult(passed=True)


def render_verified_response(
    *,
    run_id: str,
    draft: AgentDraft,
    verification: VerificationResult,
    bundle: SafeEvidenceBundle,
    citation_verifier: Callable[[str], bool],
) -> AgentResponse:
    if not verification.passed:
        raise AgentPolicyError("Unverified model output cannot be rendered.")
    referenced = [
        reference for claim in draft.claims for reference in claim.safe_evidence_card_refs
    ]
    lines = ["已通过本地结构化验证。", "评论证据仅支持核查顺序，不代表缺陷率或业务影响。"]
    lines.extend(_render_local_items(referenced, bundle, citation_verifier))
    lines.append("评论摘录仅作为惰性证据显示，不会作为模型或工具指令执行。")
    return AgentResponse(
        run_id=run_id,
        status="VERIFIED_RESULT",
        workflow=draft.workflow,
        rendered_text="\n".join(lines),
    )


def deterministic_fallback(
    *,
    run_id: str,
    workflow: str,
    bundle: SafeEvidenceBundle,
    error_code: str,
    citation_verifier: Callable[[str], bool],
) -> AgentResponse:
    lines = [
        "模型生成未完成；以下为本地确定性降级结果。",
        "评论证据仅支持核查顺序，不代表缺陷率或业务影响。",
    ]
    lines.extend(_render_local_items(list(bundle.local_items), bundle, citation_verifier))
    lines.append("评论摘录仅作为惰性证据显示，不会作为模型或工具指令执行。")
    return AgentResponse(
        run_id=run_id,
        status="DEGRADED_SAFE_RESULT",
        workflow=workflow,
        rendered_text="\n".join(lines),
        error_code=error_code,
    )


def _polarity_is_supported(polarity: str, card: SafeEvidenceCard) -> bool:
    key = getattr(polarity, "value", polarity)
    return {
        "supporting": card.supporting_count,
        "opposing": card.opposing_count,
        "conflicting": card.conflicting_count,
        "insufficient": card.insufficient_count,
    }[key] > 0


def _render_local_items(
    references: list[str],
    bundle: SafeEvidenceBundle,
    citation_verifier: Callable[[str], bool],
) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for reference in references:
        if reference in seen:
            continue
        seen.add(reference)
        item = bundle.local_items.get(reference)
        if item is None:
            raise AgentPolicyError("Referenced evidence card has no local mapping.")
        lines.append(
            f"[{reference}] 本地商品 {item.product_id} / {item.aspect} / {item.strength}"
        )
        for evidence in item.evidence:
            if not citation_verifier(evidence.evidence_id):
                raise AgentPolicyError("Citation failed local revalidation before rendering.")
            lines.append(f"- {evidence.evidence_id}: {evidence.text}")
    return lines
