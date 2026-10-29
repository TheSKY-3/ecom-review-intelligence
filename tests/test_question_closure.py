import pytest

from ecom_review_intelligence.agent_tools import LocalEvidenceItem
from ecom_review_intelligence.engine import Evidence
from ecom_review_intelligence.question_closure import (
    BlockerCode,
    CaveatCode,
    LabelAction,
    LabelRejected,
    MeasurementRow,
    NextActionCode,
    QuestionState,
    apply_label,
    assess_question,
    lineage_is_stale,
    question_key,
    rows_from_evidence_items,
)

AT = "2026-10-29T04:00:00+00:00"


def _row(
    product_id: str = "B001",
    aspect: str = "noise",
    strength: str = "STRONG",
    supporting: int = 0,
    opposing: int = 0,
    conflicting: int = 0,
    insufficient: int = 0,
    matched_count: int | None = None,
) -> MeasurementRow:
    def ids(prefix: str, count: int) -> tuple[str, ...]:
        return tuple(f"{prefix}-{index}" for index in range(count))

    return MeasurementRow(
        product_id=product_id,
        aspect=aspect,
        strength=strength,
        supporting_ids=ids("s", supporting),
        opposing_ids=ids("o", opposing),
        conflicting_ids=ids("c", conflicting),
        insufficient_ids=ids("i", insufficient),
        matched_count=matched_count,
    )


def _evidence(identifier: str, polarity: str, product: str = "B001") -> Evidence:
    return Evidence(
        evidence_id=identifier,
        source_line=1,
        parent_asin=product,
        text="untrusted review text",
        rating=3.0,
        timestamp=1,
        verified_purchase=True,
        helpful_vote=0,
        polarity=polarity,
    )


def test_no_rows_means_nothing_matched_and_can_be_marked_unresolvable() -> None:
    assessment = assess_question(())

    assert assessment.state is QuestionState.OPEN
    assert assessment.blockers == (BlockerCode.NO_MATCH,)
    assert assessment.can_mark_unresolvable is True
    assert assessment.is_conclusive is False
    assert NextActionCode.WIDEN_QUERY in [action.code for action in assessment.actions]


def test_rows_without_evidence_are_the_same_no_match_case() -> None:
    assessment = assess_question((_row(),))

    assert assessment.blockers == (BlockerCode.NO_MATCH,)
    assert assessment.can_mark_unresolvable is True


def test_stale_lineage_voids_the_conclusion_before_anything_else() -> None:
    assessment = assess_question((_row(conflicting=2),), stale=True)

    assert assessment.state is QuestionState.OPEN
    assert assessment.blockers == (BlockerCode.STALE_EVIDENCE,)
    assert assessment.can_mark_unresolvable is False
    assert [action.code for action in assessment.actions] == [NextActionCode.RECHECK_RUN]


def test_unread_conflicts_block_the_question_with_the_exact_ids() -> None:
    assessment = assess_question((_row(conflicting=2),), read_evidence_ids=frozenset({"c-0"}))

    assert assessment.state is QuestionState.OPEN
    assert assessment.blockers == (BlockerCode.CONFLICT_UNREAD,)
    read = next(action for action in assessment.actions if action.code is NextActionCode.READ_CONFLICTS)
    assert read.evidence_ids == ("c-1",)
    assert "1 条" in read.label
    assert assessment.unread_conflicting_ids == ("c-1",)


def test_reading_every_conflict_advances_to_measured() -> None:
    assessment = assess_question(
        (_row(conflicting=2),), read_evidence_ids=frozenset({"c-0", "c-1"})
    )

    assert assessment.state is QuestionState.MEASURED
    assert assessment.blockers == (BlockerCode.NOT_VERIFIED,)
    assert assessment.is_conclusive is True
    assert assessment.ready_to_close is False
    assert NextActionCode.CONFIRM_CARDS in [action.code for action in assessment.actions]


def test_verified_aspects_are_the_only_way_to_reach_verified() -> None:
    verified = assess_question(
        (_row(conflicting=2),),
        read_evidence_ids=frozenset({"c-0", "c-1"}),
        verified_aspects=frozenset({"noise"}),
    )

    assert verified.state is QuestionState.VERIFIED
    assert verified.blockers == ()
    assert verified.ready_to_close is True
    assert NextActionCode.CLOSE_QUESTION in [action.code for action in verified.actions]


