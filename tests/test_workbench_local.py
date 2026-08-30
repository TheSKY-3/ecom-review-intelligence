import hashlib
import io
import json
import re
import sqlite3
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

import ecom_review_intelligence.workbench_regression as regression
from ecom_review_intelligence.engine import compute_evidence_id, initialize_store
from ecom_review_intelligence.workbench_service import P6BackedWorkbenchService
from ecom_review_intelligence.workbench_web import create_workbench_app


def _local_fixture(tmp_path):
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
    private_product_id = "LOCAL_PRIVATE_PRODUCT"
    private_review = "noise loud private review content"
    evidence_id = compute_evidence_id(snapshot, 1)
    with sqlite3.connect(store) as connection:
        connection.execute(
            "INSERT INTO products(parent_asin,title) VALUES (?,?)",
            (private_product_id, "Private local title"),
        )
        connection.execute(
            "INSERT INTO reviews(evidence_id,source_line,parent_asin,text,rating,timestamp,verified_purchase,helpful_vote) VALUES (?,?,?,?,?,?,?,?)",
            (evidence_id, 1, private_product_id, private_review, 1.0, 1, 1, 0),
        )
        connection.execute(
            "INSERT INTO reviews_fts(rowid,text) SELECT rowid,text FROM reviews"
        )
    config = {
        "dataset_snapshot_id": snapshot,
        "contract_sha256": contract,
        "retrieval_limit_per_aspect": 5,
        "model": "deterministic-local-workbench",
        "config_sha256": "c" * 64,
    }
    return store, checkpoint, config, private_product_id, private_review, evidence_id


