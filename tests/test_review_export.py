import csv
import json
import sqlite3
from pathlib import Path

import pytest

from ecom_review_intelligence.engine import compute_evidence_id, initialize_store
from ecom_review_intelligence.review_export import (
    COLUMNS,
    EXPORT_SCHEMA_VERSION,
    ExportError,
    build_export_rows,
    export_product_aspect,
    format_review_timestamp,
)

SNAPSHOT_ID = "a" * 64
CONTRACT_SHA256 = "b" * 64

REVIEWS: tuple[tuple[int, str, str], ...] = (
    (1, "product-a", "very quiet machine"),
    (2, "product-a", "loud at night"),
    (3, "product-a", "quiet, but the motor is loud"),
    (4, "product-a", "it makes ice"),
    (5, "product-a", "quiet again"),
    (6, "product-a", "loud and quiet together"),
    (7, "product-b", "quiet machine"),
)


def _build_store(tmp_path: Path) -> Path:
    store_path = tmp_path / "evidence.sqlite"
    initialize_store(
        store_path,
        snapshot_id=SNAPSHOT_ID,
        contract_sha256=CONTRACT_SHA256,
        aspect_terms={"noise": ("ice", "quiet", "loud", "motor")},
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
                evidence_id, source_line, parent_asin, text, rating, timestamp,
                verified_purchase, helpful_vote
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    compute_evidence_id(SNAPSHOT_ID, line),
                    line,
                    product,
                    text,
                    3.0,
                    1_700_000_000 + line,
                    1,
                    line,
                )
                for line, product, text in REVIEWS
            ],
        )
    return store_path


def _rows(store_path: Path, **overrides: object):
    arguments = {
        "product_code": "frostline",
        "product_id": "product-a",
        "aspect": "noise",
        "participated_ids": frozenset({compute_evidence_id(SNAPSHOT_ID, 1)}),
    }
    arguments.update(overrides)
    return build_export_rows(store_path, **arguments)


def test_each_review_is_bucketed_by_the_frozen_polarity_rule(tmp_path: Path) -> None:
    store_path = _build_store(tmp_path)

    rows, matched_total, truncated = _rows(store_path)

    assert matched_total == 6
    assert truncated is False
    assert [row.polarity for row in rows] == [
        "supporting",
        "opposing",
        "conflicting",
        "insufficient",
        "supporting",
        "conflicting",
    ]
    assert rows[0].bucket_label == "支持"
    assert rows[3].bucket_label == "证据不足"


def test_hit_terms_explain_why_a_review_landed_in_its_bucket(tmp_path: Path) -> None:
    store_path = _build_store(tmp_path)

    rows, _total, _truncated = _rows(store_path)

    assert rows[0].hit_terms == ("quiet",)
    assert rows[1].hit_terms == ("loud",)
    assert rows[2].hit_terms == ("quiet", "loud")
    assert rows[3].hit_terms == ()


def test_only_the_reviews_the_agent_judged_are_marked(tmp_path: Path) -> None:
    store_path = _build_store(tmp_path)
    judged = frozenset(
        {compute_evidence_id(SNAPSHOT_ID, 1), compute_evidence_id(SNAPSHOT_ID, 2)}
    )

    rows, _total, _truncated = _rows(store_path, participated_ids=judged)

    assert [row.participated for row in rows] == [True, True, False, False, False, False]


def test_another_product_is_not_mixed_in(tmp_path: Path) -> None:
    store_path = _build_store(tmp_path)

    rows, matched_total, _truncated = _rows(store_path)

    assert matched_total == 6
    assert {row.product_id for row in rows} == {"product-a"}


def test_the_cap_is_recorded_rather_than_silently_applied(tmp_path: Path) -> None:
    store_path = _build_store(tmp_path)

    rows, matched_total, truncated = _rows(store_path, max_rows=2)

    assert truncated is True
    assert len(rows) == 2
    assert matched_total == 6


def test_unknown_aspect_and_bad_cap_are_refused(tmp_path: Path) -> None:
    store_path = _build_store(tmp_path)

    with pytest.raises(ExportError, match="frozen contract"):
        _rows(store_path, aspect="unfrozen")
    with pytest.raises(ExportError, match="positive"):
        _rows(store_path, max_rows=0)


