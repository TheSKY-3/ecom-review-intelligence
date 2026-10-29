"""Unit tests for the constrained chat answer contract and its machine checks."""

from ecom_review_intelligence.agent_chat import (
    ChatAnswerDraft,
    ChatReviewPoint,
    ChatStatsPoint,
    build_card_payload,
    validate_chat_answer,
)

MATERIALS = {
    "C1-E1": "The noise from the compressor is very loud at night.",
    "C1-E2": "It is quiet enough in the kitchen.",
}

STATS_ROWS = {
    "signal_totals": [
        {"product": "商品 A", "sample_size": 42, "supporting": 1, "opposing": 3,
         "conflicting": 0, "insufficient": 38},
    ],
    "full_population_totals": [
        {"product": "商品 A", "matched_total": 766, "supporting": 286, "opposing": 166,
         "conflicting": 239, "insufficient": 75},
    ],
    "rating_distribution": [
        {"product": "商品 A", "r1": 10, "r2": 3, "r3": 5, "r4": 40, "r5": 100, "none": 0},
    ],
}


def _draft(**updates):
    value = {
        "supported": "样本里有买家抱怨夜间噪音。",
        "review_points": (
            ChatReviewPoint(
                text="有买家说晚上很吵",
                citation="C1-E1",
                quote="very loud at night",
            ),
        ),
        "stats_points": (),
        "not_supported": "样本只有 2 条,不能推断普遍程度。",
    }
    value.update(updates)
    return ChatAnswerDraft(**value)


def test_grounded_answer_passes_all_checks():
    assert validate_chat_answer(_draft(), MATERIALS, STATS_ROWS) == ()


def test_stats_point_that_matches_tool_result_passes():
    draft = _draft(
        review_points=(),
        stats_points=(
            ChatStatsPoint(
                text="该商品匹配 42 条,其中 38 条方向未判。",
                stat_key="signal_totals",
                product="商品 A",
                metric="insufficient",
                value=38,
            ),
        ),
    )

    assert validate_chat_answer(draft, MATERIALS, STATS_ROWS) == ()


def test_citation_that_was_never_read_is_rejected():
    draft = _draft(
        review_points=(
            ChatReviewPoint(text="有买家说很吵", citation="C2-E1", quote="loud"),
        ),
    )

    assert "UNKNOWN_CITATION" in validate_chat_answer(draft, MATERIALS, STATS_ROWS)


def test_quote_that_is_not_verbatim_is_rejected():
    draft = _draft(
        review_points=(
            ChatReviewPoint(
                text="有买家说晚上完全安静",
                citation="C1-E1",
                quote="completely silent at night",
            ),
        ),
    )

    assert "QUOTE_NOT_VERBATIM" in validate_chat_answer(draft, MATERIALS, STATS_ROWS)


def test_stats_value_that_differs_from_the_tool_result_is_rejected():
    draft = _draft(
        review_points=(),
        stats_points=(
            ChatStatsPoint(
                text="该商品匹配 999 条。",
                stat_key="signal_totals",
                product="商品 A",
                metric="sample_size",
                value=999,
            ),
        ),
    )

    assert "STATS_MISMATCH" in validate_chat_answer(draft, MATERIALS, STATS_ROWS)


def test_unknown_stat_reference_is_rejected():
    draft = _draft(
        review_points=(),
        stats_points=(
            ChatStatsPoint(
                text="评分分布里没有这一项。",
                stat_key="signal_totals",
                product="商品 A",
                metric="r1",
                value=10,
            ),
        ),
    )

    assert "UNKNOWN_STAT_REF" in validate_chat_answer(draft, MATERIALS, STATS_ROWS)


def test_answer_without_any_grounding_is_rejected():
    draft = _draft(review_points=(), stats_points=())

    assert "UNREFERENCED_ANSWER" in validate_chat_answer(draft, MATERIALS, STATS_ROWS)


def test_alias_inside_free_text_is_rejected():
    draft = _draft(supported="见 C1-E1 的说法。")

    assert "ALIAS_IN_TEXT" in validate_chat_answer(draft, MATERIALS, STATS_ROWS)