def test_partial_verification_still_blocks() -> None:
    assessment = assess_question(
        (
            _row(aspect="noise", supporting=1),
            _row(aspect="reliability", supporting=1),
        ),
        verified_aspects=frozenset({"noise"}),
    )

    assert assessment.state is QuestionState.MEASURED
    assert assessment.blockers == (BlockerCode.NOT_VERIFIED,)


def test_truncated_sample_and_thin_evidence_are_caveats_not_blockers() -> None:
    assessment = assess_question(
        (_row(strength="WEAK", supporting=5),),
        verified_aspects=frozenset({"noise"}),
        retrieval_limit=5,
    )

    assert assessment.state is QuestionState.VERIFIED
    assert assessment.caveats == (CaveatCode.SAMPLE_TRUNCATED, CaveatCode.THIN_EVIDENCE)


def test_a_strong_small_sample_carries_no_caveats() -> None:
    assessment = assess_question(
        (_row(strength="STRONG", supporting=3, opposing=1),),
        verified_aspects=frozenset({"noise"}),
    )

    assert assessment.state is QuestionState.VERIFIED
    assert assessment.caveats == ()


def test_an_exact_match_count_clears_the_truncation_caveat() -> None:
    assessment = assess_question(
        (_row(strength="STRONG", supporting=5, matched_count=5),),
        retrieval_limit=5,
        verified_aspects=frozenset({"noise"}),
    )

    assert assessment.caveats == ()
    assert assessment.rows[0].sample_is_complete is True


def test_an_exact_match_count_raises_the_caveat_below_the_limit() -> None:
    assessment = assess_question(
        (_row(strength="STRONG", supporting=2, matched_count=766),),
        retrieval_limit=5,
        verified_aspects=frozenset({"noise"}),
    )

    assert assessment.caveats == (CaveatCode.SAMPLE_TRUNCATED,)
    assert assessment.rows[0].sample_is_complete is False


def test_recommendations_never_exceed_three_and_are_drawn_from_the_blockers() -> None:
    assessment = assess_question((_row(conflicting=2),))

    assert 1 <= len(assessment.actions) <= 3
    assert all(action.basis for action in assessment.actions)
    assert all(action.label for action in assessment.actions)


def test_comparison_is_offered_only_for_a_single_product_scope() -> None:
    single = assess_question((_row(supporting=1),), verified_aspects=frozenset({"noise"}))
    multiple = assess_question(
        (_row(product_id="B001", supporting=1), _row(product_id="B002", supporting=1)),
        verified_aspects=frozenset({"noise"}),
    )

    assert NextActionCode.COMPARE_PRODUCTS in [action.code for action in single.actions]
    assert NextActionCode.COMPARE_PRODUCTS not in [action.code for action in multiple.actions]


def test_verify_is_refused_while_a_blocker_is_open() -> None:
    assessment = assess_question((_row(conflicting=2),))

    with pytest.raises(LabelRejected, match="open blockers"):
        apply_label(
            assessment,
            LabelAction.VERIFY,
            current_state=QuestionState.OPEN,
            question="噪音怎么样",
            actor="ops-a",
            recorded_at=AT,
        )


def test_close_is_refused_before_verification() -> None:
    assessment = assess_question((_row(conflicting=1),))

    with pytest.raises(LabelRejected, match="Only a verified question"):
        apply_label(
            assessment,
            LabelAction.CLOSE,
            current_state=QuestionState.VERIFIED,
            question="噪音怎么样",
            actor="ops-a",
            recorded_at=AT,
        )


def test_close_is_refused_from_a_state_that_cannot_close() -> None:
    assessment = assess_question((_row(),))

    with pytest.raises(LabelRejected, match="not allowed from OPEN"):
        apply_label(
            assessment,
            LabelAction.CLOSE,
            current_state=QuestionState.OPEN,
            question="噪音怎么样",
            actor="ops-a",
            recorded_at=AT,
        )


