"""A visitor's API key is used for one call and reaches no store, log, or response."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from ecom_review_intelligence.agent_insight import InsightDraft
from ecom_review_intelligence.workbench_service import (
    P6BackedWorkbenchService,
    SyntheticWorkbenchService,
)
from ecom_review_intelligence.workbench_web import (
    INSIGHT_KEY_HEADER,
    InsightRateLimiter,
    create_workbench_app,
)

_VISITOR_KEY = "test-visitor-key-0123456789abcdef"
_CONFIG = {"model": "deepseek-v4-flash", "base_url": "https://api.deepseek.com"}


def _draft_for(payload) -> InsightDraft:
    reviews = payload["reviews"]
    return InsightDraft(
        reviews=tuple(
            {
                "evidence_alias": review["id"],
                "relevant": True,
                "note": "提到噪音",
                "quote": review["text"][:20],
            }
            for review in reviews
        ),
        supported="样本中提到运行噪音。",
        supported_refs=tuple(review["id"] for review in reviews),
        specifics=(),
        not_supported="样本无法说明噪音发生的比例。",
    )


class _StubGateway:
    def __init__(self, key: str) -> None:
        self.key = key
        self.calls = 0

    def analyse(self, payload):
        self.calls += 1
        return _draft_for(payload)


def _tiny_store(tmp_path: Path) -> Path:
    """A three-product store, because the service reads product labels at build time."""

    import sqlite3

    from ecom_review_intelligence.engine import initialize_store

    store = tmp_path / "tiny.sqlite"
    initialize_store(
        store,
        snapshot_id="snapshot-byo",
        contract_sha256="contract-byo",
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


def _service(tmp_path: Path, *, user_factory=None) -> P6BackedWorkbenchService:
    service = P6BackedWorkbenchService(
        store_path=_tiny_store(tmp_path),
        checkpoint_path=tmp_path / "checkpoints.sqlite",
        config=_CONFIG,
        product_ids_by_code={"frostline": "A", "northstar": "B", "harbor": "C"},
        verdict_path=tmp_path / "verdicts.jsonl",
        insight_gateway_factory=lambda: None,
        user_insight_gateway_factory=user_factory,
    )
    # The one helper that would read the store is replaced: this test is about the
    # key path, not about retrieval.
    service._insight_inputs = lambda run_token, card_alias: (
        "noise",
        "商品 A",
        [("E1", "the unit is loud at night"), ("E2", "hums quietly")],
        2,
    )
    return service


def _client(service, *, rate_limit=(10, 600.0)) -> TestClient:
    return TestClient(create_workbench_app(service, insight_rate_limit=rate_limit))


def _post(client: TestClient, *, key: str | None = _VISITOR_KEY, alias: str = "EV-01"):
    headers = {"Content-Type": "application/json"}
    if key is not None:
        headers[INSIGHT_KEY_HEADER] = key
    return client.post(
        "/api/v1/insights",
        json={"run_token": "token-1", "card_alias": alias},
        headers=headers,
    )


def test_visitor_key_builds_one_gateway_and_is_never_echoed(tmp_path: Path) -> None:
    seen: list[str] = []

    def factory(key: str):
        seen.append(key)
        return _StubGateway(key)

    service = _service(tmp_path, user_factory=factory)
    client = _client(service)

    response = _post(client)

    assert response.status_code == 200
    assert response.json()["status"] == "READY"
    assert seen == [_VISITOR_KEY]
    assert _VISITOR_KEY not in response.text

    for name in ("verdicts.jsonl", "p7_workbench_traces.jsonl"):
        path = tmp_path / name
        if path.exists():
            assert _VISITOR_KEY not in path.read_text(encoding="utf-8")


def test_cached_result_does_not_build_another_gateway(tmp_path: Path) -> None:
    seen: list[str] = []

    def factory(key: str):
        seen.append(key)
        return _StubGateway(key)

    client = _client(_service(tmp_path, user_factory=factory))

    assert _post(client).json()["status"] == "READY"
    assert _post(client).json()["status"] == "READY"

    assert seen == [_VISITOR_KEY]


def test_without_any_key_the_step_reports_not_configured(tmp_path: Path) -> None:
    client = _client(_service(tmp_path))

    response = _post(client, key=None)

    assert response.status_code == 200
    assert response.json()["status"] == "UNAVAILABLE"
    assert response.json()["reason"] == "MODEL_NOT_CONFIGURED"


def test_invalid_key_shape_is_rejected_without_echo(tmp_path: Path) -> None:
    client = _client(_service(tmp_path))

    response = _post(client, key="not a key with spaces")

    assert response.status_code == 422
    assert "not a key" not in response.text
    assert response.json()["error"]["code"] == "INVALID_INPUT"


def test_repeated_calls_hit_the_rate_limit(tmp_path: Path) -> None:
    seen: list[str] = []

    def factory(key: str):
        seen.append(key)
        return _StubGateway(key)

    client = _client(_service(tmp_path, user_factory=factory), rate_limit=(2, 600.0))

    # Different cards, so each call is a fresh reading rather than a cached one.
    assert _post(client, alias="EV-01").status_code == 200
    assert _post(client, alias="EV-02").status_code == 200
    third = _post(client, alias="EV-03")

    assert third.status_code == 429
    assert third.json()["error"]["code"] == "RATE_LIMITED"
    assert len(seen) == 2, "a limited request must not build a gateway"


def test_synthetic_mode_hides_the_step_and_refuses_the_request() -> None:
    client = TestClient(create_workbench_app(SyntheticWorkbenchService()))

    page = client.get("/").text
    response = _post(client)

    assert 'data-insight-byo="false"' in page
    assert response.status_code == 404


def test_byo_flag_is_on_for_a_local_service(tmp_path: Path) -> None:
    client = _client(_service(tmp_path))

    page = client.get("/").text

    assert 'data-insight-byo="true"' in page
    assert "自己的 DeepSeek API Key" in page


def test_limiter_is_per_client_and_slides() -> None:
    now = [1000.0]
    limiter = InsightRateLimiter(max_calls=2, window_seconds=60, clock=lambda: now[0])

    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("1.2.3.4") is False
    assert limiter.allow("5.6.7.8") is True

    now[0] += 61
    assert limiter.allow("1.2.3.4") is True
