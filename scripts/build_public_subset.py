"""Build the small publishable store that ships with the project.

The full category store is 165 MB and must stay local. This keeps only the three
controlled products the workbench uses (11,958 reviews, 2.81 MB of text), which
is what a downloaded copy needs to run without the raw dataset.

Two details matter:

- rowids are preserved, because ``reviews_fts`` joins to ``reviews`` on rowid;
- ``store_meta`` is copied verbatim, so the dataset snapshot id, contract hash,
  and aspect vocabulary still match the frozen config and the lineage check keeps
  passing on the published copy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path

from ecom_review_intelligence.agent_cli import _validate_store_lineage
from ecom_review_intelligence.workbench_resources import load_local_agent_resources

_TABLE_ORDER = ("products", "store_meta", "reviews")
_SKIP_TABLES = {"reviews_fts"}


def _schema(connection: sqlite3.Connection) -> list[str]:
    """Collect the DDL needed to rebuild the published store, minus FTS shadows."""

    statements: list[str] = []
    for name, sql in connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY rowid"
    ):
        if sql is None or name.startswith("reviews_fts_"):
            continue
        if name == "reviews_fts":
            statements.append(sql)
            continue
        statements.append(sql)
    return statements


def build(source: Path, output: Path, parent_asins: Sequence[str]) -> dict[str, object]:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    placeholders = ",".join("?" for _ in parent_asins)

    with sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True) as reader:
        reader.row_factory = sqlite3.Row
        products = reader.execute(
            f"SELECT parent_asin, title FROM products WHERE parent_asin IN ({placeholders})",
            list(parent_asins),
        ).fetchall()
        reviews = reader.execute(
            f"""SELECT rowid, evidence_id, source_line, parent_asin, text, rating, timestamp,
                       verified_purchase, helpful_vote
                FROM reviews WHERE parent_asin IN ({placeholders}) ORDER BY rowid""",
            list(parent_asins),
        ).fetchall()
        meta = reader.execute("SELECT key, value FROM store_meta").fetchall()
        schema = _schema(reader)

    with sqlite3.connect(output) as writer:
        for statement in schema:
            writer.execute(statement)
        writer.executemany(
            "INSERT INTO products (parent_asin, title) VALUES (?, ?)",
            [(row["parent_asin"], row["title"]) for row in products],
        )
        writer.executemany(
            "INSERT INTO store_meta (key, value) VALUES (?, ?)",
            [(row["key"], row["value"]) for row in meta],
        )
        writer.executemany(
            """INSERT INTO reviews
                   (rowid, evidence_id, source_line, parent_asin, text, rating, timestamp,
                    verified_purchase, helpful_vote)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    row["rowid"],
                    row["evidence_id"],
                    row["source_line"],
                    row["parent_asin"],
                    row["text"],
                    row["rating"],
                    row["timestamp"],
                    row["verified_purchase"],
                    row["helpful_vote"],
                )
                for row in reviews
            ],
        )
        writer.executemany(
            "INSERT INTO reviews_fts (rowid, text) VALUES (?, ?)",
            [(row["rowid"], row["text"]) for row in reviews],
        )
        writer.commit()
        writer.execute("VACUUM")

    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    with sqlite3.connect(f"file:{output.as_posix()}?mode=ro", uri=True) as check:
        return {
            "output": str(output),
            "bytes": output.stat().st_size,
            "sha256": digest,
            "products": check.execute("SELECT count(*) FROM products").fetchone()[0],
            "reviews": check.execute("SELECT count(*) FROM reviews").fetchone()[0],
            "fts_rows": check.execute("SELECT count(*) FROM reviews_fts").fetchone()[0],
            "text_chars": check.execute("SELECT sum(length(text)) FROM reviews").fetchone()[0],
        }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--output", type=Path, default=None, help="Default: data/public/ice_makers_public.sqlite"
    )
    args = parser.parse_args(argv)

    root = args.project_root.resolve()
    store, _checkpoint, config, product_ids = load_local_agent_resources(root)
    output = args.output or root / "data" / "public" / "ice_makers_public.sqlite"
    summary = build(store, output, tuple(product_ids.values()))
    _validate_store_lineage(output, config)
    summary["lineage"] = "PASSED"
    print(json.dumps(summary, ensure_ascii=False, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
