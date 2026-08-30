from __future__ import annotations

import sqlite3
from pathlib import Path

from ecom_review_intelligence.engine import compute_evidence_id, initialize_store
from ecom_review_intelligence.evaluation import evaluate_fixed_cases


def test_evaluation_compares_two_retrievers_and_honors_no_answer_cases(tmp_path: Path) -> None:
    store_path = tmp_path / "evidence.sqlite"
    snapshot_id = "a" * 64
    first_id = compute_evidence_id(snapshot_id, 1)
    initialize_store(
        store_path,
        snapshot_id=snapshot_id,
        contract_sha256="b" * 64,
        aspect_terms={"noise": ("quiet",), "leakage": ("leak",)},
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
                (first_id, 1, "product-a", "quiet ice maker", 5.0, 1, 1, 0),
                (compute_evidence_id(snapshot_id, 2), 2, "product-b", "quiet ice maker", 5.0, 2, 1, 0),
            ],
        )
        connection.executemany(
            "INSERT INTO reviews_fts(rowid, text) VALUES (?, ?)",
            [(1, "quiet ice maker"), (2, "quiet ice maker")],
        )

    result = evaluate_fixed_cases(
        store_path,
        (
            {
                "case_id": "P5-DIAG-01",
                "workflow": "diagnosis",
                "parent_asins": ["product-a"],
                "aspect": "noise",
                "expected_behavior": "evidence",
                "expected_evidence_ids": [first_id],
                "label_provenance": "silver",
                "reviewer_status": "not_reviewed",
                "frozen_at": "2026-08-03T00:00:00+00:00",
            },
            {
                "case_id": "P5-NOANSWER-01",
                "workflow": "no_answer",
                "parent_asins": ["product-a"],
                "aspect": "leakage",
                "expected_behavior": "no_answer",
                "expected_evidence_ids": [],
                "label_provenance": "silver",
                "reviewer_status": "not_reviewed",
                "frozen_at": "2026-08-03T00:00:00+00:00",
            },
        ),
        limit=3,
    )

    assert result.metrics["lexical"].recall_at_k == 1.0
    assert result.metrics["fts5_bm25"].recall_at_k == 1.0
    assert result.metrics["lexical"].citation_integrity == 1.0
    assert result.metrics["fts5_bm25"].product_join_errors == 0
    assert result.metrics["lexical"].no_answer_false_positives == 0
    assert result.quality_gates_passed is True