def test_summary_number_must_trace_to_a_tool_result():
    draft = _draft(supported="这批 900 条里有 77 条判不出方向。")

    assert "NUMBER_NOT_TRACEABLE" in validate_chat_answer(draft, MATERIALS, STATS_ROWS)


def test_summary_number_from_a_queried_row_is_allowed():
    draft = _draft(supported="匹配 42 条,其中 38 条方向未判。")

    assert validate_chat_answer(draft, MATERIALS, STATS_ROWS) == ()


def test_percentage_that_no_tool_produced_is_rejected():
    draft = _draft(supported="方向判不出的比例是 22.3%。")

    assert "NUMBER_NOT_TRACEABLE" in validate_chat_answer(draft, MATERIALS, STATS_ROWS)


def test_review_point_number_must_appear_in_the_cited_review():
    draft = _draft(
        review_points=(
            ChatReviewPoint(
                text="有 3 位买家说晚上很吵",
                citation="C1-E1",
                quote="very loud at night",
            ),
        ),
    )

    assert "NUMBER_NOT_TRACEABLE" in validate_chat_answer(draft, MATERIALS, STATS_ROWS)


def test_review_point_number_inside_the_cited_review_is_allowed():
    materials = {"C1-E1": "Took 2 weeks to arrive and is very loud at night."}
    draft = _draft(
        review_points=(
            ChatReviewPoint(
                text="买家说 2 周才到",
                citation="C1-E1",
                quote="very loud at night",
            ),
        ),
        not_supported="样本很少,不能推断普遍程度。",
    )

    assert validate_chat_answer(draft, materials, STATS_ROWS) == ()


def test_stats_text_number_must_come_from_that_row():
    draft = _draft(
        review_points=(),
        stats_points=(
            ChatStatsPoint(
                text="该商品匹配 999 条。",
                stat_key="signal_totals",
                product="商品 A",
                metric="sample_size",
                value=42,
            ),
        ),
    )

    assert "NUMBER_NOT_TRACEABLE" in validate_chat_answer(draft, MATERIALS, STATS_ROWS)


def test_card_matched_count_is_traceable_when_supplied_as_seen_material():
    draft = _draft(supported="这张卡有 9,999 条匹配评论。")

    assert "NUMBER_NOT_TRACEABLE" in validate_chat_answer(draft, MATERIALS, STATS_ROWS)
    assert validate_chat_answer(draft, MATERIALS, STATS_ROWS, {"9999"}) == ()


def test_population_and_sample_metrics_cannot_be_crossed():
    # A population total cited under the sample stat is refused outright, so a
    # sentence can never pair "766 matched reviews" with a sample count.
    draft = _draft(
        review_points=(),
        stats_points=(
            ChatStatsPoint(
                text="全部匹配评论里反对 0 条。",
                stat_key="signal_totals",
                product="商品 A",
                metric="matched_total",
                value=766,
            ),
        ),
    )

    assert "UNKNOWN_STAT_REF" in validate_chat_answer(draft, MATERIALS, STATS_ROWS)


def test_population_totals_are_citable_with_their_own_denominator():
    draft = _draft(
        review_points=(),
        stats_points=(
            ChatStatsPoint(
                text="全部 766 条匹配评论里,反对 166 条。",
                stat_key="full_population_totals",
                product="商品 A",
                metric="opposing",
                value=166,
            ),
        ),
    )

    assert validate_chat_answer(draft, MATERIALS, STATS_ROWS) == ()


def test_card_payload_namespaces_ids_and_flattens_text():
    payload = build_card_payload(
        card_number=2,
        card_alias="EV-02",
        product_label="商品 B",
        aspect_label="运行噪音",
        matched_count=7,
        reviews=[("E1", "line one\nline two")],
    )

    assert payload["reviews"] == [{"id": "C2-E1", "text": "line one line two"}]
    assert payload["card_alias"] == "EV-02"
