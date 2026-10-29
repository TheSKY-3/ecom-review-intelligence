"""Endpoint tests for the constrained chat assistant on the local workbench."""

import json
import sqlite3
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

from ecom_review_intelligence.agent_chat import ChatAnswerDraft, ChatReviewPoint
from ecom_review_intelligence.model_gateway import ChatToolStep, ChatTurnOutcome, GatewayError
from ecom_review_intelligence.workbench_service import P6BackedWorkbenchService
from ecom_review_intelligence.workbench_web import create_workbench_app

REVIEW_TEXTS = (
    "The noise from the compressor is very loud at night.",
    "The noise is acceptable when it sits in the kitchen.",
    "I bought it because my office has too much background noise already.",
)

PRODUCT_LABEL = "Private local title"


def _chat_fixture(tmp_path):
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
            (product_id, PRODUCT_LABEL),
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
    """Scripted tool loop: ("tool", name, args) rounds, then ("submit", draft)."""

    def __init__(self, steps=()):
        self.steps = list(steps)
        self.messages_seen = []

    def run_turn(self, messages, tool_executor):
        self.messages_seen.append([dict(item) for item in messages])
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
            draft = step[1]
            steps.append(
                ChatToolStep(
                    round=calls,
                    tool="submit_chat_answer",
                    title="提交回答",
                    detail=(
                        f"引用 {len(draft.review_points)} 条评论",
                        f"统计 {len(draft.stats_points)} 项",
                    ),
                    status="ok",
                    duration_ms=0,
                )
            )
            return ChatTurnOutcome(draft, tuple(steps), calls)
        raise GatewayError(
            "MODEL_OUTPUT_INVALID", "Scripted chat steps are exhausted.", steps=tuple(steps)
        )


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
        "not_supported": "样本只有 3 条,不能推断普遍程度。",
    }
    value.update(updates)
    return ChatAnswerDraft(**value)


def _service(tmp_path, steps=(), uuid_factory=None, user_chat_gateway_factory=None):
    store, checkpoint, config, product_id = _chat_fixture(tmp_path)
    gateway = _ScriptedChatGateway(steps)
    service = P6BackedWorkbenchService(
        store_path=store,
        checkpoint_path=checkpoint,
        trace_path=tmp_path / "workbench-traces.jsonl",
        config=config,
        product_ids_by_code={"frostline": product_id},
        chat_gateway_factory=lambda: gateway,
        user_chat_gateway_factory=user_chat_gateway_factory,
        export_dir=tmp_path / "exports",
        project_root=tmp_path,
        uuid_factory=uuid_factory
        or (lambda: UUID("00000000-0000-4000-8000-000000000001")),
    )
    return service, gateway


def _counting_uuids(start=2):
    import itertools

    counter = itertools.count(start)
    return lambda: UUID(f"00000000-0000-4000-8000-{next(counter):012d}")


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
    return created.json()["run_token"]


