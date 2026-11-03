"""Service-level tests for the D2 parallel card-reader team.

The harness mirrors tests/test_insight_byo_key.py: a tiny three-product store,
``_insight_inputs`` and ``get_run`` replaced, and a scripted gateway. What is
under test here is the team path itself -- fan-out, per-card isolation,
deterministic order, caching -- not retrieval."""

from __future__ import annotations

import threading
from pathlib import Path

from fastapi.testclient import TestClient

from ecom_review_intelligence.agent_contracts import Workflow
from ecom_review_intelligence.agent_insight import InsightDraft
from ecom_review_intelligence.model_gateway import GatewayError
from ecom_review_intelligence.workbench_contracts import (
    EvidenceView,
    WorkbenchRun,
)
from ecom_review_intelligence.workbench_service import P6BackedWorkbenchService
from ecom_review_intelligence.workbench_web import INSIGHT_KEY_HEADER, create_workbench_app

_VISITOR_KEY = "test-visitor-key-0123456789abcdef"
_CONFIG = {"model": "deepseek-v4-flash", "base_url": "https://api.deepseek.com"}
_REVIEWS = [
    ("E1", "the unit is loud at night"),
    ("E2", "hums quietly while making ice"),
    ("E3", "makes loud screeching sounds"),
]


def _tiny_store(tmp_path: Path) -> Path:
    import sqlite3

    from ecom_review_intelligence.engine import initialize_store

    store = tmp_path / "tiny.sqlite"
    initialize_store(
        store,
        snapshot_id="snapshot-team",
        contract_sha256="contract-team",
        aspect_terms={"noise": ("noise", "loud")},
        polarity_terms={"opposing": ("loud",)},
    )
    with sqlite3.connect(store) as connection:
        connection.executemany(
            "INSERT INTO products (parent_asin, title) VALUES (?, ?)",
            [("A", "商品 A"), ("B", "商品 B"), ("C", "商品 C")],
        )
        connection.commit()
    return store


def _run_view(token: str = "token-team") -> WorkbenchRun:
    return WorkbenchRun(
        run_token=token,
        run_alias="R-TEAM",
        status="VERIFIED_RESULT",
        workflow=Workflow.DIAGNOSIS,
        title="测试运行",
        summary="测试",
        evidence_count=3,
        degraded=False,
        next_action="继续",
        evidence=tuple(
            EvidenceView(
                evidence_alias=f"EV-0{index}",
                product_code="frostline",
                aspect="noise",
                polarity="supporting",
                strength="MODERATE",
                excerpt="样例",
            )
            for index in (1, 2, 3)
        ),
    )


class _TeamGateway:
    """Scripted team reader: records threads and payloads, optional per-card failures."""

    def __init__(self, fail_aliases: set[str] | None = None, *, bad_quote_alias: str | None = None):
        self.fail_aliases = fail_aliases or set()
        self.bad_quote_alias = bad_quote_alias
        self.threads: set[int] = set()
        self.payloads: list[dict] = []
        self.lock = threading.Lock()

    def analyse(self, payload):
        with self.lock:
            self.threads.add(threading.get_ident())
            self.payloads.append(dict(payload))
        alias = payload["reviews"][0]["id"]
        if alias in self.fail_aliases:
            raise GatewayError("MODEL_PROVIDER_ERROR", "The model provider request failed.")
        reviews = payload["reviews"]
        if alias == self.bad_quote_alias:
            quote = "not a substring of any review"
        else:
            quote = reviews[0]["text"][:20]
        return InsightDraft(
            reviews=tuple(
                {
                    "evidence_alias": review["id"],
                    "relevant": True,
                    "note": "提到噪音",
                    "quote": quote if review["id"] == reviews[0]["id"] else review["text"][:20],
                }
                for review in reviews
            ),
            supported="样本中提到运行噪音。",
            supported_refs=tuple(review["id"] for review in reviews),
            specifics=(),
            not_supported="样本无法说明噪音发生的比例。",
        )


def _service(tmp_path: Path, gateway, *, user_factory=None) -> P6BackedWorkbenchService:
    service = P6BackedWorkbenchService(
        store_path=_tiny_store(tmp_path),
        checkpoint_path=tmp_path / "checkpoints.sqlite",
        config=_CONFIG,
        product_ids_by_code={"frostline": "A", "northstar": "B", "harbor": "C"},
        verdict_path=tmp_path / "verdicts.jsonl",
        insight_gateway_factory=lambda: gateway,
        user_insight_gateway_factory=user_factory,
        chat_gateway_factory=lambda: None,
    )

    def _inputs(run_token: str, card_alias: str):
        # Per-card review ids, so the scripted gateway can target one card.
        number = card_alias[-1]
        return (
            "noise",
            "商品 A",
            [(f"C{number}-{index}", text) for index, text in _REVIEWS],
            3,
        )

    service._insight_inputs = _inputs  # type: ignore[method-assign]
    service.get_run = lambda run_token: _run_view(run_token)  # type: ignore[method-assign]
    return service


