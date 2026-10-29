from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from ecom_review_intelligence.engine import (
    EngineError,
    LocalStoreBuild,
    build_local_store,
    compute_evidence_id,
    compute_store_content_sha256,
    count_aspect_matches,
    initialize_store,
    query_aspect,
    query_stratified_aspect,
    verify_citation,
    write_phase4_outputs,
)


def test_deterministic_query_returns_revalidatable_exact_product_evidence(tmp_path: Path) -> None:
    store_path = tmp_path / "evidence.sqlite"
    snapshot_id = "a" * 64
    contract_sha256 = "b" * 64
    first_evidence_id = compute_evidence_id(snapshot_id, 1)
    second_evidence_id = compute_evidence_id(snapshot_id, 2)
    initialize_store(
        store_path,
        snapshot_id=snapshot_id,
        contract_sha256=contract_sha256,
        aspect_terms={"noise": ("noise", "quiet", "loud")},
        polarity_terms={"supporting": ("quiet",), "opposing": ("loud",)},
    )
    with sqlite3.connect(store_path) as connection:
        connection.executemany(
            "INSERT INTO products(parent_asin, title) VALUES (?, ?)",
            [("product-a", "A"), ("product-b", "B")],
        )
        connection.executemany(
            """
            INSERT INTO reviews(
                evidence_id, source_line, parent_asin, text, rating, timestamp, verified_purchase, helpful_vote
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (first_evidence_id, 1, "product-a", "quiet ice maker", 5.0, 1, 1, 0),
                (second_evidence_id, 2, "product-b", "loud ice maker", 1.0, 2, 1, 0),
            ],
        )
        connection.executemany(
            "INSERT INTO reviews_fts(rowid, text) VALUES (?, ?)",
            [(1, "quiet ice maker"), (2, "loud ice maker")],
        )

    result = query_aspect(store_path, ("product-a",), "noise", limit=5)

    assert result.strength == "WEAK"
    assert result.evidence[0].evidence_id == first_evidence_id
    assert result.evidence[0].polarity == "supporting"
    assert verify_citation(store_path, first_evidence_id) is True
    assert verify_citation(store_path, second_evidence_id) is True


def test_count_aspect_matches_reports_the_true_total_not_the_sample(tmp_path: Path) -> None:
    store_path = tmp_path / "evidence.sqlite"
    snapshot_id = "a" * 64
    initialize_store(
        store_path,
        snapshot_id=snapshot_id,
        contract_sha256="b" * 64,
        aspect_terms={"noise": ("noise", "quiet", "loud")},
        polarity_terms={"supporting": ("quiet",), "opposing": ("loud",)},
    )
    reviews = [(index, "product-a", "quiet ice maker") for index in range(1, 8)]
    reviews.append((8, "product-b", "quiet ice maker"))
    with sqlite3.connect(store_path) as connection:
        connection.executemany(
            "INSERT INTO products(parent_asin, title) VALUES (?, ?)",
            [("product-a", "A"), ("product-b", "B")],
        )
        connection.executemany(
            """
            INSERT INTO reviews(
                evidence_id, source_line, parent_asin, text, rating, timestamp,
                verified_purchase, helpful_vote
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (compute_evidence_id(snapshot_id, line), line, product, text, 5.0, 1, 1, 0)
                for line, product, text in reviews
            ],
        )

    sampled = query_aspect(store_path, ("product-a",), "noise", limit=2)

    assert len(sampled.evidence) == 2
    assert count_aspect_matches(store_path, ("product-a",), "noise") == 7
    assert count_aspect_matches(store_path, ("product-a", "product-b"), "noise") == 8
    with pytest.raises(EngineError, match="frozen contract"):
        count_aspect_matches(store_path, ("product-a",), "unfrozen")


def test_query_rejects_an_aspect_outside_the_frozen_contract(tmp_path: Path) -> None:
    store_path = tmp_path / "evidence.sqlite"
    initialize_store(
        store_path,
        snapshot_id="a" * 64,
        contract_sha256="b" * 64,
        aspect_terms={"noise": ("noise",)},
        polarity_terms={"supporting": ("quiet",), "opposing": ("loud",)},
    )

    with pytest.raises(EngineError, match="enabled"):
        query_aspect(store_path, ("product-a",), "unfrozen", limit=5)


def test_build_local_store_rejects_missing_frozen_lineage(tmp_path: Path) -> None:
    with pytest.raises(EngineError, match="P1"):
        build_local_store(tmp_path, git_revision="test-revision")


def test_build_local_store_atomically_materializes_a_valid_frozen_fixture(tmp_path: Path) -> None:
    _write_build_fixture(tmp_path)

    result = build_local_store(tmp_path, git_revision="test-revision")

    assert result.product_count == 1
    assert result.review_count == 1
    assert result.store_path.exists()
    assert len(result.content_sha256) == 64
    assert query_aspect(result.store_path, ("product-a",), "noise", limit=1).strength == "WEAK"


def test_store_content_hash_detects_consistent_review_and_fts_tampering(tmp_path: Path) -> None:
    _write_build_fixture(tmp_path)
    result = build_local_store(tmp_path, git_revision="test-revision")
    with sqlite3.connect(result.store_path) as connection:
        connection.execute("UPDATE reviews SET text='noise changed'")
        connection.execute("UPDATE reviews_fts SET text='noise changed'")

    assert compute_store_content_sha256(result.store_path) != result.content_sha256


def test_store_content_hash_detects_runtime_policy_tampering(tmp_path: Path) -> None:
    _write_build_fixture(tmp_path)
    result = build_local_store(tmp_path, git_revision="test-revision")
    with sqlite3.connect(result.store_path) as connection:
        connection.execute(
            "UPDATE store_meta SET value=? WHERE key='aspect_terms'",
            ('{"unrelated":["term"]}',),
        )

    assert compute_store_content_sha256(result.store_path) != result.content_sha256


