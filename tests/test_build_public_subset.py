"""The published subset keeps provenance, rowids, and only the chosen products."""

from __future__ import annotations

import importlib
import sqlite3

import pytest

from ecom_review_intelligence.engine import compute_evidence_id, initialize_store

subset_builder = importlib.import_module("scripts.build_public_subset")

_KEEP = "B0KEEPME01"
_DROP = "B0DROPME01"
_ASPECTS = {"noise": ("noise", "loud")}
_POLARITY = {"opposing": ("loud",)}


def _source_store(path) -> None:
    initialize_store(
        path,
        snapshot_id="snapshot-test",
        contract_sha256="contract-test",
        aspect_terms=_ASPECTS,
        polarity_terms=_POLARITY,
    )
    with sqlite3.connect(path) as connection:
        connection.executemany(
            "INSERT INTO products (parent_asin, title) VALUES (?, ?)",
            [(_KEEP, "Kept product"), (_DROP, "Dropped product")],
        )
        rows = [
            (_KEEP, 1, "this unit is loud"),
            (_KEEP, 2, "noise again"),
            (_DROP, 3, "unrelated"),
        ]
        for asin, line, text in rows:
            evidence_id = compute_evidence_id("snapshot-test", line)
            connection.execute(
                """INSERT INTO reviews
                       (evidence_id, source_line, parent_asin, text, rating, timestamp,
                        verified_purchase, helpful_vote)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (evidence_id, line, asin, text, 5.0, 100 + line, 1, 0),
            )
            connection.execute(
                "INSERT INTO reviews_fts (rowid, text) VALUES ((SELECT rowid FROM reviews WHERE source_line = ?), ?)",
                (line, text),
            )
        connection.commit()


def test_subset_keeps_meta_rowids_and_only_chosen_products(tmp_path) -> None:
    source = tmp_path / "source.sqlite"
    output = tmp_path / "public.sqlite"
    _source_store(source)

    summary = subset_builder.build(source, output, (_KEEP,))

    assert summary["products"] == 1
    assert summary["reviews"] == 2
    assert summary["fts_rows"] == 2

    with sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True) as reader:
        source_meta = dict(reader.execute("SELECT key, value FROM store_meta"))
        source_rowids = {
            row[0] for row in reader.execute("SELECT rowid FROM reviews WHERE parent_asin = ?", (_KEEP,))
        }
    with sqlite3.connect(f"file:{output.as_posix()}?mode=ro", uri=True) as reader:
        assert dict(reader.execute("SELECT key, value FROM store_meta")) == source_meta
        assert {
            row[0] for row in reader.execute("SELECT rowid FROM reviews")
        } == source_rowids
        assert [row[0] for row in reader.execute("SELECT parent_asin FROM products")] == [_KEEP]
        # The FTS index must still join to the same rows as the reviews table.
        joined = reader.execute(
            "SELECT count(*) FROM reviews_fts JOIN reviews ON reviews_fts.rowid = reviews.rowid"
        ).fetchone()[0]
        assert joined == 2


def test_subset_keeps_the_lineage_check_satisfied(tmp_path) -> None:
    from ecom_review_intelligence.agent_cli import _validate_store_lineage

    source = tmp_path / "source.sqlite"
    output = tmp_path / "public.sqlite"
    _source_store(source)
    subset_builder.build(source, output, (_KEEP,))

    config = {"dataset_snapshot_id": "snapshot-test", "contract_sha256": "contract-test"}
    _validate_store_lineage(output, config)

    with pytest.raises(ValueError):
        _validate_store_lineage(
            output, {"dataset_snapshot_id": "other", "contract_sha256": "contract-test"}
        )
