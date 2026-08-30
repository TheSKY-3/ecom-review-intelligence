import json
import re
from importlib import import_module
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient


def test_workbench_exposes_closed_synthetic_health_response():
    module = import_module("ecom_review_intelligence.workbench_web")
    client = TestClient(module.create_workbench_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": "p7-workbench-health-v1",
        "status": "ok",
        "mode": "synthetic",
        "live_enabled": False,
    }


def _client():
    module = import_module("ecom_review_intelligence.workbench_web")
    return TestClient(module.create_workbench_app())


def _request(workflow="diagnosis", products=None, **updates):
    value = {
        "workflow": workflow,
        "product_codes": products if products is not None else ["frostline"],
        "product_query": None,
        "aspect": "noise",
        "question": "Which review signals should operations verify first?",
    }
    value.update(updates)
    return value


def _assert_public_safe(payload):
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "DEEPSEEK_API_KEY" not in serialized
    assert not re.search(r"[A-Za-z]:[\\/]", serialized)
    assert not re.search(r"(?<![A-Z0-9])B[A-Z0-9]{9}(?![A-Z0-9])", serialized)
    assert not re.search(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", serialized)


def test_create_and_fetch_diagnosis_run_uses_closed_public_dto():
    client = _client()

    created = client.post("/api/v1/runs", json=_request())

    assert created.status_code == 201
    payload = created.json()
    UUID(payload["run_token"])
    assert set(payload) == {
        "schema_version",
        "run_token",
        "run_alias",
        "status",
        "workflow",
        "title",
        "summary",
        "evidence_count",
        "signal_totals",
        "degraded",
        "next_action",
        "products",
        "evidence",
        "comparison_rows",
        "triage_items",
        "clarification",
    }
    assert re.fullmatch(r"RUN-[0-9A-F]{8}", payload["run_alias"])
    assert payload["status"] == "VERIFIED_RESULT"
    assert payload["workflow"] == "diagnosis"
    assert payload["evidence_count"] == len(payload["evidence"]) > 0
    assert payload["degraded"] is False
    _assert_public_safe(payload)

    fetched = client.get(f"/api/v1/runs/{payload['run_token']}")
    assert fetched.status_code == 200
    assert fetched.json() == payload


def test_evidence_detail_is_unavailable_in_synthetic_mode():
    client = _client()
    created = client.post("/api/v1/runs", json=_request()).json()

    detail = client.get(f"/api/v1/runs/{created['run_token']}/evidence/EV-01")

    assert detail.status_code == 404
    assert detail.json()["error"]["code"] == "EVIDENCE_DETAIL_UNAVAILABLE"


def test_comparison_and_triage_have_workflow_specific_outputs():
    client = _client()

    comparison = client.post(
        "/api/v1/runs",
        json=_request("comparison", ["frostline", "northstar"]),
    ).json()
    triage = client.post(
        "/api/v1/runs",
        json=_request("triage", ["frostline", "northstar"]),
    ).json()

    assert comparison["comparison_rows"] and not comparison["triage_items"]
    assert all(
        set(row) == {
            "product_code",
            "signal",
            "direction",
            "strength",
            "evidence_count",
            "comparable",
            "insufficient_reason",
        }
        for row in comparison["comparison_rows"]
    )
    assert triage["triage_items"] and not triage["comparison_rows"]
    _assert_public_safe(comparison)
    _assert_public_safe(triage)


def test_non_noise_aspect_controls_all_business_output_wording():
    client = _client()

    result = client.post(
        "/api/v1/runs",
        json=_request("triage", ["frostline", "northstar"], aspect="reliability"),
    ).json()

    serialized = json.dumps(result, ensure_ascii=False).casefold()
    assert result["status"] == "VERIFIED_RESULT"
    assert all(item["aspect"] == "reliability" for item in result["evidence"])
    assert "reliability" in serialized
    assert "noise" not in serialized


def test_ambiguous_product_query_can_resume_once():
    client = _client()
    created = client.post(
        "/api/v1/runs",
        json=_request(products=[], product_query="countertop"),
    )

    assert created.status_code == 201
    pending = created.json()
    assert pending["status"] == "CLARIFICATION_REQUIRED"
    assert len(pending["clarification"]["choices"]) == 2

    resumed = client.post(
        f"/api/v1/runs/{pending['run_token']}/resume",
        json={"selected_product_codes": ["frostline"]},
    )

    assert resumed.status_code == 200
    assert resumed.json()["status"] == "VERIFIED_RESULT"
    second = client.post(
        f"/api/v1/runs/{pending['run_token']}/resume",
        json={"selected_product_codes": ["frostline"]},
    )
    assert second.status_code == 409
    assert second.json() == {
        "error": {"code": "STATE_ERROR", "message": "Run cannot be resumed."}
    }


def test_diagnosis_resume_rejects_duplicate_or_multiple_product_choices():
    client = _client()
    for selected in (["frostline", "frostline"], ["frostline", "harbor"]):
        pending = client.post(
            "/api/v1/runs",
            json=_request(products=[], product_query="countertop"),
        ).json()
        response = client.post(
            f"/api/v1/runs/{pending['run_token']}/resume",
            json={"selected_product_codes": selected},
        )
        assert response.status_code == 422
        assert response.json() == {
            "error": {"code": "INVALID_INPUT", "message": "Request validation failed."}
        }


def test_invalid_input_and_unknown_token_fail_with_fixed_safe_errors():
    client = _client()
    invalid = client.post(
        "/api/v1/runs",
        json=_request(question="C:\\private\\secret", aspect="unknown"),
    )
    missing = client.get("/api/v1/runs/00000000-0000-4000-8000-000000000000")

    assert invalid.status_code == 422
    assert invalid.json() == {
        "error": {"code": "INVALID_INPUT", "message": "Request validation failed."}
    }
    assert "private" not in invalid.text
    assert missing.status_code == 404
    assert missing.json() == {
        "error": {"code": "RUN_NOT_FOUND", "message": "Run was not found."}
    }


def test_unknown_service_exception_is_redacted():
    module = import_module("ecom_review_intelligence.workbench_web")

    class ExplodingService:
        def create_run(self, request):
            del request
            raise RuntimeError("C:\\private\\store.sqlite DEEPSEEK_API_KEY=secret")

    client = TestClient(
        module.create_workbench_app(service=ExplodingService()),
        raise_server_exceptions=False,
    )
    response = client.post("/api/v1/runs", json=_request())

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "INTERNAL_ERROR", "message": "Request could not be completed."}
    }
    assert "private" not in response.text and "secret" not in response.text


