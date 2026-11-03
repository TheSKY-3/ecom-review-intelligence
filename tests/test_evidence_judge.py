"""Tests for the D1 evidence judge: closed verdicts, fail-closed aggregation,
and the public-safety boundary of the summary and report."""

from __future__ import annotations

import pytest

from ecom_review_intelligence.evidence_judge import (
    JUDGE_ISSUE_CODES,
    EvidenceJudgeError,
    JudgeCase,
    build_judge_payload,
    chat_answer_digest,
    count_issue_codes,
    evaluate_cases,
    insight_answer_digest,
    judge_rates,
    parse_judge_verdict,
    per_workflow_counts,
    render_judge_report,
    validate_judge_summary,
)


def _case(**updates: object) -> JudgeCase:
    value = {
        "item_id": "01-insight",
        "workflow": "insight",
        "aspect": "noise",
        "product_codes": ("frostline",),
        "question": "运行噪音普遍吗？",
        "answer_text": "总体判断: 样本里抱怨噪音的占多数。",
        "answer_status": "READY",
        "model_calls": 1,
        "stats": {
            "aggregate": {"matched_count": 766, "supporting": 3, "opposing": 1, "conflicting": 1},
            "products": [
                {
                    "product_code": "frostline",
                    "label": "Frostline Countertop",
                    "matched_count": 766,
                    "sample_supporting": 3,
                    "lexicon_matched": 766,
                }
            ],
        },
    }
    value.update(updates)
    return JudgeCase(**value)  # type: ignore[arg-type]


def _verdict(overall: str = "pass", codes: list[str] | None = None, rationale: str = "ok") -> dict:
    return {"overall": overall, "issue_codes": codes or [], "rationale_code": rationale}


def _summary() -> dict[str, object]:
    entries, counts = evaluate_cases(
        [_case(), _case(item_id="02-chat", workflow="chat")], lambda payload: _verdict()
    )
    return {
        "schema_version": "agent-judge-eval-v1",
        "generated_at": "2026-11-04T00:00:00+00:00",
        "dataset_snapshot_id": "snapshot-2026-08-03",
        "contract_sha256": "a" * 64,
        "mode": "live",
        "model": "deepseek-chat",
        "cases_collected": len(entries),
        "generation_model_calls": 2,
        "judge_model_calls": 2,
        "judge_tokens": {"input": 10, "output": 5},
        "verdict_counts": counts,
        "rates": judge_rates(counts),
        "issue_code_counts": count_issue_codes(entries),
        "per_workflow": per_workflow_counts(entries),
        "limitations": ["Engineering exploration; not a phase gate."],
    }


def test_parse_judge_verdict_accepts_and_normalizes() -> None:
    verdict = parse_judge_verdict(
        {"overall": "warn", "issue_codes": ["OVERSTATES_SAMPLE", "OVERSTATES_SAMPLE"], "rationale_code": "x" * 400}
    )
    assert verdict["overall"] == "warn"
    assert verdict["issue_codes"] == ["OVERSTATES_SAMPLE"]
    assert len(verdict["rationale"]) == 300


def test_parse_judge_verdict_rejects_schema_violations() -> None:
    with pytest.raises(EvidenceJudgeError):
        parse_judge_verdict({"overall": "nope", "issue_codes": [], "rationale_code": "x"})
    with pytest.raises(EvidenceJudgeError):
        parse_judge_verdict({"overall": "pass", "issue_codes": ["MADE_UP"], "rationale_code": "x"})
    with pytest.raises(EvidenceJudgeError):
        parse_judge_verdict({"overall": "pass", "issue_codes": [], "rationale_code": "  "})
    with pytest.raises(EvidenceJudgeError):
        parse_judge_verdict("pass")


def test_parse_judge_verdict_escalates_never_deescalates() -> None:
    from ecom_review_intelligence.evidence_judge import parse_judge_verdict

    escalated = parse_judge_verdict(
        {"overall": "warn", "issue_codes": ["FABRICATED_CLAIM"], "rationale_code": "invented a number"}
    )
    assert escalated["overall"] == "fail"
    kept = parse_judge_verdict(_verdict(overall="fail", codes=["OVERSTATES_SAMPLE"]))
    assert kept["overall"] == "fail"


def test_build_judge_payload_is_closed_and_carries_stats() -> None:
    payload = build_judge_payload(_case())
    assert set(payload) == {
        "item_id",
        "workflow",
        "aspect",
        "products",
        "question",
        "answer_status",
        "answer",
        "ground_truth_stats",
        "discipline_note_required",
    }
    stats = payload["ground_truth_stats"]
    assert isinstance(stats, dict)
    assert stats["aggregate"]["matched_count"] == 766
    assert stats["products"][0]["label"] == "Frostline Countertop"
    assert payload["answer"].startswith("总体判断")