def test_chat_turn_reads_a_card_and_returns_the_checked_answer(tmp_path):
    service, _gateway = _service(
        tmp_path,
        steps=[
            ("tool", "read_card_reviews", json.dumps({"card_number": 1})),
            ("submit", _draft()),
        ],
    )
    client = TestClient(create_workbench_app(service=service))
    run_token = _create_run(client)

    response = client.post(
        "/api/v1/chat", json={"run_token": run_token, "message": "晚上噪音怎么样?"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "READY"
    assert body["review_points"][0]["citation"] == "C1-E1"
    assert body["model_calls"] == 2
    assert body["session_turns"] == 1
    assert [step["title"] for step in body["steps"]] == ["读取证据卡 1", "提交回答"]
    first = body["steps"][0]
    assert first["index"] == 1
    assert first["round"] == 1
    assert first["tool"] == "read_card_reviews"
    assert first["status"] == "ok"
    assert first["detail"] == [f"EV-01 · {PRODUCT_LABEL}", "3 条评论原文"]
    assert body["steps"][1]["detail"] == ["引用 1 条评论", "统计 0 项"]


def test_second_turn_keeps_the_conversation_in_context(tmp_path):
    service, gateway = _service(
        tmp_path,
        steps=[
            ("tool", "read_card_reviews", json.dumps({"card_number": 1})),
            ("submit", _draft()),
        ],
    )
    client = TestClient(create_workbench_app(service=service))
    run_token = _create_run(client)
    first = client.post("/api/v1/chat", json={"run_token": run_token, "message": "晚上噪音怎么样?"})
    assert first.json()["status"] == "READY"

    gateway.steps = [("submit", _draft())]
    second = client.post(
        "/api/v1/chat", json={"run_token": run_token, "message": "那白天呢?"}
    )

    assert second.json()["status"] == "READY"
    assert second.json()["session_turns"] == 2
    follow_up = [item for item in gateway.messages_seen[-1] if item["content"] == "那白天呢?"]
    assert follow_up, "the new message must be the last entry"
    assert any(
        "样本里有买家抱怨夜间噪音" in item["content"] for item in gateway.messages_seen[-1]
    ), "prior answer summary stays in context"


def test_failed_check_discards_the_turn_and_adds_no_history(tmp_path):
    bad = _draft(
        review_points=(
            ChatReviewPoint(
                text="有买家说晚上完全安静",
                citation="C1-E1",
                quote="completely silent at night",
            ),
        ),
    )
    service, gateway = _service(
        tmp_path, steps=[("tool", "read_card_reviews", json.dumps({"card_number": 1})), ("submit", bad)]
    )
    client = TestClient(create_workbench_app(service=service))
    run_token = _create_run(client)

    response = client.post("/api/v1/chat", json={"run_token": run_token, "message": "晚上安静吗?"})

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "VALIDATION_FAILED"
    assert body["reason"] == "QUOTE_NOT_VERBATIM"
    assert body["supported"] == ""
    follow_up = client.post(
        "/api/v1/chat", json={"run_token": run_token, "message": "重说一遍"}
    )
    assert follow_up.status_code == 200
    context = gateway.messages_seen[-1][0]["content"]
    assert "晚上安静吗" not in context, "failed turns leave no history behind"


def test_reading_a_card_records_its_matched_count_for_the_number_check(tmp_path):
    service, _gateway = _service(
        tmp_path,
        steps=[
            ("tool", "read_card_reviews", json.dumps({"card_number": 1})),
            ("submit", _draft()),
        ],
    )
    client = TestClient(create_workbench_app(service=service))
    run_token = _create_run(client)

    client.post("/api/v1/chat", json={"run_token": run_token, "message": "噪音怎么样?"})

    matched = service.get_run(run_token).product_summaries[0].matched_count
    assert str(matched) in service._chat_sessions[run_token].card_numbers


def test_stats_tool_feeds_a_checkable_answer(tmp_path):
    service, gateway = _service(tmp_path)
    client = TestClient(create_workbench_app(service=service))
    run_token = _create_run(client)
    matched = service.get_run(run_token).product_summaries[0].matched_count
    gateway.steps = [
        ("tool", "query_run_stats", json.dumps({"stat_key": "full_population_totals"})),
        (
            "submit",
            _draft(
                review_points=(),
                stats_points=(
                    {
                        "text": f"该商品共匹配 {matched} 条评论。",
                        "stat_key": "full_population_totals",
                        "product": PRODUCT_LABEL,
                        "metric": "matched_total",
                        "value": matched,
                    },
                ),
            ),
        ),
    ]

    response = client.post(
        "/api/v1/chat", json={"run_token": run_token, "message": "一共有多少条相关评论?"}
    )

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "READY"
    assert body["stats_points"][0]["value"] == matched
    assert body["steps"][0]["title"] == "查询统计 · full_population_totals"
    assert body["steps"][0]["detail"][0] == "1 个商品"
    assert "匹配评论中" not in " ".join(body["steps"][0]["detail"])


def test_rating_distribution_stat_matches_the_reviews(tmp_path):
    service, gateway = _service(tmp_path)
    client = TestClient(create_workbench_app(service=service))
    run_token = _create_run(client)
    gateway.steps = [
        ("tool", "query_run_stats", json.dumps({"stat_key": "rating_distribution"})),
        (
            "submit",
            _draft(
                review_points=(),
                not_supported="只有这几条样本,不能推断整体分布。",
                stats_points=(
                    {
                        "text": "1 星评论有 1 条。",
                        "stat_key": "rating_distribution",
                        "product": PRODUCT_LABEL,
                        "metric": "r1",
                        "value": 1,
                    },
                ),
            ),
        ),
    ]

    response = client.post(
        "/api/v1/chat", json={"run_token": run_token, "message": "差评占多少?"}
    )

    body = response.json()
    assert body["status"] == "READY"
    assert body["stats_points"][0]["metric"] == "r1"
    assert body["stats_points"][0]["value"] == 1
    assert body["steps"][0]["title"] == "查询统计 · rating_distribution"
    assert body["steps"][0]["detail"] == ["1 个商品", "指标: r1 / r2 / r3 / r4 / r5 / none"]


def test_untraceable_number_is_rejected_end_to_end(tmp_path):
    service, _gateway = _service(
        tmp_path,
        steps=[
            (
                "submit",
                _draft(
                    supported="匹配 766 条。",
                    review_points=(),
                    not_supported="不能推断普遍程度。",
                ),
            )
        ],
    )
    client = TestClient(create_workbench_app(service=service))
    run_token = _create_run(client)

    response = client.post(
        "/api/v1/chat", json={"run_token": run_token, "message": "有多少条相关评论?"}
    )

    body = response.json()
    assert body["status"] == "VALIDATION_FAILED"
    assert "NUMBER_NOT_TRACEABLE" in body["reason"]
    assert body["supported"] == ""


def test_session_limit_stops_the_conversation(tmp_path):
    from ecom_review_intelligence.agent_chat import MAX_CHAT_TURNS

    service, _gateway = _service(tmp_path, steps=[("submit", _draft())])
    client = TestClient(create_workbench_app(service=service))
    run_token = _create_run(client)
    session = service._chat_sessions.get(run_token)
    assert session is None
    client.post("/api/v1/chat", json={"run_token": run_token, "message": "第一问"})
    service._chat_sessions[run_token].turns = MAX_CHAT_TURNS

    response = client.post("/api/v1/chat", json={"run_token": run_token, "message": "再问一次"})

    body = response.json()
    assert body["status"] == "UNAVAILABLE"
    assert body["reason"] == "CHAT_SESSION_LIMIT"


def test_missing_key_answers_unavailable_without_spending(tmp_path):
    store, checkpoint, config, product_id = _chat_fixture(tmp_path)
    service = P6BackedWorkbenchService(
        store_path=store,
        checkpoint_path=checkpoint,
        trace_path=tmp_path / "workbench-traces.jsonl",
        config=config,
        product_ids_by_code={"frostline": product_id},
        chat_gateway_factory=lambda: None,
        uuid_factory=lambda: UUID("00000000-0000-4000-8000-000000000001"),
    )
    client = TestClient(create_workbench_app(service=service))
    run_token = _create_run(client)

    response = client.post("/api/v1/chat", json={"run_token": run_token, "message": "晚上噪音怎么样?"})

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "UNAVAILABLE"
    assert body["reason"] == "MODEL_NOT_CONFIGURED"


def test_synthetic_mode_refuses_the_chat():
    client = TestClient(create_workbench_app())

    response = client.post("/api/v1/chat", json={"run_token": "x", "message": "hi"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CHAT_UNAVAILABLE"


def test_contract_rejects_an_empty_message():
    client = TestClient(create_workbench_app())

    response = client.post("/api/v1/chat", json={"run_token": "token", "message": " "})

    assert response.status_code == 422


_VISITOR_KEY = "test-visitor-key-0123456789abcdef"


def test_chat_model_override_reaches_the_gateway(tmp_path):
    seen = []

    def factory(key: str, model: str):
        seen.append((key, model))
        return _ScriptedChatGateway([("submit", _draft())])

    service, _gateway = _service(tmp_path, user_chat_gateway_factory=factory)
    client = TestClient(create_workbench_app(service=service))
    run_token = _create_run(client)

    response = client.post(
        "/api/v1/chat",
        json={"run_token": run_token, "message": "晚上噪音怎么样?"},
        headers={
            "X-Insight-Key": _VISITOR_KEY,
            "X-Insight-Model": "deepseek-v4-pro",
        },
    )

    assert response.status_code == 200
    assert seen == [(_VISITOR_KEY, "deepseek-v4-pro")]


def test_chat_uses_the_configured_model_by_default(tmp_path):
    seen = []

    def factory(key: str, model: str):
        seen.append(model)
        return _ScriptedChatGateway([("submit", _draft())])

    service, _gateway = _service(tmp_path, user_chat_gateway_factory=factory)
    client = TestClient(create_workbench_app(service=service))
    run_token = _create_run(client)

    response = client.post(
        "/api/v1/chat",
        json={"run_token": run_token, "message": "晚上噪音怎么样?"},
        headers={"X-Insight-Key": _VISITOR_KEY},
    )

    assert response.status_code == 200
    assert seen == ["deepseek-v4-flash"]


def test_chat_rejects_a_model_this_deployment_does_not_offer(tmp_path):
    service, _gateway = _service(tmp_path)
    client = TestClient(create_workbench_app(service=service))
    run_token = _create_run(client)

    response = client.post(
        "/api/v1/chat",
        json={"run_token": run_token, "message": "晚上噪音怎么样?"},
        headers={"X-Insight-Model": "gpt-4o"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"
    assert "gpt-4o" not in response.text


def test_chat_create_run_switches_the_session_to_the_new_run(tmp_path):
    service, _gateway = _service(
        tmp_path,
        steps=[
            (
                "tool",
                "create_run",
                json.dumps({"workflow": "diagnosis", "product_numbers": [1], "aspect": "noise"}),
            ),
            ("tool", "read_card_reviews", json.dumps({"card_number": 1})),
            ("submit", _draft()),
        ],
        uuid_factory=_counting_uuids(),
    )
    client = TestClient(create_workbench_app(service=service))
    old_token = _create_run(client)

    response = client.post("/api/v1/chat", json={"run_token": old_token, "message": "看看可靠性"})

    body = response.json()
    assert body["status"] == "READY"
    new_token = body["created_run_token"]
    assert new_token and new_token != old_token
    assert body["run_token"] == new_token
    assert any(step["title"] == "发起新分析" for step in body["steps"])
    assert service._chat_sessions.get(old_token) is None
    rebound = service._chat_sessions[new_token]
    assert rebound.run_token == new_token
    assert "C1-E1" in rebound.materials, "post-switch reads register the new run's reviews"
    # the turn after the switch reads the NEW run's card and its citations check out
    assert body["review_points"][0]["citation"] == "C1-E1"


def test_chat_create_run_shape_error_keeps_the_old_session(tmp_path):
    service, _gateway = _service(
        tmp_path,
        steps=[
            (
                "tool",
                "create_run",
                json.dumps({"workflow": "comparison", "product_numbers": [1], "aspect": "noise"}),
            ),
            ("tool", "read_card_reviews", json.dumps({"card_number": 1})),
            ("submit", _draft()),
        ],
    )
    client = TestClient(create_workbench_app(service=service))
    run_token = _create_run(client)

    response = client.post("/api/v1/chat", json={"run_token": run_token, "message": "对比一下"})

    body = response.json()
    assert body["status"] == "READY"
    assert body["created_run_token"] is None
    assert any(
        step["status"] == "error" and any("参数不合法" in line for line in step["detail"])
        for step in body["steps"]
    )
    assert service._chat_sessions[run_token].run_token == run_token
    assert body["review_points"][0]["citation"] == "C1-E1"


def test_chat_create_run_unknown_product_reports_valid_numbers(tmp_path):
    service, _gateway = _service(
        tmp_path,
        steps=[
            (
                "tool",
                "create_run",
                json.dumps({"workflow": "diagnosis", "product_numbers": [7], "aspect": "noise"}),
            ),
            ("submit", _draft()),
        ],
    )
    client = TestClient(create_workbench_app(service=service))
    run_token = _create_run(client)

    response = client.post("/api/v1/chat", json={"run_token": run_token, "message": "看7号商品"})

    body = response.json()
    assert body["status"] == "VALIDATION_FAILED", "the follow-up draft cites an unread card"
    assert any(
        "商品编号 [7] 不存在" in line for step in body["steps"] for line in step["detail"]
    )
    assert body["created_run_token"] is None


def test_chat_create_run_turn_still_counts_toward_the_session_limit(tmp_path):
    from ecom_review_intelligence.agent_chat import MAX_CHAT_TURNS

    service, _gateway = _service(
        tmp_path,
        steps=[
            (
                "tool",
                "create_run",
                json.dumps({"workflow": "diagnosis", "product_numbers": [1], "aspect": "noise"}),
            ),
            ("tool", "read_card_reviews", json.dumps({"card_number": 1})),
            ("submit", _draft()),
        ],
        uuid_factory=_counting_uuids(),
    )
    client = TestClient(create_workbench_app(service=service))
    run_token = _create_run(client)

    first = client.post("/api/v1/chat", json={"run_token": run_token, "message": "发起一次"})
    new_token = first.json()["created_run_token"]
    assert new_token and new_token != run_token
    assert first.json()["session_turns"] == 1
    service._chat_sessions[new_token].turns = MAX_CHAT_TURNS

    second = client.post("/api/v1/chat", json={"run_token": new_token, "message": "再问"})

    assert second.json()["reason"] == "CHAT_SESSION_LIMIT"


def test_direction_stats_never_mix_sample_and_population_denominators(tmp_path):
    service, _gateway = _service(tmp_path)
    client = TestClient(create_workbench_app(service=service))
    run_token = _create_run(client)
    session = service._chat_session_for(run_token)
    matched = service.get_run(run_token).product_summaries[0].matched_count

    sample, _, _, _ = service._chat_query_stats(
        session, json.dumps({"stat_key": "signal_totals"})
    )
    population, _, _, _ = service._chat_query_stats(
        session, json.dumps({"stat_key": "full_population_totals"})
    )

    sample_row = sample["rows"][0]
    population_row = population["rows"][0]
    assert "matched_total" not in sample_row
    assert sample_row["sample_size"] == sum(
        sample_row[key] for key in ("supporting", "opposing", "conflicting", "insufficient")
    )
    assert "sample_size" not in population_row
    assert population_row["matched_total"] == matched
    assert population_row["matched_total"] == sum(
        population_row[key]
        for key in ("supporting", "opposing", "conflicting", "insufficient")
    )

def test_export_tool_writes_a_local_csv_and_keeps_the_path_off_the_model(tmp_path):
    service, _gateway = _service(tmp_path)
    client = TestClient(create_workbench_app(service=service))
    run_token = _create_run(client)
    session = service._chat_session_for(run_token)

    payload, title, detail, status = service._chat_export_reviews(
        session, json.dumps({"product_number": 1})
    )

    assert status == "ok", detail
    assert title == "导出评论"
    path = Path(session.export_path or "")
    assert path.is_absolute() and path.is_dir()
    header = (path / "reviews.csv").read_text(encoding="utf-8-sig").splitlines()[0]
    assert "参与本次判断" in header
    assert payload["exported"] == 3
    assert any(item.startswith("路径: ") for item in detail)
    assert str(path) not in json.dumps(payload, ensure_ascii=False)


def test_export_budget_is_one_file_per_run(tmp_path):
    service, _gateway = _service(tmp_path)
    client = TestClient(create_workbench_app(service=service))
    run_token = _create_run(client)
    session = service._chat_session_for(run_token)

    first, _title, _detail, first_status = service._chat_export_reviews(
        session, json.dumps({"product_number": 1})
    )
    payload, _title, detail, status = service._chat_export_reviews(
        session, json.dumps({"product_number": 1})
    )

    assert first_status == "ok"
    assert status == "error"
    assert payload["error"] == "EXPORT_ALREADY_DONE"
    assert "已经导出过一次" in detail[0]
    assert len(list((tmp_path / "exports").iterdir())) == 1
    assert payload["path"] == session.export_path
    assert first["exported"] == 3


def test_export_totals_are_queryable_only_after_the_export(tmp_path):
    service, _gateway = _service(tmp_path)
    client = TestClient(create_workbench_app(service=service))
    run_token = _create_run(client)
    session = service._chat_session_for(run_token)

    before, _title, _before_detail, before_status = service._chat_query_stats(
        session, json.dumps({"stat_key": "export_totals"})
    )
    assert before_status == "error" and before["error"] == "NO_EXPORT_YET"

    service._chat_export_reviews(session, json.dumps({"product_number": 1}))
    rows, _title, _detail, status = service._chat_query_stats(
        session, json.dumps({"stat_key": "export_totals"})
    )

    assert status == "ok"
    assert rows["rows"][0]["exported"] == 3
    assert rows["rows"][0]["matched_total"] == 3


def test_chat_turn_surfaces_the_export_path_beside_the_answer(tmp_path):
    service, gateway = _service(tmp_path)
    client = TestClient(create_workbench_app(service=service))
    run_token = _create_run(client)
    gateway.steps = [
        ("tool", "export_reviews", json.dumps({"product_number": 1})),
        (
            "submit",
            _draft(
                review_points=(),
                stats_points=(
                    {
                        "text": "已导出 3 条评论。",
                        "stat_key": "export_totals",
                        "product": PRODUCT_LABEL,
                        "metric": "exported",
                        "value": 3,
                    },
                ),
                not_supported="导出的表需要人工逐条核对,不能直接当结论。",
            ),
        ),
    ]

    body = client.post(
        "/api/v1/chat",
        json={"run_token": run_token, "message": "把评论导出来,我要人工标注"},
    ).json()

    assert body["status"] == "READY", body
    assert body["export_rows"] == 3
    assert body["export_path"].startswith("exports/")
    assert ":" not in body["export_path"]
    assert (tmp_path / body["export_path"] / "reviews.csv").is_file()
    assert body["steps"][0]["tool"] == "export_reviews"
    assert body["steps"][0]["status"] == "ok"
    assert body["stats_points"][0]["stat_key"] == "export_totals"

def test_switching_runs_by_chat_resets_the_export_budget(tmp_path):
    from ecom_review_intelligence.workbench_contracts import WorkbenchRunRequest

    service, _gateway = _service(tmp_path)
    client = TestClient(create_workbench_app(service=service))
    run_token = _create_run(client)
    session = service._chat_session_for(run_token)
    service._chat_export_reviews(session, json.dumps({"product_number": 1}))
    assert session.exports == 1

    request = WorkbenchRunRequest(
        workflow="diagnosis",
        product_codes=("frostline",),
        aspect="noise",
        question="Which noise signals should operations verify first?",
    )
    new_run = service.create_run(request)
    service._switch_chat_session(session, new_run)

    assert session.exports == 0
    assert session.export_path is None
    assert session.export_rows == 0
    payload, _title, _detail, status = service._chat_export_reviews(
        session, json.dumps({"product_number": 1})
    )
    assert status == "ok", payload

def test_card_rows_chat_rows_and_export_mark_the_same_sample(tmp_path):
    import sqlite3 as _sqlite3

    from ecom_review_intelligence.engine import compute_evidence_id, initialize_store

    store = tmp_path / "six.sqlite"
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
    rows = (
        ("noise quiet one", 5.0),
        ("noise quiet two", 5.0),
        ("noise quiet three", 5.0),
        ("noise quiet four", 5.0),
        ("noise quiet five", 5.0),
        ("noise loud six", 1.0),
        ("noise loud seven", 1.0),
    )
    with _sqlite3.connect(store) as connection:
        connection.execute(
            "INSERT INTO products(parent_asin,title) VALUES (?,?)", (product_id, PRODUCT_LABEL)
        )
        for line, (text, rating) in enumerate(rows, start=1):
            connection.execute(
                "INSERT INTO reviews(evidence_id,source_line,parent_asin,text,rating,timestamp,"
                "verified_purchase,helpful_vote) VALUES (?,?,?,?,?,?,?,?)",
                (compute_evidence_id(snapshot, line), line, product_id, text, rating, line, 1, 0),
            )
    service = P6BackedWorkbenchService(
        store_path=store,
        checkpoint_path=checkpoint,
        trace_path=tmp_path / "traces.jsonl",
        config={
            "dataset_snapshot_id": snapshot,
            "contract_sha256": contract,
            "retrieval_limit_per_aspect": 5,
            "model": "deepseek-v4-flash",
            "base_url": "https://api.deepseek.com",
            "config_sha256": "c" * 64,
        },
        product_ids_by_code={"frostline": product_id},
        export_dir=tmp_path / "exports",
        project_root=tmp_path,
    )
    client = TestClient(create_workbench_app(service=service))
    run_token = _create_run(client)
    run = service.get_run(run_token)
    card_alias = run.evidence[0].evidence_alias

    card_texts = sorted(row.text for row in service.get_evidence_rows(run_token, card_alias).rows)
    _aspect, _label, chat_reviews, _matched = service._insight_inputs(run_token, card_alias)
    chat_texts = sorted(text for _id, text in chat_reviews)
    session = service._chat_session_for(run_token)
    service._chat_export_reviews(session, json.dumps({"product_number": 1}))
    import csv

    with open(session.export_path + "/reviews.csv", encoding="utf-8-sig", newline="") as handle:
        exported = [
            row["评论原文"] for row in csv.DictReader(handle) if row["参与本次判断"] == "是"
        ]

    assert len(card_texts) == 5
    assert any("loud" in text for text in card_texts), card_texts
    assert chat_texts == card_texts
    assert sorted(exported) == card_texts