def test_the_csv_opens_in_excel_with_chinese_headers(tmp_path: Path) -> None:
    store_path = _build_store(tmp_path)
    out = tmp_path / "export"

    result = export_product_aspect(
        store_path,
        out,
        product_code="frostline",
        product_id="product-a",
        product_label="Frostline",
        aspect="noise",
        participated_ids=frozenset({compute_evidence_id(SNAPSHOT_ID, 1)}),
        judged_limit=5,
        generated_at="2026-10-29T10:00:00+00:00",
    )

    raw = result.csv_path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    with result.csv_path.open(encoding="utf-8-sig", newline="") as handle:
        table = list(csv.reader(handle))
    assert tuple(table[0]) == COLUMNS
    assert len(table) == len(REVIEWS[:6]) + 1
    assert table[1][0] == compute_evidence_id(SNAPSHOT_ID, 1)
    assert table[1][4] == "支持"
    assert table[1][11] == "是"
    assert table[2][11] == "否"


def test_a_review_containing_commas_or_quotes_stays_in_one_row(tmp_path: Path) -> None:
    store_path = _build_store(tmp_path)
    out = tmp_path / "export"

    result = export_product_aspect(
        store_path,
        out,
        product_code="frostline",
        product_id="product-a",
        product_label="Frostline",
        aspect="noise",
        participated_ids=frozenset(),
        judged_limit=5,
        generated_at="2026-10-29T10:00:00+00:00",
    )

    with result.csv_path.open(encoding="utf-8-sig", newline="") as handle:
        table = list(csv.reader(handle))
    conflicting = next(row for row in table if row[4] == "冲突")
    assert conflicting[12] == "quiet, but the motor is loud"


def test_the_sidecar_carries_lineage_and_totals_without_review_text(tmp_path: Path) -> None:
    store_path = _build_store(tmp_path)
    out = tmp_path / "export"

    export_product_aspect(
        store_path,
        out,
        product_code="frostline",
        product_id="product-a",
        product_label="Frostline",
        aspect="noise",
        participated_ids=frozenset({compute_evidence_id(SNAPSHOT_ID, 1)}),
        judged_limit=5,
        generated_at="2026-10-29T10:00:00+00:00",
    )

    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))

    assert meta["schema_version"] == EXPORT_SCHEMA_VERSION
    assert meta["dataset_snapshot_id"] == SNAPSHOT_ID
    assert meta["contract_sha256"] == CONTRACT_SHA256
    assert meta["matched_total"] == 6
    assert meta["exported_rows"] == 6
    assert meta["participated_count"] == 1
    assert meta["bucket_counts"] == {
        "supporting": 2,
        "opposing": 1,
        "conflicting": 2,
        "insufficient": 1,
    }
    assert meta["truncated"] is False
    assert "quiet machine" not in json.dumps(meta)


def test_split_writes_one_file_per_non_empty_bucket_only(tmp_path: Path) -> None:
    store_path = _build_store(tmp_path)
    out = tmp_path / "export"

    result = export_product_aspect(
        store_path,
        out,
        product_code="frostline",
        product_id="product-a",
        product_label="Frostline",
        aspect="noise",
        participated_ids=frozenset(),
        judged_limit=5,
        split=True,
        generated_at="2026-10-29T10:00:00+00:00",
    )

    names = sorted(path.name for path in result.split_paths)
    assert names == [
        "reviews-conflicting.csv",
        "reviews-insufficient.csv",
        "reviews-opposing.csv",
        "reviews-supporting.csv",
    ]
    assert all(path.exists() for path in result.split_paths)


def test_epoch_seconds_and_milliseconds_both_format() -> None:
    # The frozen store keeps epoch milliseconds; 13 digits must not become an
    # empty cell just because the code assumed seconds.
    assert format_review_timestamp(1_700_000_000) == "2023-11-14 22:13"
    assert format_review_timestamp(1_700_000_000_000) == "2023-11-14 22:13"
    assert format_review_timestamp(None) == ""
    assert format_review_timestamp(10**20) == ""


def test_the_readme_says_what_the_table_is_and_is_not(tmp_path: Path) -> None:
    store_path = _build_store(tmp_path)
    out = tmp_path / "export"

    export_product_aspect(
        store_path,
        out,
        product_code="frostline",
        product_id="product-a",
        product_label="Frostline",
        aspect="noise",
        participated_ids=frozenset({compute_evidence_id(SNAPSHOT_ID, 1)}),
        judged_limit=5,
        generated_at="2026-10-29T10:00:00+00:00",
    )

    readme = (out / "说明.md").read_text(encoding="utf-8")

    assert "全库匹配：6 条" in readme
    assert "其中参与过本次判断：1 条" in readme
    assert "不进入版本库" in readme
    assert SNAPSHOT_ID in readme
