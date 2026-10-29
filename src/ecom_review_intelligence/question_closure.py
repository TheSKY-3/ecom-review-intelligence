"""When is an operator question actually finished?

The workbench can answer a question, but until now nothing defined what "this
question is done" means. Without that definition a queue of questions cannot be
worked, handed over, or audited: every item looks equally unfinished forever.

This module answers the question with a closed set of measured conditions and
keeps the answer honest about who is allowed to decide what:

* The **machine** decides whether the evidence is conclusive: something matched,
  the conclusion is not void, every conflict has been read, and the lineage
  still matches the frozen store.
* The **operator** decides whether they accept it. That is a recorded act, not a
  computed state, so :func:`assess_question` never returns a finished question --
  it returns the furthest state the evidence can justify.
* "This cannot be answered" is a first-class outcome with a mandatory reason
  code. Otherwise unsolvable questions sit in the queue forever.

Two lists are kept apart on purpose:

* **blockers** stop the question advancing to the next state;
* **caveats** travel with a finished answer (truncated sample, thin evidence).

Conflating them would either block honest answers or hide real limitations.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ecom_review_intelligence.agent_tools import LocalEvidenceItem

STATE_SCHEMA_VERSION = "p7-question-state-v1"
MAX_NEXT_ACTIONS = 3

STRENGTH_RANK = {"INSUFFICIENT": 0, "WEAK": 1, "MODERATE": 2, "STRONG": 3}


class QuestionState(str, Enum):
    """Progress states. CLOSED and UNRESOLVABLE are only reachable by an act."""

    OPEN = "OPEN"
    MEASURED = "MEASURED"
    VERIFIED = "VERIFIED"
    CLOSED = "CLOSED"
    UNRESOLVABLE = "UNRESOLVABLE"


class BlockerCode(str, Enum):
    """Why the question cannot move to the next state."""

    NO_MATCH = "NO_MATCH"
    STALE_EVIDENCE = "STALE_EVIDENCE"
    CONFLICT_UNREAD = "CONFLICT_UNREAD"
    NOT_VERIFIED = "NOT_VERIFIED"


class CaveatCode(str, Enum):
    """A limitation carried by an answer that is still allowed to conclude."""

    SAMPLE_TRUNCATED = "SAMPLE_TRUNCATED"
    THIN_EVIDENCE = "THIN_EVIDENCE"


class NextActionCode(str, Enum):
    """The closed set of things an operator may be told to do next."""

    READ_CONFLICTS = "READ_CONFLICTS"
    CONFIRM_CARDS = "CONFIRM_CARDS"
    COMPARE_PRODUCTS = "COMPARE_PRODUCTS"
    WIDEN_QUERY = "WIDEN_QUERY"
    RECHECK_RUN = "RECHECK_RUN"
    CLOSE_QUESTION = "CLOSE_QUESTION"


class LabelAction(str, Enum):
    """Operator acts that move a question between states."""

    VERIFY = "VERIFY"
    CLOSE = "CLOSE"
    REOPEN = "REOPEN"
    MARK_UNRESOLVABLE = "MARK_UNRESOLVABLE"


@dataclass(frozen=True)
class MeasurementRow:
    """One product-and-aspect measurement, kept with per-polarity evidence IDs.

    The artifact-facing sibling ``agent_scenarios.StepMeasurement`` keeps counts
    only, because replay must stay compact. Judging needs to know *which* IDs are
    conflicting, so this type buckets the IDs and derives the counts from them.
    """

    product_id: str
    aspect: str
    strength: str
    supporting_ids: tuple[str, ...] = ()
    opposing_ids: tuple[str, ...] = ()
    conflicting_ids: tuple[str, ...] = ()
    insufficient_ids: tuple[str, ...] = ()
    matched_count: int | None = None

    @property
    def sample_is_complete(self) -> bool:
        """True only when we positively know every matching review was read."""

        return self.matched_count is not None and self.matched_count <= self.evidence_count

    @property
    def evidence_count(self) -> int:
        return (
            len(self.supporting_ids)
            + len(self.opposing_ids)
            + len(self.conflicting_ids)
            + len(self.insufficient_ids)
        )

    @property
    def negative_or_conflicting(self) -> int:
        return len(self.opposing_ids) + len(self.conflicting_ids)

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        return (
            self.supporting_ids + self.opposing_ids + self.conflicting_ids + self.insufficient_ids
        )

    def as_document(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "aspect": self.aspect,
            "strength": self.strength,
            "evidence_count": self.evidence_count,
            "matched_count": self.matched_count,
            "sample_is_complete": self.sample_is_complete,
            "supporting": len(self.supporting_ids),
            "opposing": len(self.opposing_ids),
            "conflicting": len(self.conflicting_ids),
            "insufficient": len(self.insufficient_ids),
            "negative_or_conflicting": self.negative_or_conflicting,
        }


def rows_from_evidence_items(
    items: Iterable[LocalEvidenceItem],
    *,
    matched_counts: dict[tuple[str, str], int] | None = None,
) -> tuple[MeasurementRow, ...]:
    """Adapt the tool layer's items into judging rows.

    ``matched_counts`` maps ``(product_id, aspect)`` to the total number of
    matching reviews in the store, so a row can say whether its sample was the
    whole population or only the first few of it.
    """

    rows: list[MeasurementRow] = []
    for item in items:
        buckets: dict[str, list[str]] = {
            "supporting": [],
            "opposing": [],
            "conflicting": [],
            "insufficient": [],
        }
        for evidence in item.evidence:
            if evidence.polarity in buckets:
                buckets[evidence.polarity].append(evidence.evidence_id)
        rows.append(
            MeasurementRow(
                product_id=item.product_id,
                aspect=item.aspect,
                strength=item.strength,
                supporting_ids=tuple(buckets["supporting"]),
                opposing_ids=tuple(buckets["opposing"]),
                conflicting_ids=tuple(buckets["conflicting"]),
                insufficient_ids=tuple(buckets["insufficient"]),
                matched_count=(matched_counts or {}).get((item.product_id, item.aspect)),
            )
        )
    return tuple(rows)


def lineage_is_stale(
    *,
    expected_snapshot_id: str,
    expected_contract_sha256: str,
    actual_snapshot_id: str | None,
    actual_contract_sha256: str | None,
) -> bool:
    """A conclusion is void once the frozen store it was measured against moved."""

    return (
        actual_snapshot_id != expected_snapshot_id
        or actual_contract_sha256 != expected_contract_sha256
    )


def question_key(
    *, workflow: str, product_ids: Iterable[str], aspects: Iterable[str]
) -> str:
    """Stable identity for one question, independent of asking order."""

    products = ",".join(sorted(product_ids))
    aspect_list = ",".join(sorted(aspects))
    return f"{workflow}|{products}|{aspect_list}"


@dataclass(frozen=True)
class NextAction:
    """A concrete next step a person can click, with the numbers behind it."""

    code: NextActionCode
    label: str
    basis: str
    products: tuple[str, ...] = ()
    aspects: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()

    def as_document(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "label": self.label,
            "basis": self.basis,
            "products": list(self.products),
            "aspects": list(self.aspects),
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True)
class ClosureAssessment:
    """The furthest state the evidence justifies, plus what is missing."""

    state: QuestionState
    blockers: tuple[BlockerCode, ...]
    caveats: tuple[CaveatCode, ...]
    rows: tuple[MeasurementRow, ...]
    actions: tuple[NextAction, ...]
    can_mark_unresolvable: bool
    unread_conflicting_ids: tuple[str, ...] = ()

    @property
    def is_conclusive(self) -> bool:
        return self.state in {QuestionState.MEASURED, QuestionState.VERIFIED}

    @property
    def ready_to_close(self) -> bool:
        return self.state is QuestionState.VERIFIED

    def summary(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "blockers": [code.value for code in self.blockers],
            "caveats": [code.value for code in self.caveats],
            "can_mark_unresolvable": self.can_mark_unresolvable,
            "measured": {
                "row_count": len(self.rows),
                "evidence_count": sum(row.evidence_count for row in self.rows),
                "max_negative_or_conflicting": max(
                    (row.negative_or_conflicting for row in self.rows), default=0
                ),
                "unread_conflicting_count": len(self.unread_conflicting_ids),
                "unread_conflicting_ids": list(self.unread_conflicting_ids),
                "rows": [row.as_document() for row in self.rows],
            },
            "actions": [action.as_document() for action in self.actions],
        }


def _unread_conflicting_ids(
    rows: tuple[MeasurementRow, ...], read_evidence_ids: frozenset[str]
) -> tuple[str, ...]:
    return tuple(
        identifier
        for row in rows
        for identifier in row.conflicting_ids
        if identifier not in read_evidence_ids
    )


def _caveats(rows: tuple[MeasurementRow, ...], retrieval_limit: int) -> tuple[CaveatCode, ...]:
    """Limitations carried by an answer that is still allowed to conclude.

    Truncation is decided from the exact match count when the caller supplied
    one. Without it we only know the sample was full, which *may* mean more
    existed, so the caveat is raised rather than hidden -- a guess in the
    conservative direction.
    """

    caveats: list[CaveatCode] = []
    truncated = any(
        (
            row.matched_count > row.evidence_count
            if row.matched_count is not None
            else row.evidence_count >= retrieval_limit
        )
        for row in rows
    )
    if truncated:
        caveats.append(CaveatCode.SAMPLE_TRUNCATED)
    if rows and max(STRENGTH_RANK.get(row.strength, 0) for row in rows) <= STRENGTH_RANK["WEAK"]:
        caveats.append(CaveatCode.THIN_EVIDENCE)
    return tuple(caveats)


def _actions(
    *,
    state: QuestionState,
    blockers: tuple[BlockerCode, ...],
    rows: tuple[MeasurementRow, ...],
    unread_conflicts: tuple[str, ...],
    product_count: int,
    has_match: bool,
) -> tuple[NextAction, ...]:
    """Recommendations, in priority order, capped at MAX_NEXT_ACTIONS.

    Every action is derived from a blocker or from the shape of the scope; none
    of them is invented, so each one can be executed as written.
    """

    products = tuple(row.product_id for row in rows)
    aspects = tuple(sorted({row.aspect for row in rows}))
    actions: list[NextAction] = []

    if BlockerCode.NO_MATCH in blockers:
        actions.append(
            NextAction(
                code=NextActionCode.WIDEN_QUERY,
                label="换一个更宽的维度或商品再查一次",
                basis="这次在这个维度上没有命中任何相关评论。",
                products=products,
                aspects=aspects,
            )
        )
    if BlockerCode.STALE_EVIDENCE in blockers:
        actions.append(
            NextAction(
                code=NextActionCode.RECHECK_RUN,
                label="重跑一次取证再下结论",
                basis="证据库的版本和这轮结论对不上了，旧结论已作废。",
                products=products,
                aspects=aspects,
            )
        )
    if BlockerCode.CONFLICT_UNREAD in blockers:
        actions.append(
            NextAction(
                code=NextActionCode.READ_CONFLICTS,
                label=f"先读这 {len(unread_conflicts)} 条冲突原文",
                basis=f"有 {len(unread_conflicts)} 条评论正反两面都在说，没读原文就判不了。",
                products=products,
                aspects=aspects,
                evidence_ids=unread_conflicts[:5],
            )
        )
    if BlockerCode.NOT_VERIFIED in blockers:
        actions.append(
            NextAction(
                code=NextActionCode.CONFIRM_CARDS,
                label=f"确认这 {len(aspects)} 个维度的证据卡",
                basis="证据已经够下结论了，但还没有人确认过卡片。",
                products=products,
                aspects=aspects,
            )
        )
    if state is QuestionState.VERIFIED:
        actions.append(
            NextAction(
                code=NextActionCode.CLOSE_QUESTION,
                label="这个问题可以收尾了",
                basis="证据已查清，卡片也确认过了。",
                products=products,
                aspects=aspects,
            )
        )
    if has_match and product_count == 1:
        # A comparison is worth offering only once the current evidence stands:
        # pointing at more work while the current conclusion is void is noise.
        void = {BlockerCode.NO_MATCH, BlockerCode.STALE_EVIDENCE}.intersection(blockers)
        if not void:
            actions.append(
                NextAction(
                    code=NextActionCode.COMPARE_PRODUCTS,
                    label="和另外两个受控商品横向比一下",
                    basis="目前只看了单个商品，横向比能看出这是个别问题还是通病。",
                    products=products,
                    aspects=aspects,
                )
            )
    return tuple(actions[:MAX_NEXT_ACTIONS])


def assess_question(
    rows: tuple[MeasurementRow, ...],
    *,
    read_evidence_ids: frozenset[str] = frozenset(),
    verified_aspects: frozenset[str] = frozenset(),
    stale: bool = False,
    retrieval_limit: int = 5,
) -> ClosureAssessment:
    """Judge how far a question has actually progressed.

    ``read_evidence_ids`` is what the operator has opened; ``verified_aspects``
    is what they have confirmed. Neither is inferred, so a question can never
    look closer to done than the record says it is.
    """

    if retrieval_limit <= 0:
        raise ValueError("Retrieval limit must be positive.")
    if not rows:
        return ClosureAssessment(
            state=QuestionState.OPEN,
            blockers=(BlockerCode.NO_MATCH,),
            caveats=(),
            rows=(),
            actions=_actions(
                state=QuestionState.OPEN,
                blockers=(BlockerCode.NO_MATCH,),
                rows=(),
                unread_conflicts=(),
                product_count=0,
                has_match=False,
            ),
            can_mark_unresolvable=True,
        )

    caveats = _caveats(rows, retrieval_limit)
    has_match = any(row.evidence_count > 0 for row in rows)
    product_count = len({row.product_id for row in rows})

    if not has_match:
        blockers: tuple[BlockerCode, ...] = (BlockerCode.NO_MATCH,)
        state = QuestionState.OPEN
        unread_conflicts: tuple[str, ...] = ()
    elif stale:
        blockers = (BlockerCode.STALE_EVIDENCE,)
        state = QuestionState.OPEN
        unread_conflicts = ()
    else:
        unread_conflicts = _unread_conflicting_ids(rows, read_evidence_ids)
        if unread_conflicts:
            blockers = (BlockerCode.CONFLICT_UNREAD,)
            state = QuestionState.OPEN
        else:
            measured_aspects = {row.aspect for row in rows if row.evidence_count > 0}
            if measured_aspects - verified_aspects:
                blockers = (BlockerCode.NOT_VERIFIED,)
                state = QuestionState.MEASURED
            else:
                blockers = ()
                state = QuestionState.VERIFIED

    return ClosureAssessment(
        state=state,
        blockers=blockers,
        caveats=caveats,
        rows=rows,
        actions=_actions(
            state=state,
            blockers=blockers,
            rows=rows,
            unread_conflicts=unread_conflicts,
            product_count=product_count,
            has_match=has_match,
        ),
        can_mark_unresolvable=(state is QuestionState.OPEN and BlockerCode.NO_MATCH in blockers),
        unread_conflicting_ids=unread_conflicts,
    )


_ACTION_RULES: dict[LabelAction, tuple[frozenset[QuestionState], QuestionState]] = {
    LabelAction.VERIFY: (
        frozenset({QuestionState.OPEN, QuestionState.MEASURED}),
        QuestionState.VERIFIED,
    ),
    LabelAction.CLOSE: (frozenset({QuestionState.VERIFIED}), QuestionState.CLOSED),
    LabelAction.REOPEN: (
        frozenset({QuestionState.MEASURED, QuestionState.VERIFIED, QuestionState.CLOSED}),
        QuestionState.OPEN,
    ),
    LabelAction.MARK_UNRESOLVABLE: (frozenset({QuestionState.OPEN}), QuestionState.UNRESOLVABLE),
}


class LabelRejected(ValueError):
    """Raised when an operator act is not allowed from the current state."""


def apply_label(
    assessment: ClosureAssessment,
    action: LabelAction,
    *,
    current_state: QuestionState,
    question: str,
    actor: str,
    recorded_at: str,
) -> dict[str, Any]:
    """Record one operator act, refusing anything the evidence does not support.

    The record embeds the numbers measured at that moment. A bare "closed" flag
    is worthless three months later; the counts are what make it auditable.
    """

    allowed, target = _ACTION_RULES[action]
    if current_state not in allowed:
        raise LabelRejected(
            f"{action.value} is not allowed from {current_state.value}."
        )
    if action is LabelAction.VERIFY and assessment.blockers:
        raise LabelRejected(
            "The question still has open blockers: "
            + ", ".join(code.value for code in assessment.blockers)
        )
    if action is LabelAction.CLOSE and not assessment.ready_to_close:
        raise LabelRejected("Only a verified question may be closed.")
    if action is LabelAction.MARK_UNRESOLVABLE and not assessment.can_mark_unresolvable:
        raise LabelRejected("This question is not unresolvable; something matched.")

    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "question": question,
        "action": action.value,
        "from_state": current_state.value,
        "to_state": target.value,
        "actor": actor,
        "recorded_at": recorded_at,
        "blockers": [code.value for code in assessment.blockers],
        "caveats": [code.value for code in assessment.caveats],
        "snapshot": assessment.summary()["measured"],
    }
