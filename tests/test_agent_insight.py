import json
import sqlite3
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from ecom_review_intelligence.agent_insight import (
    InsightDraft,
    InsightReviewNote,
    InsightSpecific,
    build_payload,
    sanitize_public_text,
    validate_draft,
)
from ecom_review_intelligence.engine import compute_evidence_id, initialize_store
from ecom_review_intelligence.workbench_service import P6BackedWorkbenchService
from ecom_review_intelligence.workbench_web import create_workbench_app

REVIEWS = {
    "E1": "The noise from the compressor is very loud at night.",
    "E2": "The noise is acceptable when it sits in the kitchen.",
    "E3": "I bought it because my office has too much background noise already.",
}


def _notes(**overrides):
    base = {
        "E1": InsightReviewNote(
            evidence_alias="E1",
            relevant=True,
            note="夜间噪音大",
            quote="very loud at night",
        ),
        "E2": InsightReviewNote(
            evidence_alias="E2",
            relevant=True,
            note="放在厨房可以接受",
            quote="acceptable when it sits in the kitchen",
        ),
        "E3": InsightReviewNote(
            evidence_alias="E3",
            relevant=False,
            note="说的是办公室本身的背景噪音",
            quote="my office has too much background noise",
        ),
    }
    base.update(overrides)
    return tuple(base.values())


def _draft(**updates):
    value = {
        "reviews": _notes(),
        "supported": "两条评论对噪音的容忍度不一致。",
        "supported_refs": ("E1", "E2"),
        "specifics": (
            InsightSpecific(
                text="夜间压缩机声音大",
                evidence_aliases=("E1",),
                quote="very loud at night",
            ),
        ),
        "not_supported": "样本只有 3 条,不能推断噪音的普遍程度。",
    }
    value.update(updates)
    return InsightDraft(**value)


def test_payload_carries_aliases_and_text_only():
    payload = build_payload(
        aspect_label="运行噪音",
        product_label="商品 A",
        matched_count=42,
        reviews=[("E1", "line one\nline two")],
    )

    assert payload["reviews"] == [{"id": "E1", "text": "line one line two"}]
    assert payload["expected_review_entries"] == 1
    serialized = json.dumps(payload)
    assert "polarity" not in serialized
    assert "strength" not in serialized


def test_grounded_draft_passes_every_rule():
    check = validate_draft(_draft(), REVIEWS)

    assert check.ok, check.violations


def test_unknown_alias_is_rejected():
    notes = _notes(
        E1=InsightReviewNote(
            evidence_alias="E9", relevant=True, note="夜间噪音大", quote="very loud at night"
        )
    )

    check = validate_draft(_draft(reviews=notes), REVIEWS)

    assert "UNKNOWN_ALIAS" in check.violations


def test_dropped_review_is_rejected():
    notes = _notes()
    check = validate_draft(_draft(reviews=notes[:2]), REVIEWS)

    assert "EVIDENCE_NOT_COVERED" in check.violations


def test_duplicate_review_entry_is_rejected():
    notes = _notes()
    check = validate_draft(_draft(reviews=(*notes, notes[0])), REVIEWS)

    assert "DUPLICATE_ALIAS" in check.violations


def test_relevant_review_that_is_never_cited_is_rejected():
    check = validate_draft(_draft(supported_refs=("E1",)), REVIEWS)

    assert "EVIDENCE_NOT_CITED" in check.violations


def test_citing_an_irrelevant_review_is_rejected():
    check = validate_draft(_draft(supported_refs=("E1", "E2", "E3")), REVIEWS)

    assert "CITED_NOT_RELEVANT" in check.violations


def test_fabricated_quote_is_rejected():
    notes = _notes(
        E1=InsightReviewNote(
            evidence_alias="E1", relevant=True, note="夜间完全安静", quote="completely silent"
        )
    )

    check = validate_draft(_draft(reviews=notes), REVIEWS)

    assert "QUOTE_NOT_VERBATIM" in check.violations


def test_off_topic_reason_must_come_from_its_own_review():
    notes = _notes(
        E3=InsightReviewNote(
            evidence_alias="E3", relevant=False, note="无关", quote="the lunch box was damaged"
        )
    )

    check = validate_draft(_draft(reviews=notes), REVIEWS)

    assert "QUOTE_NOT_VERBATIM" in check.violations


def test_specific_quote_must_come_from_its_own_reviews():
    check = validate_draft(
        _draft(
            specifics=(
                InsightSpecific(
                    text="厨房可以接受", evidence_aliases=("E1",), quote="acceptable in the kitchen"
                ),
            )
        ),
        REVIEWS,
    )

    assert "QUOTE_NOT_VERBATIM" in check.violations


def test_sanitizer_removes_identifier_hash_and_path_shapes():
    cleaned = sanitize_public_text(
        "见 B0ABCDEFGH 与 " + "a" * 64 + " 以及 C:\\private\\store.sqlite 和末尾（E1、E2）"
    )

    assert "B0ABCDEFGH" not in cleaned
    assert "a" * 64 not in cleaned
    assert "store.sqlite" not in cleaned
    assert "E1" not in cleaned


