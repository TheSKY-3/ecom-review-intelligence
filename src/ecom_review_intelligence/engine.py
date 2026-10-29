"""Deterministic local evidence storage and aspect retrieval."""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

_TOKEN_PATTERN = re.compile(r"[a-z]+")


class EngineError(RuntimeError):
    """Raised when deterministic evidence retrieval cannot honor its contract."""


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    source_line: int
    parent_asin: str
    text: str
    rating: float | None
    timestamp: int | None
    verified_purchase: bool | None
    helpful_vote: int | None
    polarity: str


@dataclass(frozen=True)
class AspectQueryResult:
    aspect: str
    strength: str
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class LocalStoreBuild:
    store_path: Path
    dataset_snapshot_id: str
    contract_sha256: str
    product_count: int
    review_count: int
    git_revision: str
    content_sha256: str


def compute_evidence_id(dataset_snapshot_id: str, source_line: int) -> str:
    """Return the frozen V1 project-local review locator."""

    if source_line <= 0:
        raise EngineError("Source JSONL line must be one-based and positive.")
    payload = f"evidence-v1\n{dataset_snapshot_id}\nreviews\n{source_line}\n"
    return hashlib.sha256(payload.encode()).hexdigest()


def compute_store_content_sha256(store_path: Path) -> str:
    """Hash ordered product and review content without exporting local values."""

    digest = hashlib.sha256()
    queries = (
        (
            "store_meta",
            "SELECT key, value FROM store_meta ORDER BY key",
        ),
        (
            "products",
            "SELECT parent_asin, title FROM products ORDER BY parent_asin",
        ),
        (
            "reviews",
            "SELECT evidence_id, source_line, parent_asin, text, rating, timestamp, verified_purchase, helpful_vote FROM reviews ORDER BY source_line, evidence_id",
        ),
    )
    with _open_sqlite(store_path) as connection:
        for label, query in queries:
            digest.update(label.encode("ascii") + b"\0")
            for row in connection.execute(query):
                encoded = json.dumps(
                    list(row),
                    ensure_ascii=True,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode("ascii")
                digest.update(len(encoded).to_bytes(8, "big") + encoded)
    return digest.hexdigest()


def initialize_store(
    store_path: Path,
    *,
    snapshot_id: str,
    contract_sha256: str,
    aspect_terms: dict[str, tuple[str, ...]],
    polarity_terms: dict[str, tuple[str, ...]],
) -> None:
    """Create an empty local SQLite store bound to one frozen evidence contract."""

    store_path.parent.mkdir(parents=True, exist_ok=True)
    with _open_sqlite(store_path) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS store_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS products (
                parent_asin TEXT PRIMARY KEY,
                title TEXT
            );
            CREATE TABLE IF NOT EXISTS reviews (
                evidence_id TEXT PRIMARY KEY,
                source_line INTEGER NOT NULL UNIQUE,
                parent_asin TEXT NOT NULL REFERENCES products(parent_asin),
                text TEXT NOT NULL,
                rating REAL,
                timestamp INTEGER,
                verified_purchase INTEGER,
                helpful_vote INTEGER
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS reviews_fts USING fts5(text);
            """
        )
        metadata = {
            "dataset_snapshot_id": snapshot_id,
            "contract_sha256": contract_sha256,
            "aspect_terms": json.dumps(aspect_terms, sort_keys=True),
            "polarity_terms": json.dumps(polarity_terms, sort_keys=True),
        }
        connection.executemany(
            "INSERT OR REPLACE INTO store_meta(key, value) VALUES (?, ?)",
            metadata.items(),
        )


def build_local_store(project_root: Path, *, git_revision: str) -> LocalStoreBuild:
    """Atomically materialize only the frozen P3 category into ignored local SQLite."""

    root = project_root.resolve()
    p1 = _read_json(root / "artifacts" / "manifests" / "phase-1-download.json", "P1 manifest")
    scope = _read_json(root / "artifacts" / "manifests" / "phase-2-scope.json", "P2 scope")
    contract = _read_json(
        root / "artifacts" / "contracts" / "phase-3-evidence-policy.json",
        "P3 evidence contract",
    )
    if p1.get("state") != "PASSED":
        raise EngineError("P1 manifest must be PASSED before local-store build.")
    if scope.get("state") != "PASSED" or contract.get("state") != "PASSED":
        raise EngineError("P2 scope and P3 contract must be PASSED before local-store build.")
    snapshot_id = p1.get("dataset_snapshot_id")
    if (
        not isinstance(snapshot_id, str)
        or scope.get("dataset_snapshot_id") != snapshot_id
        or contract.get("dataset_snapshot_id") != snapshot_id
    ):
        raise EngineError("P1/P2/P3 dataset snapshot lineage does not match.")
    contract_copy = dict(contract)
    contract_sha256 = contract_copy.pop("contract_sha256", None)
    if not isinstance(contract_sha256, str) or _canonical_sha(contract_copy) != contract_sha256:
        raise EngineError("P3 contract SHA-256 does not match its canonical policy.")
    if scope.get("audit_sha256") != contract.get("audit_sha256"):
        raise EngineError("P2 audit lineage does not match the P3 contract.")
    selected_category = contract.get("selected_category")
    if not isinstance(selected_category, str):
        raise EngineError("P3 contract has no selected category.")
    aspect_terms = contract.get("aspect_terms")
    polarity_terms = contract.get("polarity_terms")
    if not isinstance(aspect_terms, dict) or not isinstance(polarity_terms, dict):
        raise EngineError("P3 contract has no valid aspect or polarity policy.")

    inputs = _manifest_inputs(root, p1)
    metadata_path = inputs["metadata"]
    review_path = inputs["reviews"]
    store_path = root / "data" / "processed" / "ice_makers_evidence.sqlite"
    temporary_path = store_path.with_suffix(".sqlite.tmp")
    if temporary_path.exists():
        temporary_path.unlink()
    initialize_store(
        temporary_path,
        snapshot_id=snapshot_id,
        contract_sha256=contract_sha256,
        aspect_terms={key: tuple(value) for key, value in aspect_terms.items()},
        polarity_terms={key: tuple(value) for key, value in polarity_terms.items()},
    )

    selected_parents = _insert_selected_products(temporary_path, metadata_path, selected_category)
    review_count = _insert_selected_reviews(
        temporary_path,
        review_path,
        selected_parents,
        snapshot_id,
    )
    expected_products = contract.get("selected_product_count")
    expected_reviews = contract.get("selected_review_count")
    if len(selected_parents) != expected_products or review_count != expected_reviews:
        temporary_path.unlink(missing_ok=True)
        raise EngineError("Local materialization row counts do not match the frozen P3 contract.")
    with _open_sqlite(temporary_path) as connection:
        connection.execute(
            "INSERT OR REPLACE INTO store_meta(key, value) VALUES (?, ?)",
            ("build_git_revision", git_revision),
        )
    content_sha256 = compute_store_content_sha256(temporary_path)
    temporary_path.replace(store_path)
    return LocalStoreBuild(
        store_path=store_path,
        dataset_snapshot_id=snapshot_id,
        contract_sha256=contract_sha256,
        product_count=len(selected_parents),
        review_count=review_count,
        git_revision=git_revision,
        content_sha256=content_sha256,
    )


def write_phase4_outputs(
    project_root: Path,
    result: LocalStoreBuild,
    *,
    verified_at: str,
) -> tuple[Path, Path]:
    """Write aggregate P4 provenance without exporting local rows or store paths."""

    payload = {
        "state": "PASSED",
        "verified_at": verified_at,
        "build_git_revision": result.git_revision,
        "dataset_snapshot_id": result.dataset_snapshot_id,
        "contract_sha256": result.contract_sha256,
        "product_count": result.product_count,
        "review_count": result.review_count,
        "fts_row_count": result.review_count,
        "content_sha256": result.content_sha256,
        "join_policy": "exact product-key equality only",
        "citation_policy": "sha256 evidence-v1 with snapshot, reviews role, and one-based source line",
        "data_boundary": "Aggregate-only; no local paths, product IDs, evidence IDs, or review text.",
    }
    summary_path = project_root / "artifacts" / "store-summaries" / "phase-4-build.json"
    report_path = project_root / "docs" / "reports" / "phase-4-local-store.md"
    _atomic_write_text(summary_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    _atomic_write_text(
        report_path,
        "\n".join(
            [
                "# P4 local evidence-store build",
                "",
                "- State: PASSED",
                f"- Build Git revision: {result.git_revision}",
                f"- Dataset snapshot ID: {result.dataset_snapshot_id}",
                f"- P3 contract SHA-256: {result.contract_sha256}",
                f"- Products materialized: {result.product_count}",
                f"- Reviews materialized: {result.review_count}",
                f"- FTS rows materialized: {result.review_count}",
                f"- Store content SHA-256: {result.content_sha256}",
                "- Join policy: exact product-key equality only.",
                "- Citation policy: local evidence-v1 locator derived from frozen snapshot and source line.",
                "",
                "Aggregate-only; no local paths, product IDs, evidence IDs, or review text.",
                "",
            ]
        ),
    )
    return summary_path, report_path


def query_aspect_evidence(
    store_path: Path,
    parent_asins: tuple[str, ...],
    aspect: str,
    *,
    limit: int | None = None,
) -> tuple[Evidence, ...]:
    """Return matching evidence, optionally capped.

    ``limit=None`` returns every matching review. This function and
    ``query_aspect`` share one predicate and one polarity rule, so a full export
    can never disagree with a sampled diagnosis about how a review was labeled.
    """

    if not parent_asins:
        raise EngineError("At least one product parent_asin is required.")
    if limit is not None and limit <= 0:
        raise EngineError("Query limit must be positive.")
    with _open_sqlite(store_path) as connection:
        connection.row_factory = sqlite3.Row
        metadata = dict(connection.execute("SELECT key, value FROM store_meta"))
        aspect_terms = json.loads(metadata["aspect_terms"])
        if aspect not in aspect_terms:
            raise EngineError("Requested aspect is not enabled by the frozen contract.")
        polarity_terms = json.loads(metadata["polarity_terms"])
        predicate, parameters = _aspect_predicate(parent_asins, aspect_terms[aspect])
        statement = f"""
            SELECT evidence_id, source_line, parent_asin, text, rating, timestamp,
                   verified_purchase, helpful_vote
            FROM reviews
            WHERE {predicate}
            ORDER BY source_line
            """
        if limit is None:
            rows = connection.execute(statement, parameters).fetchall()
        else:
            rows = connection.execute(f"{statement} LIMIT ?", [*parameters, limit]).fetchall()

    return tuple(
        Evidence(
            evidence_id=row["evidence_id"],
            source_line=row["source_line"],
            parent_asin=row["parent_asin"],
            text=row["text"],
            rating=row["rating"],
            timestamp=row["timestamp"],
            verified_purchase=None
            if row["verified_purchase"] is None
            else bool(row["verified_purchase"]),
            helpful_vote=row["helpful_vote"],
            polarity=_polarity(row["text"], polarity_terms),
        )
        for row in rows
    )


def query_aspect(
    store_path: Path,
    parent_asins: tuple[str, ...],
    aspect: str,
    *,
    limit: int,
) -> AspectQueryResult:
    """Return exact-product local evidence matching a frozen aspect vocabulary."""

    if not parent_asins:
        raise EngineError("At least one product parent_asin is required.")
    if limit <= 0:
        raise EngineError("Query limit must be positive.")
    evidence = query_aspect_evidence(store_path, parent_asins, aspect, limit=limit)
    return AspectQueryResult(aspect=aspect, strength=_strength(len(evidence)), evidence=evidence)


def polarity_hits(text: str, polarity_terms: dict[str, list[str]]) -> tuple[str, tuple[str, ...]]:
    """Return the frozen polarity plus the exact terms that produced it.

    Exported sheets need to show *why* a review landed in a bucket. Deriving the
    terms here, next to the rule itself, keeps the explanation from drifting away
    from the classification it explains.
    """

    tokens = set(_TOKEN_PATTERN.findall(text.lower()))
    supporting = sorted(tokens.intersection(polarity_terms["supporting"]))
    opposing = sorted(tokens.intersection(polarity_terms["opposing"]))
    if supporting and opposing or supporting == opposing and supporting:
        bucket = "conflicting"
    elif len(supporting) > len(opposing):
        bucket = "supporting"
    elif len(opposing) > len(supporting):
        bucket = "opposing"
    else:
        bucket = "insufficient"
    return bucket, tuple(supporting + opposing)


def read_store_meta(store_path: Path) -> dict[str, str]:
    """Return the frozen store metadata as raw key/value strings."""

    with _open_sqlite(store_path) as connection:
        return dict(connection.execute("SELECT key, value FROM store_meta ORDER BY key"))


def _aspect_predicate(
    parent_asins: tuple[str, ...], terms: list[str]
) -> tuple[str, list[object]]:
    """The one WHERE fragment that defines "this review is about this aspect"."""

    product_placeholders = ",".join("?" for _ in parent_asins)
    term_predicates = " OR ".join("lower(text) LIKE ?" for _ in terms)
    return (
        f"parent_asin IN ({product_placeholders}) AND ({term_predicates})",
        [*parent_asins, *[f"%{term.lower()}%" for term in terms]],
    )


def count_aspect_matches(
    store_path: Path,
    parent_asins: tuple[str, ...],
    aspect: str,
) -> int:
    """Count reviews matching a frozen aspect vocabulary, before any sample cap.

    ``query_aspect`` returns at most ``limit`` rows and derives its strength tier
    from how many it returned, so "STRONG" there means "the sample was full". This
    count is what lets a caller state whether it read every matching review or
    only the first few, which is a different and load-bearing claim.
    """

    if not parent_asins:
        raise EngineError("At least one product parent_asin is required.")
    with _open_sqlite(store_path) as connection:
        metadata = dict(connection.execute("SELECT key, value FROM store_meta"))
        aspect_terms = json.loads(metadata["aspect_terms"])
        if aspect not in aspect_terms:
            raise EngineError("Requested aspect is not enabled by the frozen contract.")
        predicate, parameters = _aspect_predicate(parent_asins, aspect_terms[aspect])
        row = connection.execute(
            f"SELECT COUNT(*) FROM reviews WHERE {predicate}", parameters
        ).fetchone()
    return int(row[0])


_INSIGHT_SAMPLE_QUOTAS: tuple[tuple[str, int], ...] = (
    ("low", 2),
    ("high", 2),
    ("mid", 1),
    ("unknown", 1),
)


def _rating_bucket(rating: float | None) -> str:
    if rating is None:
        return "unknown"
    if rating <= 2:
        return "low"
    if rating == 3:
        return "mid"
    return "high"


def select_stratified_evidence(
    candidates: list[sqlite3.Row], limit: int
) -> list[sqlite3.Row]:
    """Pick a deterministic rating-stratified sample from per-bucket candidates.

    Review files are ordered by download order, so taking the first rows would
    read the oldest (and typically most positive) reviews of a product. This
    keeps every click reproducible while covering both ends of the rating range.
    """

    by_bucket: dict[str, list[sqlite3.Row]] = {"low": [], "mid": [], "high": [], "unknown": []}
    for row in candidates:
        by_bucket[_rating_bucket(row["rating"])].append(row)
    for bucket in by_bucket.values():
        bucket.sort(key=lambda row: row["source_line"])

    picked: list[sqlite3.Row] = []
    for bucket, quota in _INSIGHT_SAMPLE_QUOTAS:
        picked.extend(by_bucket[bucket][:quota])

    if len(picked) < limit:
        chosen = {id(row) for row in picked}
        leftovers = sorted(
            (row for bucket in by_bucket.values() for row in bucket if id(row) not in chosen),
            key=lambda row: row["source_line"],
        )
        picked.extend(leftovers[: limit - len(picked)])

    sample = picked[:limit]
    sample.sort(key=lambda row: row["source_line"])
    return sample


def query_stratified_aspect(
    store_path: Path,
    parent_asins: tuple[str, ...],
    aspect: str,
    *,
    limit: int,
) -> AspectQueryResult:
    """Sample a frozen aspect with a fixed rating mix instead of the first rows.

    Used by the opt-in AI step: the model explains a sample, so that sample must
    not be "the five oldest reviews" of an otherwise large matched set.
    """

    if not parent_asins:
        raise EngineError("At least one product parent_asin is required.")
    if limit <= 0:
        raise EngineError("Query limit must be positive.")
    with _open_sqlite(store_path) as connection:
        connection.row_factory = sqlite3.Row
        metadata = dict(connection.execute("SELECT key, value FROM store_meta"))
        aspect_terms = json.loads(metadata["aspect_terms"])
        if aspect not in aspect_terms:
            raise EngineError("Requested aspect is not enabled by the frozen contract.")
        polarity_terms = json.loads(metadata["polarity_terms"])
        product_placeholders = ",".join("?" for _ in parent_asins)
        term_predicates = " OR ".join("lower(text) LIKE ?" for _ in aspect_terms[aspect])
        rows = connection.execute(
            f"""
            WITH matched AS (
                SELECT evidence_id, source_line, parent_asin, text, rating, timestamp,
                       verified_purchase, helpful_vote,
                       CASE
                           WHEN rating IS NULL THEN 'unknown'
                           WHEN rating <= 2 THEN 'low'
                           WHEN rating = 3 THEN 'mid'
                           ELSE 'high'
                       END AS bucket
                FROM reviews
                WHERE parent_asin IN ({product_placeholders}) AND ({term_predicates})
            ),
            ranked AS (
                SELECT *, row_number() OVER (
                    PARTITION BY bucket ORDER BY source_line
                ) AS bucket_rank
                FROM matched
            )
            SELECT * FROM ranked WHERE bucket_rank <= ? ORDER BY bucket, bucket_rank
            """,
            [
                *parent_asins,
                *[f"%{term.lower()}%" for term in aspect_terms[aspect]],
                limit,
            ],
        ).fetchall()

    evidence = tuple(
        Evidence(
            evidence_id=row["evidence_id"],
            source_line=row["source_line"],
            parent_asin=row["parent_asin"],
            text=row["text"],
            rating=row["rating"],
            timestamp=row["timestamp"],
            verified_purchase=None
            if row["verified_purchase"] is None
            else bool(row["verified_purchase"]),
            helpful_vote=row["helpful_vote"],
            polarity=_polarity(row["text"], polarity_terms),
        )
        for row in select_stratified_evidence(rows, limit)
    )
    return AspectQueryResult(aspect=aspect, strength=_strength(len(evidence)), evidence=evidence)


def fts_query_aspect(
    store_path: Path,
    parent_asins: tuple[str, ...],
    aspect: str,
    *,
    limit: int,
) -> AspectQueryResult:
    """Return exact-product evidence ranked only by frozen FTS5/BM25 terms."""

    if not parent_asins:
        raise EngineError("At least one product parent_asin is required.")
    if limit <= 0:
        raise EngineError("Query limit must be positive.")
    with _open_sqlite(store_path) as connection:
        connection.row_factory = sqlite3.Row
        metadata = dict(connection.execute("SELECT key, value FROM store_meta"))
        aspect_terms = json.loads(metadata["aspect_terms"])
        if aspect not in aspect_terms:
            raise EngineError("Requested aspect is not enabled by the frozen contract.")
        polarity_terms = json.loads(metadata["polarity_terms"])
        product_placeholders = ",".join("?" for _ in parent_asins)
        rows = connection.execute(
            f"""
            SELECT reviews.evidence_id, reviews.source_line, reviews.parent_asin, reviews.text,
                   reviews.rating, reviews.timestamp, reviews.verified_purchase, reviews.helpful_vote
            FROM reviews_fts
            JOIN reviews ON reviews_fts.rowid = reviews.rowid
            WHERE reviews.parent_asin IN ({product_placeholders}) AND reviews_fts MATCH ?
            ORDER BY bm25(reviews_fts), reviews.source_line
            LIMIT ?
            """,
            [*parent_asins, _fts_expression(aspect_terms[aspect]), limit],
        ).fetchall()

    evidence = tuple(
        Evidence(
            evidence_id=row["evidence_id"],
            source_line=row["source_line"],
            parent_asin=row["parent_asin"],
            text=row["text"],
            rating=row["rating"],
            timestamp=row["timestamp"],
            verified_purchase=None
            if row["verified_purchase"] is None
            else bool(row["verified_purchase"]),
            helpful_vote=row["helpful_vote"],
            polarity=_polarity(row["text"], polarity_terms),
        )
        for row in rows
    )
    return AspectQueryResult(aspect=aspect, strength=_strength(len(evidence)), evidence=evidence)


def verify_citation(store_path: Path, evidence_id: str) -> bool:
    """Check that an evidence locator maps back to its frozen source-line formula."""

    with _open_sqlite(store_path) as connection:
        row = connection.execute(
            "SELECT source_line FROM reviews WHERE evidence_id = ?",
            (evidence_id,),
        ).fetchone()
        snapshot = connection.execute(
            "SELECT value FROM store_meta WHERE key = 'dataset_snapshot_id'"
        ).fetchone()
    if row is None or snapshot is None:
        return False
    return compute_evidence_id(snapshot[0], row[0]) == evidence_id


def _polarity(text: str, polarity_terms: dict[str, list[str]]) -> str:
    return polarity_hits(text, polarity_terms)[0]


def _strength(count: int) -> str:
    if count == 0:
        return "INSUFFICIENT"
    if count == 1:
        return "WEAK"
    if count <= 4:
        return "MODERATE"
    return "STRONG"


def _fts_expression(terms: object) -> str:
    if not isinstance(terms, list) or not terms:
        raise EngineError("Frozen aspect terms are invalid for FTS5 retrieval.")
    normalized: list[str] = []
    for term in terms:
        if not isinstance(term, str) or not re.fullmatch(r"[a-z]+", term):
            raise EngineError("Frozen aspect terms are invalid for FTS5 retrieval.")
        normalized.append(term)
    return " OR ".join(normalized)


def _manifest_inputs(root: Path, manifest: dict[str, object]) -> dict[str, Path]:
    records = manifest.get("sources")
    if not isinstance(records, list):
        raise EngineError("P1 manifest has no source registrations.")
    raw_root = (root / "data" / "raw").resolve()
    expected_filenames = {"reviews": "Appliances.jsonl.gz", "metadata": "meta_Appliances.jsonl.gz"}
    paths: dict[str, Path] = {}
    for record in records:
        if not isinstance(record, dict):
            raise EngineError("P1 source registration is invalid.")
        source_name = record.get("source_name")
        filename = record.get("filename")
        total_bytes = record.get("total_bytes")
        sha256 = record.get("sha256")
        if (
            not isinstance(source_name, str)
            or not isinstance(filename, str)
            or not isinstance(total_bytes, int)
            or not isinstance(sha256, str)
            or source_name not in expected_filenames
            or filename != expected_filenames[source_name]
        ):
            raise EngineError("P1 source registration does not match the approved source role.")
        path = (raw_root / filename).resolve()
        if not path.is_relative_to(raw_root) or not path.exists() or path.stat().st_size != total_bytes:
            raise EngineError("P1 source path or byte size does not match the frozen manifest.")
        if _file_sha256(path) != sha256:
            raise EngineError("P1 source SHA-256 does not match the frozen manifest.")
        paths[source_name] = path
    if set(paths) != {"reviews", "metadata"}:
        raise EngineError("P1 manifest must provide reviews and metadata paths.")
    return paths


def _insert_selected_products(
    store_path: Path,
    metadata_path: Path,
    selected_category: str,
) -> set[str]:
    selected_parents: set[str] = set()
    with _open_sqlite(store_path) as connection, gzip.open(
        metadata_path, "rt", encoding="utf-8"
    ) as source:
        for line in source:
            record = _json_object(line, metadata_path.name)
            parent_asin = record.get("parent_asin")
            if not isinstance(parent_asin, str) or _category_label(record.get("categories")) != selected_category:
                continue
            if parent_asin in selected_parents:
                raise EngineError("Selected metadata has duplicate parent_asin values.")
            title = record.get("title") if isinstance(record.get("title"), str) else None
            connection.execute(
                "INSERT INTO products(parent_asin, title) VALUES (?, ?)",
                (parent_asin, title),
            )
            selected_parents.add(parent_asin)
    return selected_parents


def _insert_selected_reviews(
    store_path: Path,
    review_path: Path,
    selected_parents: set[str],
    snapshot_id: str,
) -> int:
    count = 0
    with _open_sqlite(store_path) as connection, gzip.open(
        review_path, "rt", encoding="utf-8"
    ) as source:
        for source_line, line in enumerate(source, start=1):
            record = _json_object(line, review_path.name)
            parent_asin = record.get("parent_asin")
            if parent_asin not in selected_parents:
                continue
            text = record.get("text") if isinstance(record.get("text"), str) else ""
            cursor = connection.execute(
                """
                INSERT INTO reviews(
                    evidence_id, source_line, parent_asin, text, rating, timestamp, verified_purchase, helpful_vote
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    compute_evidence_id(snapshot_id, source_line),
                    source_line,
                    parent_asin,
                    text,
                    record.get("rating") if isinstance(record.get("rating"), (int, float)) else None,
                    record.get("timestamp") if isinstance(record.get("timestamp"), int) else None,
                    int(record["verified_purchase"]) if isinstance(record.get("verified_purchase"), bool) else None,
                    record.get("helpful_vote") if isinstance(record.get("helpful_vote"), int) else None,
                ),
            )
            connection.execute(
                "INSERT INTO reviews_fts(rowid, text) VALUES (?, ?)",
                (cursor.lastrowid, text),
            )
            count += 1
    return count


def _read_json(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EngineError(f"Cannot read {label}.") from error
    if not isinstance(value, dict):
        raise EngineError(f"{label} is not a JSON object.")
    return value


@contextmanager
def _open_sqlite(path: Path) -> Iterator[sqlite3.Connection]:
    """Yield one transaction and deterministically close its Windows file handle."""

    connection = sqlite3.connect(path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _category_label(value: object) -> str | None:
    if not isinstance(value, list):
        return None
    labels = [" ".join(item.split()) for item in value if isinstance(item, str) and item.strip()]
    return labels[-1] if labels else None


def _json_object(line: str, source_name: str) -> dict[str, object]:
    try:
        value = json.loads(line)
    except json.JSONDecodeError as error:
        raise EngineError(f"Malformed JSON in {source_name}.") from error
    if not isinstance(value, dict):
        raise EngineError(f"Non-object JSON record in {source_name}.")
    return value


def _canonical_sha(value: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(content, encoding="utf-8")
    temporary_path.replace(path)
