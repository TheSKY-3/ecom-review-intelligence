"""The container entry point must refuse public exposure unless it is asked for."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ecom_review_intelligence.workbench_service import SyntheticWorkbenchService
from ecom_review_intelligence.workbench_web import create_workbench_app

serve_demo = importlib.import_module("scripts.serve_demo")


def _parse(*argv: str):
    return serve_demo.build_parser().parse_args(list(argv))


def test_non_loopback_bind_needs_an_explicit_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("WORKBENCH_ALLOWED_HOSTS", raising=False)

    with pytest.raises(SystemExit):
        serve_demo.validate(_parse("--host", "0.0.0.0"))

    serve_demo.validate(_parse("--host", "0.0.0.0", "--allow-non-loopback"))


def test_loopback_bind_needs_no_flag() -> None:
    serve_demo.validate(_parse("--host", "127.0.0.1"))


def test_local_mode_needs_the_data_acknowledgement() -> None:
    with pytest.raises(SystemExit):
        serve_demo.validate(_parse("--mode", "local"))

    serve_demo.validate(_parse("--mode", "local", "--acknowledge-local-data"))


def test_bad_port_is_refused() -> None:
    with pytest.raises(SystemExit):
        serve_demo.validate(_parse("--port", "70000"))


def test_allowed_hosts_merge_flag_and_environment(monkeypatch) -> None:
    monkeypatch.setenv("WORKBENCH_ALLOWED_HOSTS", "demo.example, second.example")

    hosts = serve_demo.resolve_allowed_hosts(_parse("--allowed-host", "third.example"))

    assert hosts == ("third.example", "demo.example", "second.example")


def test_app_accepts_only_named_host_headers() -> None:
    app = create_workbench_app(
        SyntheticWorkbenchService(), extra_allowed_hosts=("demo.example",)
    )
    client = TestClient(app)

    assert client.get("/health", headers={"host": "demo.example"}).status_code == 200
    assert client.get("/health", headers={"host": "127.0.0.1:8000"}).status_code == 200
    rejected = client.get("/health", headers={"host": "attacker.example"})
    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "INVALID_HOST"


def test_synthetic_mode_starts_without_any_review_data(tmp_path: Path) -> None:
    service = SyntheticWorkbenchService()

    run = TestClient(create_workbench_app(service)).post(
        "/api/v1/runs",
        json={
            "workflow": "triage",
            "product_codes": ["frostline", "northstar"],
            "product_query": None,
            "aspect": "noise",
            "question": "先查哪个商品的噪音问题？",
        },
    )

    assert run.status_code == 201
    assert run.json()["evidence"]


def test_wildcard_host_allows_subdomains_only() -> None:
    app = create_workbench_app(
        SyntheticWorkbenchService(), extra_allowed_hosts=("*.hf.space",)
    )
    client = TestClient(app)

    assert client.get("/health", headers={"host": "demo-abc.hf.space"}).status_code == 200
    assert client.get("/health", headers={"host": "hf.space"}).status_code == 400
    assert client.get("/health", headers={"host": "attacker-hf.space"}).status_code == 400
    assert (
        client.get("/health", headers={"host": "evil.hf.space.example.com"}).status_code
        == 400
    )


def test_port_comes_from_the_platform_when_no_flag_is_given(monkeypatch) -> None:
    monkeypatch.setenv("PORT", "10000")
    assert serve_demo.resolve_port(_parse()) == 10000

    monkeypatch.setenv("PORT", "7860")
    assert serve_demo.resolve_port(_parse("--port", "9000")) == 9000


def test_a_bad_port_value_is_refused(monkeypatch) -> None:
    monkeypatch.setenv("PORT", "not-a-port")
    with pytest.raises(SystemExit):
        serve_demo.resolve_port(_parse())

    monkeypatch.setenv("PORT", "70000")
    with pytest.raises(SystemExit):
        serve_demo.resolve_port(_parse())


def test_local_mode_explains_a_missing_private_store(tmp_path) -> None:
    with pytest.raises(SystemExit) as error:
        serve_demo._service_for(tmp_path, "local", None)

    message = str(error.value)
    assert "private category store is missing" in message
    assert "--mode bundled" in message
