"""Deterministic local product resolution and P6 evidence workflows."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from ecom_review_intelligence.engine import (
    AspectQueryResult,
    EngineError,
    Evidence,
    query_aspect,
    verify_citation,
)


class AgentToolError(RuntimeError):
    """Raised when an Agent tool request cannot satisfy the frozen contract."""


@dataclass(frozen=True)
class ProductCandidate:
    product_id: str
    title: str | None


@dataclass(frozen=True)
class LocalEvidenceItem:
    product_id: str
    aspect: str
    strength: str
    evidence: tuple[Evidence, ...]

    @property
    def negative_or_conflicting_count(self) -> int:
        return sum(item.polarity in {"opposing", "conflicting"} for item in self.evidence)


@dataclass(frozen=True)
class WorkflowToolResult:
    workflow: str
    items: tuple[LocalEvidenceItem, ...]


@dataclass
class ToolInvocationLedger:
    execution_count: int = field(default=0, init=False)

    def consume(self) -> None:
        if self.execution_count >= 1:
            raise AgentToolError("A run may execute only one business evidence tool.")
        self.execution_count += 1


class ProductResolver:
    def __init__(self, store_path: Path) -> None:
        self.store_path = store_path

    def by_exact_id(self, product_id: str) -> ProductCandidate | None:
        with _open_store(self.store_path) as connection:
            row = connection.execute(
                "SELECT parent_asin, title FROM products WHERE parent_asin = ?",
                (product_id,),
            ).fetchone()
        if row is None:
            return None
        return ProductCandidate(product_id=row[0], title=row[1])

    def by_title_substring(self, keyword: str, *, limit: int = 5) -> tuple[ProductCandidate, ...]:
        normalized = keyword.strip().lower()
        if not normalized or limit <= 0 or limit > 5:
            return ()
        escaped = normalized.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with _open_store(self.store_path) as connection:
            rows = connection.execute(
                """
                SELECT parent_asin, title
                FROM products
                WHERE title IS NOT NULL AND lower(title) LIKE ? ESCAPE '\\'
                ORDER BY lower(title), parent_asin
                LIMIT ?
                """,
                (f"%{escaped}%", limit),
            ).fetchall()
        return tuple(ProductCandidate(product_id=row[0], title=row[1]) for row in rows)


class EvidenceToolService:
    def __init__(
        self,
        store_path: Path,
        *,
        expected_snapshot_id: str,
        expected_contract_sha256: str,
        retrieval_limit: int = 5,
        ledger: ToolInvocationLedger | None = None,
    ) -> None:
        if retrieval_limit <= 0:
            raise AgentToolError("Retrieval limit must be positive.")
        self.store_path = store_path
        self.expected_snapshot_id = expected_snapshot_id
        self.expected_contract_sha256 = expected_contract_sha256
        self.retrieval_limit = retrieval_limit
        self.ledger = ledger or ToolInvocationLedger()

    def diagnose_product(self, product_id: str, aspects: tuple[str, ...]) -> WorkflowToolResult:
        self._validate_request((product_id,), aspects)
        self.ledger.consume()
        return WorkflowToolResult("diagnosis", self._collect((product_id,), aspects))

    def compare_products(
        self, product_ids: tuple[str, ...], aspects: tuple[str, ...]
    ) -> WorkflowToolResult:
        if not 2 <= len(product_ids) <= 5:
            raise AgentToolError("Comparison requires two to five products.")
        self._validate_request(product_ids, aspects)
        self.ledger.consume()
        return WorkflowToolResult("comparison", self._collect(product_ids, aspects))

    def triage_issues(
        self, product_ids: tuple[str, ...], aspects: tuple[str, ...]
    ) -> WorkflowToolResult:
        if not 1 <= len(product_ids) <= 5:
            raise AgentToolError("Triage requires one to five products.")
        self._validate_request(product_ids, aspects)
        self.ledger.consume()
        retained = [
            item
            for item in self._collect(product_ids, aspects)
            if item.negative_or_conflicting_count > 0
        ]
        strength_rank = {"INSUFFICIENT": 0, "WEAK": 1, "MODERATE": 2, "STRONG": 3}
        retained.sort(
            key=lambda item: (
                -strength_rank[item.strength],
                -item.negative_or_conflicting_count,
                -len(item.evidence),
                item.aspect,
                item.product_id,
            )
        )
        return WorkflowToolResult("triage", tuple(retained))

    def _validate_request(self, product_ids: tuple[str, ...], aspects: tuple[str, ...]) -> None:
        if not product_ids or len(set(product_ids)) != len(product_ids):
            raise AgentToolError("Product IDs must be non-empty and unique.")
        if not aspects or len(set(aspects)) != len(aspects):
            raise AgentToolError("Aspects must be non-empty and unique.")
        with _open_store(self.store_path) as connection:
            metadata = dict(connection.execute("SELECT key, value FROM store_meta"))
            existing = {
                row[0]
                for row in connection.execute(
                    f"SELECT parent_asin FROM products WHERE parent_asin IN ({','.join('?' for _ in product_ids)})",
                    product_ids,
                )
            }
        if (
            metadata.get("dataset_snapshot_id") != self.expected_snapshot_id
            or metadata.get("contract_sha256") != self.expected_contract_sha256
        ):
            raise AgentToolError("Local evidence-store lineage does not match P6 expectations.")
        if existing != set(product_ids):
            raise AgentToolError("One or more requested products do not exist in the local store.")
        enabled_aspects = set(json.loads(metadata.get("aspect_terms", "{}")))
        if not set(aspects).issubset(enabled_aspects):
            raise AgentToolError("One or more aspects are outside the frozen P3 contract.")

    def _collect(
        self, product_ids: tuple[str, ...], aspects: tuple[str, ...]
    ) -> tuple[LocalEvidenceItem, ...]:
        items: list[LocalEvidenceItem] = []
        for product_id in product_ids:
            for aspect in aspects:
                try:
                    result: AspectQueryResult = query_aspect(
                        self.store_path,
                        (product_id,),
                        aspect,
                        limit=self.retrieval_limit,
                    )
                except EngineError as error:
                    raise AgentToolError("Deterministic evidence query failed.") from error
                for evidence in result.evidence:
                    if evidence.parent_asin != product_id:
                        raise AgentToolError("Product-evidence cross-link validation failed.")
                    if not verify_citation(self.store_path, evidence.evidence_id):
                        raise AgentToolError("Local evidence citation validation failed.")
                items.append(
                    LocalEvidenceItem(
                        product_id=product_id,
                        aspect=aspect,
                        strength=result.strength,
                        evidence=result.evidence,
                    )
                )
        return tuple(items)


def _open_store(path: Path) -> sqlite3.Connection:
    try:
        return sqlite3.connect(path)
    except sqlite3.Error as error:
        raise AgentToolError("Unable to open the local evidence store.") from error
