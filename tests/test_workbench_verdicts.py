"""Human check records: they persist, they refuse foreign cards, and they leak no text."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from ecom_review_intelligence.verdict_export import (
    dump_records,
    read_verdicts,
    render_markdown,
)
from ecom_review_intelligence.workbench_contracts import VerdictRecord, VerdictRequest
from ecom_review_intelligence.workbench_service import (
    SyntheticWorkbenchService,
    VerdictStore,
    WorkbenchRunNotFound,
)
from ecom_review_intelligence.workbench_web import create_workbench_app

_RUN = {
    "workflow": "diagnosis",
    "product_codes": ["frostline"],
    "product_query": None,
    "aspect": "noise",
    "question": "评论中有哪些关于运行噪音的问题值得优先处理？",
}


def _client() -> TestClient:
    return TestClient(create_workbench_app(SyntheticWorkbenchService()))


def _new_run(client) -> tuple[str, str]:
    response = client.post("/api/v1/runs", json=_RUN)
    assert response.status_code == 201
    body = response.json()
    return body["run_token"], body["evidence"][0]["evidence_alias"]


def test_verdict_round_trip_keeps_one_record_per_card() -> None:
    client = _client()
    token, alias = _new_run(client)

    first = client.post(
        "/api/v1/verdicts",
        json={"run_token": token, "card_alias": alias, "verdict": "confirmed"},
    )
    assert first.status_code == 201
    assert first.json()["verdict"] == "confirmed"
    assert first.json()["card_alias"] == alias

    second = client.post(
        "/api/v1/verdicts",
        json={"run_token": token, "card_alias": alias, "verdict": "doubtful"},
    )
    assert second.status_code == 201

    listed = client.get(f"/api/v1/runs/{token}/verdicts")
    assert listed.status_code == 200
    records = listed.json()["records"]
    assert len(records) == 1
    assert records[0]["verdict"] == "doubtful"


def test_verdict_rejects_unknown_run_and_foreign_card() -> None:
    client = _client()
    token, alias = _new_run(client)

    missing = client.post(
        "/api/v1/verdicts",
        json={"run_token": "00000000000000000000000000000000", "card_alias": alias, "verdict": "confirmed"},
    )
    assert missing.status_code == 404

    foreign = client.post(
        "/api/v1/verdicts",
        json={"run_token": token, "card_alias": "EV-99", "verdict": "confirmed"},
    )
    assert foreign.status_code == 422


def test_verdict_rejects_unknown_value() -> None:
    client = _client()
    token, alias = _new_run(client)

    response = client.post(
        "/api/v1/verdicts",
        json={"run_token": token, "card_alias": alias, "verdict": "approved"},
    )
    assert response.status_code == 422


def test_service_refuses_a_card_outside_the_run() -> None:
    service = SyntheticWorkbenchService()
    with pytest.raises(WorkbenchRunNotFound):
        service.record_verdict(
            VerdictRequest(run_token="missing", card_alias="EV-01", verdict="confirmed")
        )


def test_file_store_survives_a_new_service_instance(tmp_path) -> None:
    path = tmp_path / "verdicts.jsonl"
    token = "11111111111111111111111111111111"
    record = VerdictRecord(
        run_token=token,
        run_alias="RUN-AAAAAAAA",
        card_alias="EV-01",
        verdict="confirmed",
        recorded_at="2026-10-22T06:00:00+00:00",
        workflow="diagnosis",
        aspect="noise",
        product_labels=("商品 A",),
        polarity="opposing",
        strength="STRONG",
        matched_count=74,
        evidence_count=5,
    )
    VerdictStore(path).append(record)

    reopened = VerdictStore(path)
    records = reopened.for_run(token)
    assert len(records) == 1
    assert records[0].card_alias == "EV-01"


def test_file_store_skips_a_truncated_line(tmp_path) -> None:
    path = tmp_path / "verdicts.jsonl"
    path.write_text('{"broken": \n', encoding="utf-8")

    assert VerdictStore(path).for_run("any") == ()


def test_stored_line_carries_no_review_text(tmp_path) -> None:
    path = tmp_path / "verdicts.jsonl"
    store = VerdictStore(path)
    store.append(
        VerdictRecord(
            run_token="22222222222222222222222222222222",
            run_alias="RUN-BBBBBBBB",
            card_alias="EV-02",
            verdict="rejected",
            recorded_at="2026-10-22T06:01:00+00:00",
            workflow="diagnosis",
            aspect="noise",
            product_labels=("商品 A",),
            polarity="supporting",
            strength="WEAK",
            matched_count=10,
            evidence_count=3,
        )
    )

    payload = json.loads(path.read_text(encoding="utf-8").strip())
    assert set(payload) == {
        "aspect",
        "card_alias",
        "evidence_count",
        "matched_count",
        "polarity",
        "product_labels",
        "recorded_at",
        "run_alias",
        "run_token",
        "schema_version",
        "strength",
        "verdict",
        "workflow",
    }
    assert all(not isinstance(value, str) or len(value) < 120 for value in payload.values())


def test_export_renders_counts_and_no_quotes(tmp_path) -> None:
    path = tmp_path / "verdicts.jsonl"
    store = VerdictStore(path)
    for alias, verdict in (("EV-01", "confirmed"), ("EV-02", "doubtful")):
        store.append(
            VerdictRecord(
                run_token="33333333333333333333333333333333",
                run_alias="RUN-CCCCCCCC",
                card_alias=alias,
                verdict=verdict,
                recorded_at="2026-10-22T06:02:00+00:00",
                workflow="diagnosis",
                aspect="leakage",
                product_labels=("AGLUCKY 便携制冰机（26磅）",),
                polarity="opposing",
                strength="STRONG",
                matched_count=74,
                evidence_count=5,
            )
        )

    records = read_verdicts(path)
    body = render_markdown(records)

    assert "RUN-CCCCCCCC" in body
    assert "核对进度：2/5" in body
    assert "确认 1 · 存疑 1 · 驳回 0" in body
    assert "| EV-01 |" in body and "| EV-02 |" in body
    assert "不含评论原文" in body
    assert dump_records(records).count("card_alias") == 2


def test_export_of_an_empty_log_says_so(tmp_path) -> None:
    assert "还没有核对记录" in render_markdown(read_verdicts(tmp_path / "missing.jsonl"))
