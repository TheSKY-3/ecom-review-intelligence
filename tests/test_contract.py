from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pytest

from ecom_review_intelligence.contract import (
    ContractError,
    build_evidence_contract,
    write_contract_outputs,
)


def _write_gzip(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="\n") as destination:
        for row in rows:
            destination.write(json.dumps(row))
            destination.write("\n")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_fixture(root: Path) -> str:
    raw_root = root / "data" / "raw"
    selected_parent = "selected-parent"
    _write_gzip(
        raw_root / "meta_Appliances.jsonl.gz",
        [
            {"parent_asin": selected_parent, "categories": ["Appliances", "Ice Makers"]},
            {"parent_asin": "excluded-parent", "categories": ["Appliances", "Water Filters"]},
        ],
    )
    _write_gzip(
        raw_root / "Appliances.jsonl.gz",
        [
            {
                "parent_asin": selected_parent,
                "text": (
                    "private synthetic review phrase ice cube quiet noise reliable broke clean "
                    "maintenance fast slow easy setup leak water compact portable"
                ),
            }
            for _ in range(120)
        ]
        + [{"parent_asin": "excluded-parent", "text": "excluded product text"}],
    )
    review_path = raw_root / "Appliances.jsonl.gz"
    metadata_path = raw_root / "meta_Appliances.jsonl.gz"
    review_sha = _sha256(review_path)
    metadata_sha = _sha256(metadata_path)
    snapshot = hashlib.sha256(
        f"reviews:{review_sha}\nmetadata:{metadata_sha}\n".encode()
    ).hexdigest()
    inputs = {
        "reviews": {
            "filename": review_path.name,
            "total_bytes": review_path.stat().st_size,
            "sha256": review_sha,
        },
        "metadata": {
            "filename": metadata_path.name,
            "total_bytes": metadata_path.stat().st_size,
            "sha256": metadata_sha,
        },
    }
    _write_json(
        root / "artifacts" / "manifests" / "phase-1-download.json",
        {"state": "PASSED", "dataset_snapshot_id": snapshot, "sources": [{"source_name": key, **value} for key, value in inputs.items()]},
    )
    audit = {
        "state": "PASSED",
        "dataset_snapshot_id": snapshot,
        "inputs": inputs,
        "recommended_category": "Ice Makers",
    }
    audit_sha = _canonical_sha(audit)
    audit["audit_sha256"] = audit_sha
    _write_json(root / "artifacts" / "audits" / "phase-2-audit.json", audit)
    _write_json(
        root / "artifacts" / "manifests" / "phase-2-scope.json",
        {
            "state": "PASSED",
            "dataset_snapshot_id": snapshot,
            "audit_sha256": audit_sha,
            "selected_category": "Ice Makers",
        },
    )
    return snapshot


def _canonical_sha(value: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_build_contract_rejects_scope_with_mismatched_snapshot(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    scope_path = tmp_path / "artifacts" / "manifests" / "phase-2-scope.json"
    scope = json.loads(scope_path.read_text(encoding="utf-8"))
    scope["dataset_snapshot_id"] = "0" * 64
    _write_json(scope_path, scope)

    with pytest.raises(ContractError, match="snapshot"):
        build_evidence_contract(tmp_path, git_revision="test-revision")


def test_build_contract_enables_data_covered_aspects_and_freezes_policy(tmp_path: Path) -> None:
    snapshot = _write_fixture(tmp_path)

    contract = build_evidence_contract(
        tmp_path,
        git_revision="test-revision",
        observed_at="2026-08-03T00:00:00+00:00",
    )

    assert contract.dataset_snapshot_id == snapshot
    assert contract.selected_category == "Ice Makers"
    assert "noise" in contract.enabled_aspects
    assert contract.strength_rules["STRONG"]["min_evidence"] == 5
    assert contract.label_policy["gold"] == "prohibited"


def test_write_contract_outputs_omits_synthetic_record_values(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    contract = build_evidence_contract(
        tmp_path,
        git_revision="test-revision",
        observed_at="2026-08-03T00:00:00+00:00",
    )

    _, decision_path = write_contract_outputs(tmp_path, contract)

    decision = decision_path.read_text(encoding="utf-8")
    assert "private synthetic review phrase" not in decision
    assert contract.contract_sha256 in decision