def test_workbench_page_is_task_first_and_uses_only_local_assets():
    client = _client()

    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-security-policy"] == (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'"
    )
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "运营工作台" in response.text
    assert 'data-workflow="diagnosis"' in response.text
    assert 'data-workflow="comparison"' in response.text
    assert 'data-workflow="triage"' in response.text
    assert 'id="run-form"' in response.text
    assert 'id="result-panel"' in response.text
    assert 'id="run-alias"' in response.text
    assert 'id="workbench-mode">SYNTHETIC<' in response.text
    assert 'href="/static/workbench.css?v=' in response.text
    assert 'src="/static/workbench.js?v=' in response.text
    assert "https://" not in response.text and "http://" not in response.text


def test_page_and_health_show_the_injected_backend_mode():
    module = import_module("ecom_review_intelligence.workbench_web")

    class LocalModeService:
        mode = "local"

    client = TestClient(module.create_workbench_app(service=LocalModeService()))

    assert client.get("/health").json()["mode"] == "local"
    page = client.get("/").text
    assert 'id="workbench-mode">LOCAL<' in page
    assert "受控本地证据" in page
    assert "合成回放" not in page
    assert "受控本地商品 A" in page
    assert "Frostline Countertop" not in page


def test_local_frontend_assets_exist_and_do_not_call_external_hosts():
    client = _client()

    stylesheet = client.get("/static/workbench.css")
    script = client.get("/static/workbench.js")

    assert stylesheet.status_code == 200 and "text/css" in stylesheet.headers["content-type"]
    assert script.status_code == 200 and "javascript" in script.headers["content-type"]
    assert "https://" not in stylesheet.text + script.text
    assert "http://" not in stylesheet.text + script.text
    assert "fetch(" in script.text
    assert 'querySelector("#run-alias").textContent = payload.run_alias' in script.text

    favicon = client.get("/favicon.ico")
    assert favicon.status_code == 204 and favicon.content == b""


