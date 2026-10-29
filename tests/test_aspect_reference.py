import json
import sqlite3
from pathlib import Path

from ecom_review_intelligence.aspect_reference import (
    AspectReferenceCounts,
    agreement_summary,
    build_aspect_reference,
    deterministic_verdict,
    reference_verdict,
)
from ecom_review_intelligence.engine import compute_evidence_id, initialize_store


def _store_fixture(tmp_path: Path) -> Path:
    store = tmp_path / "evidence.sqlite"
    snapshot = "a" * 64
    initialize_store(
        store,
        snapshot_id=snapshot,
        contract_sha256="b" * 64,
        aspect_terms={"noise": ("noise",)},
        polarity_terms={"supporting": ("quiet",), "opposing": ("loud",)},
    )
    reviews = [
        (1, 1.0, "noise one"),
        (2, 2.0, "noise two"),
        (3, 5.0, "noise three"),
        (4, 5.0, "noise four"),
        (5, 1.0, "noise five"),
        (6, 5.0, "noise six"),
        (7, 5.0, "noise seven"),
        (8, None, "noise eight"),
    ]
    with sqlite3.connect(store) as connection:
        connection.executemany(
            "INSERT INTO products(parent_asin,title) VALUES (?,?)",
            [("P-LOUD", "Loud one"), ("P-CALM", "Calm one")],
        )
        for line, rating, text in reviews[:4]:
            connection.execute(
                "INSERT INTO reviews(evidence_id,source_line,parent_asin,text,rating,timestamp,verified_purchase,helpful_vote)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (compute_evidence_id(snapshot, line), line, "P-LOUD", text, rating, line, 1, 0),
            )
        for line, rating, text in reviews[4:]:
            connection.execute(
                "INSERT INTO reviews(evidence_id,source_line,parent_asin,text,rating,timestamp,verified_purchase,helpful_vote)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (compute_evidence_id(snapshot, line), line, "P-CALM", text, rating, line, 1, 0),
            )
    return store


def _labels_fixture(tmp_path: Path, records: list[dict[str, str]]) -> Path:
    path = tmp_path / "model_labels.jsonl"
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    return path


def test_build_aspect_reference_joins_labels_to_products(tmp_path):
    store = _store_fixture(tmp_path)
    labels = _labels_fixture(
        tmp_path,
        [
            {"id": "noise:1", "polarity": "opposing", "reason": "normal"},
            {"id": "noise:2", "polarity": "opposing", "reason": "off_topic"},
            {"id": "noise:3", "polarity": "supporting", "reason": "normal"},
            {"id": "noise:4", "polarity": "insufficient", "reason": "vague"},
            # P-CALM: only one direction label, below the min-direction floor.
            {"id": "noise:5", "polarity": "opposing", "reason": "normal"},
            {"id": "noise:6", "polarity": "insufficient", "reason": "off_topic"},
        ],
    )
    reference = build_aspect_reference(store, labels, min_direction=2)
    assert set(reference) == {"P-LOUD"}
    counts = reference["P-LOUD"]["noise"]
    assert (counts.labelled, counts.direction, counts.supporting, counts.opposing) == (4, 3, 1, 2)
    assert counts.off_topic == 0 and counts.still_unclear == 1
    assert counts.opposing_share == 2 / 3
    quiet = _labels_fixture(
        tmp_path,
        [
            {"id": "noise:1", "polarity": "opposing", "reason": "normal"},
            {"id": "noise:2", "polarity": "opposing", "reason": "off_topic"},
            {"id": "noise:3", "polarity": "opposing", "reason": "normal"},
            {"id": "noise:4", "polarity": "opposing", "reason": "vague"},
        ],
    )
    loud = build_aspect_reference(store, quiet, min_direction=2)["P-LOUD"]["noise"]
    assert reference_verdict(loud) == "pain"
    assert reference_verdict(counts) == "pain"  # opposing_share 2/3 clears the 0.5 threshold


def test_verdict_helpers_follow_the_half_threshold():
    counts = AspectReferenceCounts(
        labelled=10, direction=6, supporting=4, opposing=2, conflicting=0, off_topic=3, still_unclear=1
    )
    assert reference_verdict(counts) == "ok"
    skewed = AspectReferenceCounts(
        labelled=10, direction=6, supporting=2, opposing=4, conflicting=0, off_topic=3, still_unclear=1
    )
    assert reference_verdict(skewed) == "pain"
    assert deterministic_verdict(3, 3) == "pain"
    assert deterministic_verdict(0, 0) is None


def test_agreement_summary_scores_and_discloses_per_aspect():
    reference = {
        "P-LOUD": {
            "noise": AspectReferenceCounts(
                labelled=6, direction=4, supporting=1, opposing=3, conflicting=0, off_topic=1, still_unclear=1
            )
        },
        "P-CALM": {
            "noise": AspectReferenceCounts(
                labelled=4, direction=2, supporting=2, opposing=0, conflicting=0, off_topic=1, still_unclear=1
            )
        },
    }
    deterministic = {
        ("P-LOUD", "noise"): (1, 3),
        ("P-CALM", "noise"): (2, 0),
        ("P-GHOST", "noise"): (1, 1),
    }
    summary = agreement_summary(reference, deterministic)
    assert summary["comparable_total"] == 2
    assert summary["agree_total"] == 2
    assert summary["agreement_rate"] == 1.0
    assert summary["per_aspect"]["noise"]["comparable"] == 2
