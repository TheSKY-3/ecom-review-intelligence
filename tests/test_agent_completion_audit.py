import hashlib
import io
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from ecom_review_intelligence.agent_completion_audit import (
    CompletionAuditError,
    _source_hash_at_revision,
    _validate_store,
    audit_main,
    validate_clean_tracked_worktree,
    validate_lineage_chain,
    validate_scope_chain,
    validate_tracked_content,
    validate_tracked_paths,
)
from ecom_review_intelligence.agent_evaluation import git_code_identity
from ecom_review_intelligence.engine import (
    compute_evidence_id,
    compute_store_content_sha256,
    initialize_store,
)


def _lineage_documents():
    review_hash = "1" * 64
    metadata_hash = "2" * 64
    snapshot = hashlib.sha256(
        f"reviews:{review_hash}\nmetadata:{metadata_hash}\n".encode()
    ).hexdigest()
    contract = "b" * 64
    config = "c" * 64
    corpus = "d" * 64
    selection = "e" * 64
    return {
        "p1": {
            "state": "PASSED",
            "dataset_snapshot_id": snapshot,
            "sources": [
                {"source_name": "reviews", "sha256": review_hash},
                {"source_name": "metadata", "sha256": metadata_hash},
            ],
        },
        "scope": {"state": "PASSED", "dataset_snapshot_id": snapshot},
        "contract": {
            "state": "PASSED",
            "dataset_snapshot_id": snapshot,
            "contract_sha256": contract,
        },
        "store": {
            "state": "PASSED",
            "dataset_snapshot_id": snapshot,
            "contract_sha256": contract,
        },
        "config": {
            "dataset_snapshot_id": snapshot,
            "contract_sha256": contract,
            "config_sha256": config,
        },
        "offline": {
            "dataset_snapshot_id": snapshot,
            "contract_sha256": contract,
            "config_sha256": config,
            "corpus_sha256": corpus,
            "gates": {"all_blocking_gates_passed": True},
        },
        "live": {
            "dataset_snapshot_id": snapshot,
            "contract_sha256": contract,
            "config_sha256": config,
            "corpus_sha256": corpus,
            "selection_sha256": selection,
            "gates": {"all_blocking_gates_passed": True},
        },
        "selection": {
            "source_corpus_sha256": corpus,
            "selection_sha256": selection,
        },
    }


def test_completion_lineage_requires_one_exact_passed_chain():
    documents = _lineage_documents()
    validate_lineage_chain(**documents)
    documents["live"]["contract_sha256"] = "f" * 64
    with pytest.raises(CompletionAuditError):
        validate_lineage_chain(**documents)


def test_completion_lineage_recomputes_p1_snapshot_from_registered_sources():
    documents = _lineage_documents()
    documents["p1"]["sources"][0]["sha256"] = "9" * 64
    with pytest.raises(CompletionAuditError):
        validate_lineage_chain(**documents)


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.local",
        "data/raw/reviews.jsonl.gz",
        "data/processed/store.sqlite",
        "artifacts/evals/phase-6-live-details.json",
        "简历/private.md",
        "规划/private.md",
        "data/checkpoints/agent.sqlite",
    ],
)
def test_completion_audit_rejects_private_or_runtime_tracked_paths(path):
    with pytest.raises(CompletionAuditError):
        validate_tracked_paths(["README.md", path])


def test_completion_audit_accepts_expected_public_paths():
    validate_tracked_paths(
        [
            "README.md",
            "artifacts/evaluation-summaries/phase-6-agent.json",
            "docs/reports/phase-6-agent-evaluation.md",
            "tests/fixtures/p6_model_replay.json",
            ".env.example",
        ]
    )


@pytest.mark.parametrize(
    "parts",
    [
        ("provider_key = sk-", "abcdefghijklmnopqrstuvwx"),
        ("Authorization: Bear", "er abcdefghijklmnopqrstuvwxyz012345"),
    ],
)
def test_completion_audit_rejects_secret_values_in_tracked_text(tmp_path, parts):
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("".join(parts), encoding="utf-8")
    with pytest.raises(CompletionAuditError):
        validate_tracked_content(tmp_path, ["tracked.txt"])


def test_completion_audit_allows_secret_variable_names_without_values(tmp_path):
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("Read DEEPSEEK_API_KEY from the environment.", encoding="utf-8")
    validate_tracked_content(tmp_path, ["tracked.txt"])


def test_completion_audit_rejects_named_key_assignment_without_sk_prefix(tmp_path):
    tracked = tmp_path / "tracked.txt"
    tracked.write_text(
        "DEEPSEEK_API_" + "KEY=plain-secret-value\n", encoding="utf-8"
    )
    with pytest.raises(CompletionAuditError):
        validate_tracked_content(tmp_path, ["tracked.txt"])