def test_evaluate_cases_full_path_with_fake_judge() -> None:
    cases = [
        _case(),
        _case(item_id="02-chat", workflow="chat", answer_status="VALIDATION_FAILED"),
        _case(item_id="03-chat", workflow="chat"),
    ]

    def flaky_judge(payload: object) -> object:
        if isinstance(payload, dict) and payload["item_id"] == "01-insight":
            return _verdict(codes=["CONTRADICTS_STATS"], rationale="number contradicts the stats")
        if isinstance(payload, dict) and payload["item_id"] == "03-chat":
            raise RuntimeError("judge offline")
        raise AssertionError("unjudgeable cases must not reach the judge")

    entries, counts = evaluate_cases(cases, flaky_judge)
    assert counts == {"pass": 0, "warn": 0, "fail": 1, "error": 1, "skipped": 1}
    assert entries[0]["judge"]["issue_codes"] == ["CONTRADICTS_STATS"]
    assert entries[0]["judge"]["overall"] == "fail"
    assert "judge offline" in entries[2]["judge"]["error"]

    dry_entries, dry_counts = evaluate_cases(cases, None)
    assert dry_counts["skipped"] == 3
    assert all("dry-run" in entry["judge"]["skipped"] for entry in dry_entries)


def test_aggregation_helpers() -> None:
    entries, counts = evaluate_cases(
        [_case(), _case(item_id="02-chat", workflow="chat")],
        lambda payload: _verdict(overall="warn", codes=["MISSING_DISCIPLINE_NOTE"]),
    )
    assert judge_rates(counts) == {"judged": 2, "pass_rate": 0.0, "fail_rate": 0.0, "error_rate": 0.0}
    assert count_issue_codes(entries) == {
        **{code: 0 for code in JUDGE_ISSUE_CODES},
        "MISSING_DISCIPLINE_NOTE": 2,
    }
    workflows = per_workflow_counts(entries)
    assert workflows["insight"]["warn"] == 1
    assert workflows["chat"]["warn"] == 1
    empty = judge_rates({"pass": 0, "warn": 0, "fail": 0, "error": 0, "skipped": 3})
    assert empty["pass_rate"] is None and empty["judged"] == 0


def test_validate_judge_summary_accepts_valid_and_rejects_leaks() -> None:
    summary = _summary()
    validate_judge_summary(summary)

    with pytest.raises(EvidenceJudgeError):
        validate_judge_summary({**summary, "answer_text": "leak"})  # type: ignore[dict-item]
    with pytest.raises(EvidenceJudgeError):
        validate_judge_summary({**summary, "contract_sha256": "short"})
    leaked = dict(summary)
    leaked["limitations"] = ["path leak C:/Users/x"]
    with pytest.raises(EvidenceJudgeError):
        validate_judge_summary(leaked)


def test_render_report_never_quotes_answer_text() -> None:
    marker = "SECRETREVIEWMARKER very loud at night"
    case = _case(answer_text=f"总体判断: 引用样例。{marker}")
    entries, counts = evaluate_cases([case], lambda payload: _verdict())
    summary = {
        **_summary(),
        "cases_collected": 1,
        "generation_model_calls": 1,
        "judge_model_calls": 1,
        "verdict_counts": counts,
        "rates": judge_rates(counts),
    }
    report = render_judge_report(summary, entries)
    assert marker not in report
    assert "SECRETREVIEWMARKER" not in report
    assert "01-insight" in report
    assert "| pass | 1 |" in report
    assert "## 局限" in report


def test_answer_digests_keep_quotes_and_boundaries() -> None:
    insight_view = {
        "status": "READY",
        "supported": "样本里夜间噪音抱怨居多。",
        "specifics": (
            {"text": "有买家说晚上很吵", "evidence_aliases": ("E1",), "quote": "very loud at night"},
        ),
        "rejected": ({"evidence_alias": "E2", "quote": "dropped"},),
        "not_supported": "样本只有 5 条，不能推断普遍程度。",
    }
    digest = insight_answer_digest(insight_view)
    assert "very loud at night" in digest
    assert "被本地校验拒绝的引用: 1 条" in digest
    assert "dropped" not in digest

    chat_view = {
        "status": "READY",
        "supported": "先看 E1。",
        "review_points": (
            {"text": "晚上很吵", "citation": "C1-E1", "quote": "very loud at night"},
        ),
        "stats_points": ({"text": "匹配 766 条", "stat_key": "matched", "product": "frostline", "metric": "matched_count", "value": 766},),
        "not_supported": "样本不是统计结论。",
    }
    chat_digest = chat_answer_digest(chat_view)
    assert "C1-E1" in chat_digest and "766" in chat_digest
    assert "样本不是统计结论" in chat_digest