def test_team_reads_every_card_in_run_order(tmp_path: Path) -> None:
    # True concurrency is proven deterministically with a barrier in
    # tests/test_evidence_team.py; the stub here is too fast to observe it.
    gateway = _TeamGateway()
    service = _service(tmp_path, gateway)

    view = service.run_parallel_insights("token-team")

    assert [card.card_alias for card in view.cards] == ["EV-01", "EV-02", "EV-03"]
    assert all(card.status == "READY" for card in view.cards)
    assert view.summary.total == 3 and view.summary.ready == 3
    assert view.summary.model_calls == 3
    assert len(gateway.threads) >= 1


def test_one_failed_reader_is_isolated_and_others_still_land(tmp_path: Path) -> None:
    gateway = _TeamGateway(fail_aliases={"C1-E1"})
    service = _service(tmp_path, gateway)

    view = service.run_parallel_insights("token-team")

    statuses = {card.card_alias: card.status for card in view.cards}
    assert statuses == {
        "EV-01": "UNAVAILABLE",
        "EV-02": "READY",
        "EV-03": "READY",
    }
    failed = view.cards[0]
    assert failed.reason == "MODEL_PROVIDER_ERROR"
    assert failed.model_calls == 1
    assert view.summary.unavailable == 1 and view.summary.ready == 2


def test_a_validation_failure_is_not_cached(tmp_path: Path) -> None:
    gateway = _TeamGateway(bad_quote_alias="C2-E1")
    service = _service(tmp_path, gateway)

    view = service.run_parallel_insights("token-team")

    statuses = {card.card_alias: card.status for card in view.cards}
    assert statuses["EV-02"] == "VALIDATION_FAILED"
    assert view.summary.validation_failed == 1
    cached = {key[1] for key in service._insight_cache}
    assert cached == {"EV-01", "EV-03"}, "only READY readings may be cached"


def test_without_a_gateway_every_card_reports_not_configured(tmp_path: Path) -> None:
    service = _service(tmp_path, _TeamGateway())
    service._insight_gateway = None  # type: ignore[attr-defined]
    service._insight_resolved = True  # type: ignore[attr-defined]

    view = service.run_parallel_insights("token-team")

    assert all(card.status == "UNAVAILABLE" for card in view.cards)
    assert {card.reason for card in view.cards} == {"MODEL_NOT_CONFIGURED"}
    assert view.summary.model_calls == 0


def test_endpoint_returns_the_team_view_and_hides_the_key(tmp_path: Path) -> None:
    gateway = _TeamGateway()
    client = TestClient(create_workbench_app(_service(tmp_path, gateway)))

    response = client.post("/api/v1/parallel-insights", json={"run_token": "token-team"})

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "p7-parallel-insight-v1"
    assert [card["card_alias"] for card in body["cards"]] == ["EV-01", "EV-02", "EV-03"]
    assert body["summary"]["ready"] == 3


def test_team_call_with_a_visitor_key_uses_only_that_gateway(tmp_path: Path) -> None:
    seen: list[str] = []

    def user_factory(key: str, model: str):
        seen.append(key)
        return _TeamGateway()

    client = TestClient(
        create_workbench_app(_service(tmp_path, _TeamGateway(), user_factory=user_factory))
    )

    response = client.post(
        "/api/v1/parallel-insights",
        json={"run_token": "token-team"},
        headers={INSIGHT_KEY_HEADER: _VISITOR_KEY},
    )

    assert response.status_code == 200
    assert response.json()["summary"]["ready"] == 3
    assert seen == [_VISITOR_KEY]
    assert _VISITOR_KEY not in response.text


def test_one_team_call_is_one_rate_limit_hit(tmp_path: Path) -> None:
    client = TestClient(
        create_workbench_app(_service(tmp_path, _TeamGateway()), insight_rate_limit=(1, 600.0))
    )

    first = client.post("/api/v1/parallel-insights", json={"run_token": "token-team"})
    second = client.post("/api/v1/parallel-insights", json={"run_token": "token-team"})

    assert first.status_code == 200
    assert second.status_code == 429