def test_completion_audit_scans_current_tracked_repository_without_self_matches():
    root = Path(__file__).parents[1]
    tracked = subprocess.run(
        ["git", "-C", str(root), "ls-files"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.splitlines()

    validate_tracked_paths(tracked)
    validate_tracked_content(root, tracked)


def test_completion_audit_requires_clean_tracked_worktree(tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "audit@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Audit Test"], check=True
    )
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("clean\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "fixture"],
        check=True,
        capture_output=True,
    )
    validate_clean_tracked_worktree(tmp_path)
    tracked.write_text("dirty\n", encoding="utf-8")
    with pytest.raises(CompletionAuditError):
        validate_clean_tracked_worktree(tmp_path)


def test_source_identity_is_stable_across_windows_line_endings(tmp_path):
    paths = (
        Path("src/ecom_review_intelligence/agent_sample.py"),
        Path("src/ecom_review_intelligence/engine.py"),
        Path("src/ecom_review_intelligence/model_gateway.py"),
        Path("scripts/run_phase6_sample.py"),
    )
    for path in paths:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"value = 1\n")
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "audit@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Audit Test"], check=True
    )
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "fixture"],
        check=True,
        capture_output=True,
    )
    revision = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    for path in paths:
        (tmp_path / path).write_bytes(b"value = 1\r\n")

    assert git_code_identity(tmp_path)["source_sha256"] == _source_hash_at_revision(
        tmp_path, revision
    )


def test_scope_chain_rejects_tampered_scope_and_recomputes_audit_hash():
    audit = {
        "state": "PASSED",
        "dataset_snapshot_id": "a" * 64,
        "candidates": [{"category": "Ice Makers", "joined_reviews": 10}],
    }
    audit["audit_sha256"] = hashlib.sha256(
        json.dumps(
            audit, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    scope = {
        "state": "PASSED",
        "dataset_snapshot_id": "a" * 64,
        "audit_sha256": audit["audit_sha256"],
        "selected_category": "Ice Makers",
        "selection_metrics": audit["candidates"][0],
    }
    contract = {
        "state": "PASSED",
        "dataset_snapshot_id": "a" * 64,
        "audit_sha256": audit["audit_sha256"],
        "selected_category": "Ice Makers",
    }
    validate_scope_chain(audit=audit, scope=scope, contract=contract)
    scope["selected_category"] = "Water Filters"
    with pytest.raises(CompletionAuditError):
        validate_scope_chain(audit=audit, scope=scope, contract=contract)


def test_store_validation_rejects_same_count_fts_content_tampering(tmp_path):
    store = tmp_path / "store.sqlite"
    snapshot, contract = "a" * 64, "b" * 64
    initialize_store(
        store,
        snapshot_id=snapshot,
        contract_sha256=contract,
        aspect_terms={"noise": ("noise",)},
        polarity_terms={"supporting": ("quiet",), "opposing": ("loud",)},
    )
    evidence_id = compute_evidence_id(snapshot, 1)
    with sqlite3.connect(store) as connection:
        connection.execute(
            "INSERT INTO products(parent_asin,title) VALUES (?,?)", ("LOCAL_A", "Model")
        )
        connection.execute(
            "INSERT INTO reviews(evidence_id,source_line,parent_asin,text,rating,timestamp,verified_purchase,helpful_vote) VALUES (?,?,?,?,?,?,?,?)",
            (evidence_id, 1, "LOCAL_A", "noise loud", 1.0, 1, 1, 0),
        )
        connection.execute(
            "INSERT INTO reviews_fts(rowid,text) VALUES ((SELECT rowid FROM reviews),?)",
            ("different text",),
        )
    summary = {
        "product_count": 1,
        "review_count": 1,
        "fts_row_count": 1,
        "content_sha256": "c" * 64,
    }
    with pytest.raises(CompletionAuditError):
        _validate_store(
            store,
            {"dataset_snapshot_id": snapshot, "contract_sha256": contract},
            summary,
        )


def test_store_validation_rejects_runtime_policy_tampering(tmp_path):
    store = tmp_path / "store.sqlite"
    snapshot, contract = "a" * 64, "b" * 64
    initialize_store(
        store,
        snapshot_id=snapshot,
        contract_sha256=contract,
        aspect_terms={"noise": ("noise",)},
        polarity_terms={"supporting": ("quiet",), "opposing": ("loud",)},
    )
    summary = {
        "product_count": 0,
        "review_count": 0,
        "fts_row_count": 0,
        "content_sha256": compute_store_content_sha256(store),
    }
    config = {"dataset_snapshot_id": snapshot, "contract_sha256": contract}
    _validate_store(store, config, summary)
    with sqlite3.connect(store) as connection:
        connection.execute(
            "UPDATE store_meta SET value=? WHERE key='aspect_terms'",
            ('{"unrelated":["term"]}',),
        )

    with pytest.raises(CompletionAuditError):
        _validate_store(store, config, summary)


def test_completion_audit_cli_hides_missing_local_paths(tmp_path):
    output, errors = io.StringIO(), io.StringIO()
    code = audit_main(
        ["--project-root", str(tmp_path)], stdout=output, stderr=errors
    )
    assert code == 1
    assert output.getvalue() == ""
    assert json.loads(errors.getvalue()) == {
        "schema_version": "p6-completion-audit-cli-v1",
        "status": "FAILED",
    }
    assert str(tmp_path) not in errors.getvalue()