def test_browser_validation_identity_covers_local_adapter_agent_dependencies():
    validator = (
        Path(__file__).parents[1] / "scripts" / "validate_phase7_workbench.cjs"
    ).read_text(encoding="utf-8")

    for relative in (
        "src/ecom_review_intelligence/agent_cli.py",
        "src/ecom_review_intelligence/agent_contracts.py",
        "src/ecom_review_intelligence/agent_graph.py",
        "src/ecom_review_intelligence/agent_policy.py",
        "src/ecom_review_intelligence/agent_tools.py",
        "src/ecom_review_intelligence/engine.py",
        "src/ecom_review_intelligence/model_gateway.py",
    ):
        assert f'"{relative}"' in validator


def test_browser_validation_invalidates_old_pass_before_loading_playwright():
    validator = (
        Path(__file__).parents[1] / "scripts" / "validate_phase7_workbench.cjs"
    ).read_text(encoding="utf-8")

    invalidate = validator.index("invalidateArtifacts();")
    playwright = validator.index('require("playwright")')

    assert invalidate < playwright
    assert validator.index("async function run()") < invalidate


def test_recursive_public_scanner_rejects_sensitive_values_and_keys():
    module = import_module("ecom_review_intelligence.workbench_web")
    unsafe_values = [
        {"nested": [{"value": "B012345678"}]},
        {"nested": [{"value": "b012345678"}]},
        {"nested": {"value": "a" * 64}},
        {"nested": {"value": "A" * 64}},
        {"nested": {"value": "C:\\private\\store.sqlite"}},
        {"nested": {"value": "\\\\server\\private\\store.sqlite"}},
        {"nested": {"value": "/etc/passwd"}},
        {"nested": {"value": "/data/processed/store.sqlite"}},
        {"nested": {"value": "/mnt/private/store.sqlite"}},
        {"nested": {"value": "/usr/local/private.txt"}},
        {"nested": {"value": "Bearer secret-token"}},
        {"api_key": "secret"},
        {"client_secret": "secret"},
    ]

    for value in unsafe_values:
        try:
            module.assert_public_workbench_safe(value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe value was accepted: {list(value)}")


def test_host_header_is_restricted_to_loopback_and_test_client():
    client = _client()

    rejected = client.get("/health", headers={"host": "attacker.example"})
    accepted = client.get("/health", headers={"host": "127.0.0.1:8765"})

    assert rejected.status_code == 400
    assert rejected.json() == {
        "error": {"code": "INVALID_HOST", "message": "Host is not allowed."}
    }
    assert accepted.status_code == 200


def test_backend_sensitive_response_is_blocked_before_serialization():
    module = import_module("ecom_review_intelligence.workbench_web")
    contracts = import_module("ecom_review_intelligence.workbench_contracts")

    class UnsafeService:
        def create_run(self, request):
            return contracts.WorkbenchRun(
                run_token="00000000-0000-4000-8000-000000000001",
                status="VERIFIED_RESULT",
                workflow=request.workflow,
                title="Unsafe",
                summary="C:\\private\\store.sqlite",
                evidence_count=0,
                degraded=False,
                next_action="none",
            )

    client = TestClient(
        module.create_workbench_app(service=UnsafeService()),
        raise_server_exceptions=False,
    )
    response = client.post("/api/v1/runs", json=_request())

    assert response.status_code == 500
    assert "private" not in response.text