def _insight_fixture(tmp_path):
    store = tmp_path / "evidence.sqlite"
    checkpoint = tmp_path / "checkpoint.sqlite"
    snapshot, contract = "a" * 64, "b" * 64
    initialize_store(
        store,
        snapshot_id=snapshot,
        contract_sha256=contract,
        aspect_terms={"noise": ("noise",)},
        polarity_terms={"supporting": ("quiet",), "opposing": ("loud",)},
    )
    product_id = "LOCAL_PRIVATE_PRODUCT"
    with sqlite3.connect(store) as connection:
        connection.execute(
            "INSERT INTO products(parent_asin,title) VALUES (?,?)",
            (product_id, "Private local title"),
        )
        for line, (text, rating) in enumerate(
            ((REVIEWS["E1"], 1.0), (REVIEWS["E2"], 5.0), (REVIEWS["E3"], 4.0)), start=1
        ):
            connection.execute(
                "INSERT INTO reviews(evidence_id,source_line,parent_asin,text,rating,timestamp,"
                "verified_purchase,helpful_vote) VALUES (?,?,?,?,?,?,?,?)",
                (compute_evidence_id(snapshot, line), line, product_id, text, rating, line, 1, 0),
            )
        connection.execute("INSERT INTO reviews_fts(rowid,text) SELECT rowid,text FROM reviews")
    config = {
        "dataset_snapshot_id": snapshot,
        "contract_sha256": contract,
        "retrieval_limit_per_aspect": 5,
        "model": "deepseek-v4-flash",
        "base_url": "https://api.deepseek.com",
        "config_sha256": "c" * 64,
    }
    return store, checkpoint, config, product_id


class _FakeGateway:
    def __init__(self, draft: InsightDraft) -> None:
        self.draft = draft
        self.calls = 0

    def analyse(self, payload):
        self.calls += 1
        return self.draft


def _service(tmp_path, draft):
    store, checkpoint, config, product_id = _insight_fixture(tmp_path)
    gateway = _FakeGateway(draft)
    service = P6BackedWorkbenchService(
        store_path=store,
        checkpoint_path=checkpoint,
        trace_path=tmp_path / "workbench-traces.jsonl",
        config=config,
        product_ids_by_code={"frostline": product_id},
        insight_gateway_factory=lambda: gateway,
        uuid_factory=lambda: UUID("00000000-0000-4000-8000-000000000001"),
    )
    return service, gateway


def _create_run(client):
    created = client.post(
        "/api/v1/runs",
        json={
            "workflow": "diagnosis",
            "product_codes": ["frostline"],
            "product_query": None,
            "aspect": "noise",
            "question": "Which noise signals should operations verify first?",
        },
    )
    assert created.status_code == 201
    payload = created.json()
    return payload["run_token"], payload["evidence"][0]["evidence_alias"]


def test_insight_endpoint_returns_checked_result_and_caches_it(tmp_path):
    service, gateway = _service(tmp_path, _draft())
    client = TestClient(create_workbench_app(service=service))
    run_token, card_alias = _create_run(client)

    first = client.post(
        "/api/v1/insights", json={"run_token": run_token, "card_alias": card_alias}
    )
    second = client.post(
        "/api/v1/insights", json={"run_token": run_token, "card_alias": card_alias}
    )

    assert first.status_code == 200
    body = first.json()
    assert body["status"] == "READY"
    assert body["model_calls"] == 1
    assert body["review_count"] == 3
    assert body["specifics"][0]["evidence_aliases"] == ["E1"]
    assert body["rejected"] == [
        {"evidence_alias": "E3", "quote": "my office has too much background noise"}
    ]
    assert second.json() == body
    assert gateway.calls == 1
    serialized = json.dumps(body, ensure_ascii=False)
    assert "LOCAL_PRIVATE_PRODUCT" not in serialized
    assert "evidence_id" not in serialized


def test_failed_quote_check_discards_the_whole_result(tmp_path):
    bad = _draft(
        specifics=(
            InsightSpecific(
                text="夜间完全安静",
                evidence_aliases=("E1",),
                quote="completely silent at night",
            ),
        )
    )
    service, _ = _service(tmp_path, bad)
    client = TestClient(create_workbench_app(service=service))
    run_token, card_alias = _create_run(client)

    response = client.post(
        "/api/v1/insights", json={"run_token": run_token, "card_alias": card_alias}
    )

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "VALIDATION_FAILED"
    assert body["reason"] == "QUOTE_NOT_VERBATIM"
    assert body["supported"] == ""
    assert body["specifics"] == []


def test_missing_key_hides_the_step_and_refuses_the_request(tmp_path):
    store, checkpoint, config, product_id = _insight_fixture(tmp_path)
    service = P6BackedWorkbenchService(
        store_path=store,
        checkpoint_path=checkpoint,
        trace_path=tmp_path / "workbench-traces.jsonl",
        config=config,
        product_ids_by_code={"frostline": product_id},
        insight_gateway_factory=lambda: None,
        uuid_factory=lambda: UUID("00000000-0000-4000-8000-000000000001"),
    )
    client = TestClient(create_workbench_app(service=service))

    assert 'data-insight-available="false"' in client.get("/").text
    response = client.post("/api/v1/insights", json={"run_token": "x", "card_alias": "EV-01"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "INSIGHT_UNAVAILABLE"


def test_synthetic_mode_never_offers_the_step():
    client = TestClient(create_workbench_app())

    assert 'data-insight-available="false"' in client.get("/").text
    response = client.post("/api/v1/insights", json={"run_token": "x", "card_alias": "EV-01"})

    assert response.status_code == 404


@pytest.mark.parametrize("alias", ["", "EV-01-EXTRA-LONG-ALIAS"])
def test_request_contract_rejects_bad_aliases(alias):
    client = TestClient(create_workbench_app())

    response = client.post("/api/v1/insights", json={"run_token": "token", "card_alias": alias})

    assert response.status_code == 422

def test_quote_keeps_ordinary_words_but_redacts_pasted_identifiers():
    cleaned = sanitize_public_text(
        "background noise and batteries, comparing with b00fi4dbtu in the review"
    )

    assert "background noise and batteries" in cleaned
    assert "b00fi4dbtu" not in cleaned
