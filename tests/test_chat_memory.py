"""Tests for D3 chat memory: quote-free persistence, restart restore, and the
history/preference endpoints."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from ecom_review_intelligence.agent_chat import ChatAnswerDraft, ChatReviewPoint
from ecom_review_intelligence.chat_memory import (
    MEMORY_NOTE,
    ChatMemoryError,
    ChatMemoryStore,
    redact_turn_for_memory,
)
from ecom_review_intelligence.model_gateway import ChatToolStep, ChatTurnOutcome, GatewayError
from ecom_review_intelligence.workbench_service import P6BackedWorkbenchService
from ecom_review_intelligence.workbench_web import create_workbench_app

# --------------------------------------------------------------------------
# Pure storage tests


def _turn(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "run_token": "token-1",
        "status": "READY",
        "supported": "样本里提到夜间噪音。",
        "review_points": (
            {"text": "有买家说晚上很吵", "citation": "C1-E1", "quote": "very loud at night"},
        ),
        "stats_points": (),
        "not_supported": "样本不能推断普遍程度。",
        "session_turns": 1,
    }
    value.update(updates)
    return value


def test_redaction_strips_quotes_but_keeps_the_answer(tmp_path: Path) -> None:
    del tmp_path
    redacted = redact_turn_for_memory(_turn())
    assert redacted["review_points"][0]["quote"] is None
    assert redacted["review_points"][0]["citation"] == "C1-E1"
    assert "very loud at night" not in json.dumps(redacted, ensure_ascii=False)
    assert redacted["supported"] == "样本里提到夜间噪音。"


def test_append_then_load_round_trips_without_quotes(tmp_path: Path) -> None:
    store = ChatMemoryStore(tmp_path / "memory.sqlite")
    store.append_turn(
        "token-1",
        user_message="晚上噪音怎么样？",
        assistant_summary="样本里提到夜间噪音。",
        turn=_turn(),
    )
    history = store.load_history("token-1")
    assert len(history) == 1
    row = history[0]
    assert row["user_message"] == "晚上噪音怎么样？"
    assert row["turn"]["status"] == "READY"
    assert row["turn"]["review_points"][0]["quote"] is None
    assert "very loud at night" not in json.dumps(row["turn"], ensure_ascii=False)


def test_history_is_scoped_by_run_and_limited_to_the_last_turns(tmp_path: Path) -> None:
    store = ChatMemoryStore(tmp_path / "memory.sqlite")
    for index in range(10):
        store.append_turn(
            f"token-{index % 2}",
            user_message=f"问题 {index}",
            assistant_summary=f"回答 {index}",
            turn=_turn(run_token=f"token-{index % 2}"),
        )
    assert store.count_turns("token-0") == 5
    latest = store.load_history("token-0", limit=3)
    assert [row["user_message"] for row in latest] == ["问题 4", "问题 6", "问题 8"]


def test_clear_history_removes_only_that_run(tmp_path: Path) -> None:
    store = ChatMemoryStore(tmp_path / "memory.sqlite")
    store.append_turn("token-1", user_message="a", assistant_summary="b", turn=_turn())
    store.append_turn("token-2", user_message="c", assistant_summary="d", turn=_turn())

    assert store.clear_history("token-1") == 1
    assert store.load_history("token-1") == []
    assert store.count_turns("token-2") == 1


def test_memory_survives_a_reopen(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite"
    ChatMemoryStore(path).append_turn(
        "token-1", user_message="重启前的问题", assistant_summary="重启前的回答", turn=_turn()
    )

    reopened = ChatMemoryStore(path)
    history = reopened.load_history("token-1")
    assert history[0]["user_message"] == "重启前的问题"


def test_preferences_reject_secretish_keys_and_bad_values(tmp_path: Path) -> None:
    store = ChatMemoryStore(tmp_path / "memory.sqlite")

    with pytest.raises(ChatMemoryError):
        store.set_preference("api_key", "sk-test")
    with pytest.raises(ChatMemoryError):
        store.set_preference("Bad Key!", "x")
    with pytest.raises(ChatMemoryError):
        store.set_preference("chat_model", "")
    with pytest.raises(ChatMemoryError):
        store.set_preference("chat_model", "x" * 201)

    store.set_preference("chat_model", "deepseek-v4-pro")
    store.set_preference("chat_model", "deepseek-v4-flash")
    assert store.preferences() == {"chat_model": "deepseek-v4-flash"}


# --------------------------------------------------------------------------
# Service + endpoint integration


REVIEW_TEXTS = (
    "The noise from the compressor is very loud at night.",
    "The noise is acceptable when it sits in the kitchen.",
    "I bought it because my office has too much background noise already.",
)


def _chat_fixture(tmp_path: Path):
    from ecom_review_intelligence.engine import compute_evidence_id, initialize_store

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
            ((REVIEW_TEXTS[0], 1.0), (REVIEW_TEXTS[1], 5.0), (REVIEW_TEXTS[2], 4.0)), start=1
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


class _ScriptedChatGateway:
    def __init__(self, steps=()):
        self.steps = list(steps)

    def run_turn(self, messages, tool_executor):
        del messages
        steps = []
        calls = 0
        while self.steps:
            step = self.steps.pop(0)
            calls += 1
            if step[0] == "tool":
                _kind, name, arguments = step
                _result, title, detail, status = tool_executor(name, arguments)
                steps.append(
                    ChatToolStep(
                        round=calls,
                        tool=name,
                        title=title,
                        detail=tuple(detail),
                        status=status,
                        duration_ms=2,
                    )
                )
                continue
            steps.append(
                ChatToolStep(
                    round=calls,
                    tool="submit_chat_answer",
                    title="提交回答",
                    detail=(),
                    status="ok",
                    duration_ms=0,
                )
            )
            return ChatTurnOutcome(step[1], tuple(steps), calls)
        raise GatewayError("MODEL_OUTPUT_INVALID", "Scripted chat steps are exhausted.")


def _draft() -> ChatAnswerDraft:
    return ChatAnswerDraft(
        supported="样本里有买家抱怨夜间噪音。",
        review_points=(
            ChatReviewPoint(
                text="有买家说晚上很吵",
                citation="C1-E1",
                quote="very loud at night",
            ),
        ),
        stats_points=(),
        not_supported="样本只有 3 条，不能推断普遍程度。",
    )


def _memory_service(tmp_path: Path, steps=(), *, fixture=None) -> P6BackedWorkbenchService:
    store, checkpoint, config, product_id = fixture or _chat_fixture(tmp_path)
    return P6BackedWorkbenchService(
        store_path=store,
        checkpoint_path=checkpoint,
        trace_path=tmp_path / "workbench-traces.jsonl",
        config=config,
        product_ids_by_code={"frostline": product_id},
        chat_gateway_factory=lambda: _ScriptedChatGateway(steps),
        chat_memory_path=tmp_path / "chat-memory.sqlite",
        export_dir=tmp_path / "exports",
        project_root=tmp_path,
        uuid_factory=lambda: UUID("00000000-0000-4000-8000-000000000001"),
    )


def _create_run(client: TestClient) -> str:
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
    return created.json()["run_token"]


def _send_chat(client: TestClient, run_token: str, message: str):
    return client.post("/api/v1/chat", json={"run_token": run_token, "message": message})


def test_a_finished_chat_turn_is_stored_and_restored_quote_free(tmp_path: Path) -> None:
    service = _memory_service(
        tmp_path,
        steps=[
            ("tool", "read_card_reviews", json.dumps({"card_number": 1})),
            ("submit", _draft()),
        ],
    )
    client = TestClient(create_workbench_app(service))
    run_token = _create_run(client)

    sent = _send_chat(client, run_token, "晚上噪音怎么样？")
    assert sent.status_code == 200
    assert sent.json()["status"] == "READY"

    history = client.get(f"/api/v1/chat/{run_token}/history")
    assert history.status_code == 200
    body = history.json()
    assert body["schema_version"] == "p7-chat-history-v1"
    assert body["memory_note"] == MEMORY_NOTE
    assert len(body["turns"]) == 1
    restored = body["turns"][0]
    assert restored["user_message"] == "晚上噪音怎么样？"
    assert restored["turn"]["status"] == "READY"
    assert restored["turn"]["review_points"][0]["quote"] is None
    assert "very loud at night" not in history.text


def test_a_restart_preloads_model_history_and_the_turn_budget(tmp_path: Path) -> None:
    fixture = _chat_fixture(tmp_path)
    service = _memory_service(
        tmp_path,
        steps=[
            ("tool", "read_card_reviews", json.dumps({"card_number": 1})),
            ("submit", _draft()),
        ],
        fixture=fixture,
    )
    client = TestClient(create_workbench_app(service))
    run_token = _create_run(client)
    assert _send_chat(client, run_token, "重启前的问题").json()["status"] == "READY"

    # A fresh process: same memory file, empty in-process sessions. The run
    # view itself is process memory today, so the test re-registers it — what
    # is under test here is the history preload, which is new in D3.
    restarted = _memory_service(tmp_path, fixture=fixture)
    restarted._runs[run_token] = service.get_run(run_token)
    session = restarted._chat_session_for(run_token)
    assert session.turns == 1
    assert session.history[0] == {"role": "user", "content": "重启前的问题"}
    assert "样本里有买家抱怨夜间噪音" in session.history[1]["content"]
    assert "very loud at night" not in session.history[1]["content"]


def test_clear_endpoint_forgets_only_the_stored_conversation(tmp_path: Path) -> None:
    service = _memory_service(
        tmp_path,
        steps=[
            ("tool", "read_card_reviews", json.dumps({"card_number": 1})),
            ("submit", _draft()),
        ],
    )
    client = TestClient(create_workbench_app(service))
    run_token = _create_run(client)
    assert _send_chat(client, run_token, "再来一条").json()["status"] == "READY"

    cleared = client.delete(f"/api/v1/chat/{run_token}/history")
    assert cleared.status_code == 200
    assert cleared.json() == {
        "schema_version": "p7-chat-history-clear-v1",
        "run_token": run_token,
        "cleared": 1,
    }
    assert client.get(f"/api/v1/chat/{run_token}/history").json()["turns"] == []


def test_preferences_endpoints_store_and_refuse(tmp_path: Path) -> None:
    client = TestClient(create_workbench_app(_memory_service(tmp_path)))

    upsert = client.put(
        "/api/v1/preferences", json={"key": "chat_model", "value": "deepseek-v4-pro"}
    )
    assert upsert.status_code == 200
    assert upsert.json()["preferences"] == {"chat_model": "deepseek-v4-pro"}
    assert client.get("/api/v1/preferences").json()["preferences"] == {
        "chat_model": "deepseek-v4-pro"
    }

    refused = client.put("/api/v1/preferences", json={"key": "api_key", "value": "sk-test"})
    assert refused.status_code == 422
    assert "sk-test" not in refused.text