def test_closing_records_the_numbers_behind_the_decision() -> None:
    assessment = assess_question(
        (_row(conflicting=2, opposing=3),),
        read_evidence_ids=frozenset({"c-0", "c-1"}),
        verified_aspects=frozenset({"noise"}),
        retrieval_limit=10,
    )

    record = apply_label(
        assessment,
        LabelAction.CLOSE,
        current_state=QuestionState.VERIFIED,
        question="AGLUCKY 的噪音是不是问题",
        actor="ops-a",
        recorded_at=AT,
    )

    assert record["schema_version"] == "p7-question-state-v1"
    assert record["from_state"] == "VERIFIED"
    assert record["to_state"] == "CLOSED"
    assert record["actor"] == "ops-a"
    assert record["snapshot"]["evidence_count"] == 5
    assert record["snapshot"]["max_negative_or_conflicting"] == 5
    assert record["caveats"] == []


def test_unresolvable_is_only_offered_when_nothing_matched() -> None:
    nothing = assess_question(())
    something = assess_question((_row(supporting=1),))

    assert apply_label(
        nothing,
        LabelAction.MARK_UNRESOLVABLE,
        current_state=QuestionState.OPEN,
        question="尺寸",
        actor="ops-a",
        recorded_at=AT,
    )["to_state"] == "UNRESOLVABLE"
    with pytest.raises(LabelRejected, match="not unresolvable"):
        apply_label(
            something,
            LabelAction.MARK_UNRESOLVABLE,
            current_state=QuestionState.OPEN,
            question="噪音",
            actor="ops-a",
            recorded_at=AT,
        )


def test_reopen_returns_a_closed_question_to_open() -> None:
    assessment = assess_question((_row(),))

    record = apply_label(
        assessment,
        LabelAction.REOPEN,
        current_state=QuestionState.CLOSED,
        question="噪音怎么样",
        actor="ops-b",
        recorded_at=AT,
    )

    assert record["to_state"] == "OPEN"
    assert record["action"] == "REOPEN"


def test_question_key_is_order_independent() -> None:
    first = question_key(
        workflow="comparison", product_ids=("B002", "B001"), aspects=("noise", "reliability")
    )
    second = question_key(
        workflow="comparison", product_ids=("B001", "B002"), aspects=("reliability", "noise")
    )

    assert first == second == "comparison|B001,B002|noise,reliability"


def test_lineage_is_stale_when_either_half_moves() -> None:
    assert (
        lineage_is_stale(
            expected_snapshot_id="a",
            expected_contract_sha256="b",
            actual_snapshot_id="a",
            actual_contract_sha256="b",
        )
        is False
    )
    assert (
        lineage_is_stale(
            expected_snapshot_id="a",
            expected_contract_sha256="b",
            actual_snapshot_id="c",
            actual_contract_sha256="b",
        )
        is True
    )
    assert (
        lineage_is_stale(
            expected_snapshot_id="a",
            expected_contract_sha256="b",
            actual_snapshot_id="a",
            actual_contract_sha256=None,
        )
        is True
    )


def test_rows_from_evidence_items_bucket_by_polarity() -> None:
    item = LocalEvidenceItem(
        product_id="B001",
        aspect="noise",
        strength="STRONG",
        evidence=(
            _evidence("e1", "supporting"),
            _evidence("e2", "opposing"),
            _evidence("e3", "conflicting"),
            _evidence("e4", "insufficient"),
            _evidence("e5", "conflicting"),
        ),
    )

    (row,) = rows_from_evidence_items((item,))

    assert row.supporting_ids == ("e1",)
    assert row.opposing_ids == ("e2",)
    assert row.conflicting_ids == ("e3", "e5")
    assert row.insufficient_ids == ("e4",)
    assert row.evidence_count == 5
    assert row.negative_or_conflicting == 3
    assert row.matched_count is None


def test_rows_can_carry_the_exact_match_count() -> None:
    item = LocalEvidenceItem(
        product_id="B001",
        aspect="noise",
        strength="STRONG",
        evidence=(_evidence("e1", "supporting"),),
    )

    (row,) = rows_from_evidence_items((item,), matched_counts={("B001", "noise"): 766})

    assert row.matched_count == 766
    assert row.sample_is_complete is False


def test_assessment_summary_is_json_ready() -> None:
    assessment = assess_question((_row(conflicting=1),))

    summary = assessment.summary()

    assert summary["state"] == "OPEN"
    assert summary["blockers"] == ["CONFLICT_UNREAD"]
    assert summary["measured"]["unread_conflicting_count"] == 1
    assert summary["measured"]["rows"][0]["aspect"] == "noise"
    assert summary["actions"][0]["code"] == "READ_CONFLICTS"


def test_retrieval_limit_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        assess_question((_row(),), retrieval_limit=0)
