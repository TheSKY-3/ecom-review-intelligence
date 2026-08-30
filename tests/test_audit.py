from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pytest

from ecom_review_intelligence.audit import (
    AuditError,
    audit_phase2,
    write_phase2_outputs,
    write_scope_decision,
)


def write_gzip_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="\n") as destination:
        for row in rows:
            destination.write(json.dumps(row, sort_keys=True))
            destination.write("\n")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_phase1_manifest(root: Path) -> str:
    raw_root = root / "data" / "raw"
    review_path = raw_root / "Appliances.jsonl.gz"
    metadata_path = raw_root / "meta_Appliances.jsonl.gz"
    review_sha256 = file_sha256(review_path)
    metadata_sha256 = file_sha256(metadata_path)
    snapshot_id = hashlib.sha256(
        f"reviews:{review_sha256}\nmetadata:{metadata_sha256}\n".encode()
    ).hexdigest()
    manifest_path = root / "artifacts" / "manifests" / "phase-1-download.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "state": "PASSED",
                "dataset_snapshot_id": snapshot_id,
                "sources": [
                    {
                        "source_name": "reviews",
                        "filename": review_path.name,
                        "total_bytes": review_path.stat().st_size,
                        "sha256": review_sha256,
                    },
                    {
                        "source_name": "metadata",
                        "filename": metadata_path.name,
                        "total_bytes": metadata_path.stat().st_size,
                        "sha256": metadata_sha256,
                    },
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return snapshot_id


def make_fixture(root: Path) -> str:
    quiet_products = [f"quiet-{index:02d}" for index in range(20)]
    durable_products = [f"durable-{index:02d}" for index in range(10)]
    sparse_products = [f"sparse-{index:02d}" for index in range(10)]
    metadata_rows = [
        {"parent_asin": parent_asin, "categories": ["Appliances", "Quiet Appliances"]}
        for parent_asin in quiet_products
    ]
    metadata_rows.extend(
        {"parent_asin": parent_asin, "categories": ["Appliances", "Durable Appliances"]}
        for parent_asin in durable_products
    )
    metadata_rows.extend(
        {"parent_asin": parent_asin, "categories": ["Appliances", "Sparse Appliances"]}
        for parent_asin in sparse_products
    )
    review_rows = [
        {
            "parent_asin": quiet_products[index % len(quiet_products)],
            "rating": 5.0,
            "timestamp": 1_700_000_000_000 + index,
            "text": "unique raw review phrase reviewer-private-id" if index == 0 else "quiet review",
            "title": "quiet",
            "verified_purchase": True,
            "helpful_vote": 0,
        }
        for index in range(600)
    ]
    review_rows.extend(
        {
            "parent_asin": durable_products[index % len(durable_products)],
            "rating": 4.0,
            "timestamp": 1_700_100_000_000 + index,
            "text": "durable review",
            "title": "durable",
            "verified_purchase": False,
            "helpful_vote": 1,
        }
        for index in range(550)
    )
    review_rows.append(
        {
            "parent_asin": "missing-parent",
            "rating": 5.0,
            "timestamp": 1_700_200_000_000,
            "text": "",
            "title": "",
            "verified_purchase": True,
            "helpful_vote": 0,
        }
    )
    write_gzip_jsonl(root / "data" / "raw" / "Appliances.jsonl.gz", review_rows)
    write_gzip_jsonl(root / "data" / "raw" / "meta_Appliances.jsonl.gz", metadata_rows)
    return write_phase1_manifest(root)


def test_audit_phase2_rejects_manifest_hash_mismatch(tmp_path: Path) -> None:
    make_fixture(tmp_path)
    manifest_path = tmp_path / "artifacts" / "manifests" / "phase-1-download.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sources"][0]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(AuditError, match="SHA-256"):
        audit_phase2(tmp_path, git_revision="test-revision", observed_at="2026-08-03T00:00:00+00:00")


def test_audit_phase2_profiles_fields_and_ranks_eligible_categories(tmp_path: Path) -> None:
    expected_snapshot = make_fixture(tmp_path)

    result = audit_phase2(
        tmp_path,
        git_revision="test-revision",
        observed_at="2026-08-03T00:00:00+00:00",
    )

    assert result.snapshot_id == expected_snapshot
    assert result.review_profile.total_rows == 1_151
    assert result.review_profile.joined_rows == 1_150
    assert result.review_profile.unmatched_rows == 1
    assert result.recommended_category == "Quiet Appliances"
    assert result.candidates[-1].category == "Sparse Appliances"
    assert result.candidates[0].eligible is True
    assert result.candidates[0].recommendation_score is not None


def test_write_audit_outputs_omits_raw_record_content(tmp_path: Path) -> None:
    make_fixture(tmp_path)
    result = audit_phase2(
        tmp_path,
        git_revision="test-revision",
        observed_at="2026-08-03T00:00:00+00:00",
    )

    audit_path, report_path = write_phase2_outputs(tmp_path, result)

    document = json.loads(audit_path.read_text(encoding="utf-8"))
    report = report_path.read_text(encoding="utf-8")
    assert document["inputs"]["reviews"]["sha256"] == file_sha256(
        tmp_path / "data" / "raw" / "Appliances.jsonl.gz"
    )
    assert document["inputs"]["metadata"]["filename"] == "meta_Appliances.jsonl.gz"
    assert "unique raw review phrase" not in report
    assert "reviewer-private-id" not in report
    assert result.audit_sha256 in report


def test_write_scope_decision_rejects_a_noneligible_category(tmp_path: Path) -> None:
    make_fixture(tmp_path)
    audit = audit_phase2(
        tmp_path,
        git_revision="test-revision",
        observed_at="2026-08-03T00:00:00+00:00",
    )

    with pytest.raises(AuditError, match="eligible"):
        write_scope_decision(tmp_path, audit, "Sparse Appliances")


def test_write_scope_decision_records_selected_category_and_lineage(tmp_path: Path) -> None:
    make_fixture(tmp_path)
    audit = audit_phase2(
        tmp_path,
        git_revision="test-revision",
        observed_at="2026-08-03T00:00:00+00:00",
    )

    manifest_path, report_path = write_scope_decision(tmp_path, audit, "Quiet Appliances")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["dataset_snapshot_id"] == audit.snapshot_id
    assert manifest["audit_sha256"] == audit.audit_sha256
    assert manifest["selected_category"] == "Quiet Appliances"
    assert manifest["selection_mode"] == "predeclared_recommendation"
    assert "Quiet Appliances" in report_path.read_text(encoding="utf-8")


def test_write_scope_decision_records_an_explicit_eligible_override(tmp_path: Path) -> None:
    make_fixture(tmp_path)
    audit = audit_phase2(
        tmp_path,
        git_revision="test-revision",
        observed_at="2026-08-03T00:00:00+00:00",
    )

    manifest_path, _ = write_scope_decision(tmp_path, audit, "Durable Appliances")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["recommended_category"] == "Quiet Appliances"
    assert manifest["selection_mode"] == "explicit_eligible_override"