def test_p6_backed_api_uses_real_graph_and_sqlite_without_public_local_values(tmp_path):
    store, checkpoint, config, private_product_id, private_review, evidence_id = (
        _local_fixture(tmp_path)
    )
    service = P6BackedWorkbenchService(
        store_path=store,
        checkpoint_path=checkpoint,
        trace_path=tmp_path / "workbench-traces.jsonl",
        config=config,
        product_ids_by_code={"frostline": private_product_id},
        uuid_factory=lambda: UUID("00000000-0000-4000-8000-000000000001"),
    )
    client = TestClient(create_workbench_app(service=service))

    response = client.post(
        "/api/v1/runs",
        json={
            "workflow": "diagnosis",
            "product_codes": ["frostline"],
            "product_query": None,
            "aspect": "noise",
            "question": "Which signal should operations verify?",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    serialized = json.dumps(payload, ensure_ascii=False)
    assert payload["status"] == "VERIFIED_RESULT"
    assert payload["evidence_count"] == 1
    assert payload["evidence"][0]["aspect"] == "noise"
    assert payload["products"][0]["label"] == "受控本地商品 A"
    assert payload["run_alias"] == "RUN-00000000"
    assert private_product_id not in serialized
    assert private_review not in serialized
    assert evidence_id not in serialized
    assert str(store) not in serialized
    assert service.mode == "local"
    traces = (tmp_path / "workbench-traces.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(traces) == 1
    local_trace = json.loads(traces[0])
    assert local_trace["schema_version"] == "p6-agent-trace-v1"
    assert local_trace["local_product_map"]
    assert local_trace["local_evidence_ids"] == [evidence_id]


def test_local_regression_entry_writes_ignored_detail_shape_and_safe_stdout(
    tmp_path, monkeypatch
):
    store, checkpoint, config, private_product_id, _, _ = _local_fixture(tmp_path)
    monkeypatch.setattr(
        regression,
        "load_local_agent_resources",
        lambda root: (store, checkpoint, config, {"frostline": private_product_id}),
    )
    identity = {
        "git_revision": "d" * 40,
        "source_sha256": "e" * 64,
        "source_dirty": False,
        "source_paths": ["src/ecom_review_intelligence/workbench_regression.py"],
    }
    monkeypatch.setattr(regression, "workbench_code_identity", lambda root: identity)
    output, errors = io.StringIO(), io.StringIO()

    code = regression.local_regression_main(
        ["--project-root", str(tmp_path), "--workflow", "diagnosis"],
        stdout=output,
        stderr=errors,
    )

    assert code == 0 and errors.getvalue() == ""
    public = json.loads(output.getvalue())
    assert public == {
        "schema_version": "p7-local-regression-cli-v1",
        "status": "PASSED",
        "workflow_count": 1,
    }
    receipt = json.loads(
        (tmp_path / "artifacts/evals/phase-7-local-regression.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["gates"]["p6_graph_sqlite_path_completed"] is True
    assert receipt["observations"][0]["evidence_count"] == 1
    assert receipt["git_revision"] == identity["git_revision"]
    assert receipt["source_sha256"] == identity["source_sha256"]
    assert receipt["source_dirty"] is False
    assert receipt["source_paths"] == identity["source_paths"]
    assert receipt["dataset_snapshot_id"] == config["dataset_snapshot_id"]
    assert receipt["contract_sha256"] == config["contract_sha256"]
    assert receipt["config_sha256"] == config["config_sha256"]


def test_local_regression_source_identity_matches_browser_validation_source_set():
    root = Path(__file__).parents[1]
    validator = (root / "scripts/validate_phase7_workbench.cjs").read_text(encoding="utf-8")
    source_block = validator.split("const sources = [", 1)[1].split("];", 1)[0]
    browser_sources = re.findall(r'"([^"]+)"', source_block)
    digest = hashlib.sha256()
    for relative in browser_sources:
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update((root / relative).read_bytes() + b"\0")

    identity = regression.workbench_code_identity(root)

    assert identity["source_paths"] == browser_sources
    assert identity["source_sha256"] == digest.hexdigest()


def test_local_regression_refuses_pass_for_dirty_source(tmp_path, monkeypatch):
    store, checkpoint, config, private_product_id, _, _ = _local_fixture(tmp_path)
    monkeypatch.setattr(
        regression,
        "load_local_agent_resources",
        lambda root: (store, checkpoint, config, {"frostline": private_product_id}),
    )
    monkeypatch.setattr(
        regression,
        "workbench_code_identity",
        lambda root: {
            "git_revision": "d" * 40,
            "source_sha256": "e" * 64,
            "source_dirty": True,
            "source_paths": [],
        },
    )
    output, errors = io.StringIO(), io.StringIO()

    code = regression.local_regression_main(
        ["--project-root", str(tmp_path), "--workflow", "diagnosis"],
        stdout=output,
        stderr=errors,
    )

    assert code == 3 and output.getvalue() == ""
    assert json.loads(errors.getvalue())["status"] == "PREREQUISITE_FAILED"
    assert not (tmp_path / "artifacts/evals/phase-7-local-regression.json").exists()


def test_evidence_detail_returns_review_rows_without_private_identifiers(tmp_path):
    store, checkpoint, config, private_product_id, private_review, _ = _local_fixture(tmp_path)
    service = P6BackedWorkbenchService(
        store_path=store,
        checkpoint_path=checkpoint,
        trace_path=tmp_path / "workbench-traces.jsonl",
        config=config,
        product_ids_by_code={"frostline": private_product_id},
        uuid_factory=lambda: UUID("00000000-0000-4000-8000-000000000004"),
    )
    client = TestClient(create_workbench_app(service=service))
    created = client.post(
        "/api/v1/runs",
        json={
            "workflow": "diagnosis",
            "product_codes": ["frostline"],
            "product_query": None,
            "aspect": "noise",
            "question": "Which signal should operations verify?",
        },
    )
    token = created.json()["run_token"]

    detail = client.get(f"/api/v1/runs/{token}/evidence/EV-01")

    assert detail.status_code == 200
    payload = detail.json()
    serialized = json.dumps(payload, ensure_ascii=False)
    assert payload["schema_version"] == "p7-evidence-detail-v1"
    assert payload["card_alias"] == "EV-01"
    assert payload["product_code"] == "frostline"
    assert len(payload["rows"]) == 1
    assert payload["rows"][0]["text"] == private_review
    assert payload["rows"][0]["polarity"] in {
        "supporting",
        "opposing",
        "conflicting",
        "insufficient",
    }
    assert private_product_id not in serialized
    assert not re.search(r"[0-9a-f]{64}", serialized)
    assert not re.search(r"[A-Za-z]:[\\/]", serialized)
    assert "review_text" not in serialized and "evidence_id" not in serialized


def test_evidence_detail_rejects_unknown_alias_and_token(tmp_path):
    store, checkpoint, config, private_product_id, _, _ = _local_fixture(tmp_path)
    service = P6BackedWorkbenchService(
        store_path=store,
        checkpoint_path=checkpoint,
        trace_path=tmp_path / "workbench-traces.jsonl",
        config=config,
        product_ids_by_code={"frostline": private_product_id},
        uuid_factory=lambda: UUID("00000000-0000-4000-8000-000000000005"),
    )
    client = TestClient(create_workbench_app(service=service))
    created = client.post(
        "/api/v1/runs",
        json={
            "workflow": "diagnosis",
            "product_codes": ["frostline"],
            "product_query": None,
            "aspect": "noise",
            "question": "Which signal should operations verify?",
        },
    )
    token = created.json()["run_token"]

    missing_alias = client.get(f"/api/v1/runs/{token}/evidence/EV-99")
    missing_token = client.get(f"/api/v1/runs/{'f' * 8}-0000-4000-8000-000000000006/evidence/EV-01")

    assert missing_alias.status_code == 404
    assert missing_alias.json()["error"]["code"] == "EVIDENCE_DETAIL_UNAVAILABLE"
    assert missing_token.status_code == 404
    assert missing_token.json()["error"]["code"] == "RUN_NOT_FOUND"
