"""Reviewed label set: cards collapse across runs, rates add up, no text leaks."""

from __future__ import annotations

import json
from pathlib import Path

from ecom_review_intelligence.reviewed_labels import (
    build_summary,
    summarize_log,
    write_reports,
)
from ecom_review_intelligence.workbench_contracts import VerdictRecord


def _record(
    run_token: str,
    run_alias: str,
    card_alias: str,
    verdict: str,
    recorded_at: str,
    *,
    aspect: str = "noise",
    workflow: str = "diagnosis",
    product_labels: tuple[str, ...] = ("AGLUCKY 便携制冰机",),
) -> VerdictRecord:
    return VerdictRecord(
        run_token=run_token,
        run_alias=run_alias,
        card_alias=card_alias,
        verdict=verdict,  # type: ignore[arg-type]
        recorded_at=recorded_at,
        workflow=workflow,  # type: ignore[arg-type]
        aspect=aspect,  # type: ignore[arg-type]
        product_labels=product_labels,
        polarity="opposing",
        strength="STRONG",
        matched_count=766,
        evidence_count=5,
    )


def test_cards_collapse_across_runs_and_latest_verdict_wins() -> None:
    records = [
        _record("token-a", "RUN-A", "EV-01", "confirmed", "2026-11-01T10:00:00+00:00"),
        _record("token-b", "RUN-B", "EV-01", "doubtful", "2026-11-02T10:00:00+00:00"),
        _record("token-a", "RUN-A", "EV-02", "confirmed", "2026-11-01T10:01:00+00:00"),
    ]

    summary = build_summary(records, generated_at="2026-11-02T11:00:00+00:00")

    assert summary["cards_total"] == 2
    assert summary["runs_total"] == 2
    assert summary["verdict_totals"] == {"confirmed": 1, "doubtful": 1, "rejected": 0}
    assert summary["flipped_cards"] == 1
    cards = {card["card_alias"]: card for card in summary["cards"]}  # type: ignore[union-attr]
    flipped = cards["EV-01"]
    assert flipped["final_verdict"] == "doubtful"
    assert flipped["verdict_history"] == ["confirmed", "doubtful"]
    assert flipped["times_reviewed"] == 2
    assert flipped["final_run_alias"] == "RUN-B"
    stable = cards["EV-02"]
    assert stable["final_verdict"] == "confirmed"
    assert stable["verdict_history"] == ["confirmed"]


def test_rates_and_aspect_totals_are_derivable_from_counts() -> None:
    records = [
        _record("t1", "RUN-A", "EV-01", "confirmed", "2026-11-01T10:00:00+00:00"),
        _record("t2", "RUN-B", "EV-01", "confirmed", "2026-11-02T10:00:00+00:00"),
        _record(
            "t3",
            "RUN-C",
            "EV-01",
            "rejected",
            "2026-11-03T10:00:00+00:00",
            aspect="leakage",
            workflow="comparison",
            product_labels=("A", "B"),
        ),
    ]

    summary = build_summary(records, generated_at="2026-11-03T11:00:00+00:00")

    assert summary["confirmation_rate"] == 0.5
    assert summary["divergence_rate"] == 0.5
    assert summary["aspect_totals"] == {
        "leakage": {"rejected": 1},
        "noise": {"confirmed": 1},
    }


def test_reports_hold_no_review_text_and_no_local_paths(tmp_path: Path) -> None:
    verdicts_path = tmp_path / "verdicts.jsonl"
    verdicts_path.write_text(
        _record("t1", "RUN-A", "EV-01", "confirmed", "2026-11-01T10:00:00+00:00").model_dump_json()
        + "\n",
        encoding="utf-8",
    )

    summary = summarize_log(verdicts_path)
    json_path, markdown_path = write_reports(summary, tmp_path / "out")

    for path in (json_path, markdown_path):
        body = path.read_text(encoding="utf-8")
        assert str(tmp_path) not in body, "a local absolute path leaked into the report"
        assert "very loud" not in body, "review text must never reach the report"
        assert path.name in {"reviewed-verdict-labels.json", "reviewed-verdict-labels.md"}
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "reviewed-verdicts-v1"


def test_empty_log_writes_a_zero_state_report(tmp_path: Path) -> None:
    summary = summarize_log(tmp_path / "missing.jsonl")

    assert summary["cards_total"] == 0
    assert summary["confirmation_rate"] == 0.0
    json_path, markdown_path = write_reports(summary, tmp_path / "out")
    assert json.loads(json_path.read_text(encoding="utf-8"))["cards_total"] == 0
    assert "还没有任何人工核对记录" in markdown_path.read_text(encoding="utf-8")
