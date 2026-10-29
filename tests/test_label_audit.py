"""Tests for the read-only P3 label-quality audit (vocabulary polarity vs. star rating)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ecom_review_intelligence.engine import initialize_store
from ecom_review_intelligence.label_audit import (
    LabelTally,
    audit_store,
    rating_bucket,
    review_signals,
)

ASPECT_TERMS = {
    "noise": ["noise", "noisy", "loud", "quiet", "sound"],
    "cleaning_maintenance": ["clean", "cleaning", "maintenance", "descale", "sanitize"],
}
POLARITY_TERMS = {
    "supporting": ["great", "good", "love", "easy", "quiet", "fast", "perfect", "reliable"],
    "opposing": ["bad", "poor", "broke", "broken", "noisy", "loud", "leak", "slow", "stopped", "failed"],
}

# The real P4 row that the local trial mislabeled: the broken appliance belongs to the
# customer's old fridge, not to the reviewed product.
CROSS_SENTENCE_REVIEW = (
    "This has been an amazing buy! We live in the desert and the ice maker in the fridge "
    "broke. This makes ice perfectly and fairly quickly. The noise level is minimal, I only "
    "hear it if I'm in the same room."
)
SAME_SENTENCE_REVIEW = "The noise is really loud at night and the sound keeps waking us up."
NEGATED_REVIEW = "It is not very noisy, so the quiet sound is fine in a bedroom."


def _signals(text: str, aspect: str = "noise"):
    return review_signals(
        text,
        aspect=aspect,
        aspect_terms=ASPECT_TERMS,
        polarity_terms=POLARITY_TERMS,
    )


def test_rating_bucket_maps_stars_to_an_independent_sentiment_proxy() -> None:
    assert rating_bucket(4.0) == "supporting"
    assert rating_bucket(5.0) == "supporting"
    assert rating_bucket(1.0) == "opposing"
    assert rating_bucket(2.0) == "opposing"
    assert rating_bucket(3.0) == "neutral"
    assert rating_bucket(None) == "unknown"


def test_polarity_term_outside_the_aspect_sentence_is_flagged_cross_sentence() -> None:
    signals = _signals(CROSS_SENTENCE_REVIEW)

    assert signals.polarity == "opposing"
    assert signals.scope == "cross_sentence"


def test_polarity_term_inside_the_aspect_sentence_is_not_flagged() -> None:
    signals = _signals(SAME_SENTENCE_REVIEW)

    assert signals.polarity == "opposing"
    assert signals.scope == "aligned"


def test_negated_polarity_term_is_flagged() -> None:
    signals = _signals(NEGATED_REVIEW)

    assert signals.negated is True


def test_review_without_any_polarity_term_is_aspect_only() -> None:
    signals = _signals("The ice maker has a water reservoir under the basket.")

    assert signals.polarity == "insufficient"
    assert signals.scope == "aspect_only"


def test_tally_reports_star_rating_agreement_per_aspect() -> None:
    tally = LabelTally()
    tally.record("noise", _signals(SAME_SENTENCE_REVIEW), 1.0)
    tally.record("noise", _signals(CROSS_SENTENCE_REVIEW), 5.0)
    tally.record("noise", _signals("Great quality and very quiet sound."), 5.0)
    tally.record("noise", _signals(CROSS_SENTENCE_REVIEW), 3.0)

    aspect = tally.to_public_dict()["aspects"]["noise"]

    assert aspect["reviews_scanned"] == 4
    # Only directional keyword labels against directional ratings are comparable, so the
    # three-star row is counted as scanned but excluded from the agreement denominator.
    assert aspect["rating_comparable_count"] == 3
    assert aspect["rating_agreement_count"] == 2
    assert aspect["rating_agreement_rate"] == pytest.approx(2 / 3)
    assert aspect["rating_bucket_counts"]["neutral"] == 1
    assert aspect["disagreement_keyword_opposing_rating_high"] == 1
    assert aspect["scope_counts"]["cross_sentence"] == 2
    assert aspect["scope_counts"]["aligned"] == 2
    assert aspect["rating_bucket_counts"]["unknown"] == 0


def _build_store(path: Path) -> Path:
    initialize_store(
        path,
        snapshot_id="snapshot-for-label-audit",
        contract_sha256="c" * 64,
        aspect_terms={key: tuple(value) for key, value in ASPECT_TERMS.items()},
        polarity_terms={key: tuple(value) for key, value in POLARITY_TERMS.items()},
    )
    with sqlite3.connect(path) as connection:
        connection.executemany(
            "INSERT INTO products (parent_asin, title) VALUES (?, ?)",
            [("B0LABEL001", "Label Audit Ice Maker")],
        )
        connection.executemany(
            """
            INSERT INTO reviews (evidence_id, source_line, parent_asin, text, rating,
                                 timestamp, verified_purchase, helpful_vote)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("e" * 64, 1, "B0LABEL001", CROSS_SENTENCE_REVIEW, 5.0, 1, 1, 0),
                ("f" * 64, 2, "B0LABEL001", SAME_SENTENCE_REVIEW, 1.0, 1, 1, 0),
                ("a" * 64, 3, "B0LABEL001", NEGATED_REVIEW, 5.0, 1, 1, 0),
            ],
        )
        connection.executemany(
            "INSERT INTO reviews_fts (rowid, text) VALUES (?, ?)",
            [(1, CROSS_SENTENCE_REVIEW), (2, SAME_SENTENCE_REVIEW), (3, NEGATED_REVIEW)],
        )
    return path


def test_audit_store_scans_every_aspect_and_reports_lineage(tmp_path: Path) -> None:
    store = _build_store(tmp_path / "label_audit.sqlite")

    report = audit_store(store)

    assert report["schema_version"] == "phase-3b-label-quality-v1"
    assert report["dataset_snapshot_id"] == "snapshot-for-label-audit"
    assert report["contract_sha256"] == "c" * 64
    assert report["reviews_scanned"] == 3
    noise = report["aspects"]["noise"]
    assert noise["reviews_scanned"] == 3
    assert noise["scope_counts"]["cross_sentence"] == 1
    assert noise["negated_count"] == 1
    assert report["aspects"]["cleaning_maintenance"]["reviews_scanned"] == 0


def test_audit_store_opens_the_evidence_store_read_only(tmp_path: Path) -> None:
    store = _build_store(tmp_path / "label_audit_ro.sqlite")
    before = store.read_bytes()

    audit_store(store)

    assert store.read_bytes() == before


def test_audit_report_contains_no_private_detail(tmp_path: Path) -> None:
    report = audit_store(_build_store(tmp_path / "label_audit_safe.sqlite"))

    document = json.dumps(report, ensure_ascii=False)
    for fragment in ("amazing buy", "weed eater", "B0LABEL001", "ice maker in the fridge"):
        assert fragment not in document
    lowered = document.lower()
    for forbidden in ("evidence_id", "parent_asin", "b:\\", "d:\\", "/users/", "/home/"):
        assert forbidden not in lowered