def test_phase4_outputs_are_aggregate_only(tmp_path: Path) -> None:
    result = LocalStoreBuild(
        store_path=tmp_path / "data" / "processed" / "ice_makers_evidence.sqlite",
        dataset_snapshot_id="a" * 64,
        contract_sha256="b" * 64,
        product_count=2,
        review_count=3,
        git_revision="test-revision",
        content_sha256="c" * 64,
    )

    summary_path, report_path = write_phase4_outputs(
        tmp_path,
        result,
        verified_at="2026-08-03T00:00:00+00:00",
    )

    summary = summary_path.read_text(encoding="utf-8")
    assert '"state": "PASSED"' in summary
    assert '"content_sha256": "' + "c" * 64 + '"' in summary
    assert "ice_makers_evidence.sqlite" not in summary
    assert "product-a" not in summary
    assert "parent_asin" not in summary
    assert "evidence_id" not in summary
    assert "P4 local evidence-store build" in report_path.read_text(encoding="utf-8")


def _write_build_fixture(project_root: Path) -> None:
    raw_root = project_root / "data" / "raw"
    raw_root.mkdir(parents=True)
    metadata_path = raw_root / "meta_Appliances.jsonl.gz"
    review_path = raw_root / "Appliances.jsonl.gz"
    with gzip.open(metadata_path, "wt", encoding="utf-8") as target:
        target.write(
            json.dumps(
                {
                    "parent_asin": "product-a",
                    "title": "Fixture ice maker",
                    "categories": ["Appliances", "Ice Makers"],
                }
            )
            + "\n"
        )
    with gzip.open(review_path, "wt", encoding="utf-8") as target:
        target.write(
            json.dumps(
                {
                    "parent_asin": "product-a",
                    "text": "quiet ice maker",
                    "rating": 5.0,
                    "timestamp": 1,
                    "verified_purchase": True,
                    "helpful_vote": 0,
                }
            )
            + "\n"
        )
    snapshot_id = "a" * 64
    audit_sha256 = "b" * 64
    contract = {
        "state": "PASSED",
        "dataset_snapshot_id": snapshot_id,
        "audit_sha256": audit_sha256,
        "selected_category": "Ice Makers",
        "selected_product_count": 1,
        "selected_review_count": 1,
        "aspect_terms": {"noise": ["quiet"]},
        "polarity_terms": {"supporting": ["quiet"], "opposing": ["loud"]},
    }
    contract["contract_sha256"] = _canonical_sha(contract)
    _write_json(
        project_root / "artifacts" / "manifests" / "phase-1-download.json",
        {
            "state": "PASSED",
            "dataset_snapshot_id": snapshot_id,
            "sources": [
                _source("reviews", review_path),
                _source("metadata", metadata_path),
            ],
        },
    )
    _write_json(
        project_root / "artifacts" / "manifests" / "phase-2-scope.json",
        {
            "state": "PASSED",
            "dataset_snapshot_id": snapshot_id,
            "audit_sha256": audit_sha256,
        },
    )
    _write_json(
        project_root / "artifacts" / "contracts" / "phase-3-evidence-policy.json",
        contract,
    )


def _source(source_name: str, path: Path) -> dict[str, object]:
    return {
        "source_name": source_name,
        "filename": path.name,
        "total_bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _canonical_sha(value: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def test_stratified_sample_does_not_read_only_the_earliest_reviews(tmp_path: Path) -> None:
    store_path = tmp_path / "evidence.sqlite"
    snapshot_id = "a" * 64
    initialize_store(
        store_path,
        snapshot_id=snapshot_id,
        contract_sha256="b" * 64,
        aspect_terms={"noise": ("noise",)},
        polarity_terms={"supporting": ("quiet",), "opposing": ("loud",)},
    )
    rows = [
        (1, "quiet noise a", 5.0),
        (2, "quiet noise b", 5.0),
        (3, "quiet noise c", 5.0),
        (4, "quiet noise d", 5.0),
        (5, "quiet noise e", 5.0),
        (6, "loud noise f", 1.0),
        (7, "loud noise g", 1.0),
        (8, "noise h", 3.0),
    ]
    with sqlite3.connect(store_path) as connection:
        connection.execute("INSERT INTO products(parent_asin, title) VALUES (?, ?)", ("p", "P"))
        connection.executemany(
            """
            INSERT INTO reviews(
                evidence_id, source_line, parent_asin, text, rating, timestamp, verified_purchase, helpful_vote
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (compute_evidence_id(snapshot_id, line), line, "p", text, rating, line, 1, 0)
                for line, text, rating in rows
            ],
        )
        connection.executemany(
            "INSERT INTO reviews_fts(rowid, text) VALUES (?, ?)",
            [(line, text) for line, text, _ in rows],
        )

    naive = query_aspect(store_path, ("p",), "noise", limit=5)
    assert all(evidence.rating == 5.0 for evidence in naive.evidence)

    sample = query_stratified_aspect(store_path, ("p",), "noise", limit=5)
    ratings = [evidence.rating for evidence in sample.evidence]
    assert len(sample.evidence) == 5
    assert 1.0 in ratings and any(rating >= 4 for rating in ratings)
    assert [evidence.source_line for evidence in sample.evidence] == [1, 2, 6, 7, 8]

    again = query_stratified_aspect(store_path, ("p",), "noise", limit=5)
    assert [evidence.evidence_id for evidence in again.evidence] == [
        evidence.evidence_id for evidence in sample.evidence
    ]
