import sqlite3
from pathlib import Path

import pytest

from ecom_review_intelligence.agent_tools import (
    AgentToolError,
    EvidenceToolService,
    ProductResolver,
    ToolInvocationLedger,
)
from ecom_review_intelligence.engine import compute_evidence_id, initialize_store

SNAPSHOT = "a" * 64
CONTRACT = "b" * 64


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    path = tmp_path / "evidence.sqlite"
    initialize_store(
        path,
        snapshot_id=SNAPSHOT,
        contract_sha256=CONTRACT,
        aspect_terms={
            "noise": ("noise", "noisy", "loud", "quiet"),
            "reliability": ("broke", "failed", "reliable"),
        },
        polarity_terms={
            "supporting": ("good", "quiet", "reliable"),
            "opposing": ("bad", "loud", "broke", "failed"),
        },
    )
    products = (
        ("B001", "Alpha Ice Quiet"),
        ("B002", "Alpha Ice Pro"),
        ("B003", "Beta Countertop"),
    )
    reviews = (
        (1, "B001", "quiet good"),
        (2, "B001", "noise loud"),
        (3, "B001", "broke failed"),
        (4, "B002", "loud bad"),
        (5, "B002", "reliable good"),
        (6, "B003", "quiet loud"),
    )
    with sqlite3.connect(path) as connection:
        connection.executemany("INSERT INTO products(parent_asin, title) VALUES (?, ?)", products)
        connection.executemany(
            """
            INSERT INTO reviews(
                evidence_id, source_line, parent_asin, text, rating, timestamp,
                verified_purchase, helpful_vote
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (compute_evidence_id(SNAPSHOT, line), line, product, text, 3.0, 1, 1, 0)
                for line, product, text in reviews
            ],
        )
    return path


def test_product_resolver_validates_exact_ids_locally(store_path: Path) -> None:
    resolver = ProductResolver(store_path)

    candidate = resolver.by_exact_id("B001")

    assert candidate is not None
    assert candidate.product_id == "B001"
    assert candidate.title == "Alpha Ice Quiet"
    assert resolver.by_exact_id("B999") is None


def test_title_lookup_is_case_normalized_stable_capped_and_literal(store_path: Path) -> None:
    resolver = ProductResolver(store_path)

    matches = resolver.by_title_substring("ALPHA", limit=5)

    assert [candidate.product_id for candidate in matches] == ["B002", "B001"]
    assert resolver.by_title_substring("%", limit=5) == ()
    assert len(resolver.by_title_substring("a", limit=1)) == 1


def test_diagnosis_returns_verified_exact_product_evidence(store_path: Path) -> None:
    service = EvidenceToolService(
        store_path,
        expected_snapshot_id=SNAPSHOT,
        expected_contract_sha256=CONTRACT,
        retrieval_limit=5,
    )

    result = service.diagnose_product("B001", ("noise", "reliability"))

    assert result.workflow == "diagnosis"
    assert [(item.product_id, item.aspect) for item in result.items] == [
        ("B001", "noise"),
        ("B001", "reliability"),
    ]
    assert all(evidence.parent_asin == "B001" for item in result.items for evidence in item.evidence)


def test_comparison_aligns_the_same_aspects_for_each_product(store_path: Path) -> None:
    service = EvidenceToolService(
        store_path,
        expected_snapshot_id=SNAPSHOT,
        expected_contract_sha256=CONTRACT,
    )

    result = service.compare_products(("B001", "B002"), ("noise", "reliability"))

    assert [(item.product_id, item.aspect) for item in result.items] == [
        ("B001", "noise"),
        ("B001", "reliability"),
        ("B002", "noise"),
        ("B002", "reliability"),
    ]


def test_triage_keeps_negative_or_conflicting_evidence_in_deterministic_order(
    store_path: Path,
) -> None:
    service = EvidenceToolService(
        store_path,
        expected_snapshot_id=SNAPSHOT,
        expected_contract_sha256=CONTRACT,
    )

    result = service.triage_issues(("B001", "B002", "B003"), ("noise", "reliability"))

    assert [(item.product_id, item.aspect) for item in result.items] == [
        ("B001", "noise"),
        ("B002", "noise"),
        ("B003", "noise"),
        ("B001", "reliability"),
    ]


def test_one_ledger_allows_only_one_business_tool_execution(store_path: Path) -> None:
    ledger = ToolInvocationLedger()
    service = EvidenceToolService(
        store_path,
        expected_snapshot_id=SNAPSHOT,
        expected_contract_sha256=CONTRACT,
        ledger=ledger,
    )

    service.diagnose_product("B001", ("noise",))

    assert ledger.execution_count == 1
    with pytest.raises(AgentToolError, match="one business evidence tool"):
        service.triage_issues(("B001",), ("noise",))


def test_lineage_or_product_shape_mismatch_fails_closed(store_path: Path) -> None:
    bad_lineage = EvidenceToolService(
        store_path,
        expected_snapshot_id="c" * 64,
        expected_contract_sha256=CONTRACT,
    )
    with pytest.raises(AgentToolError, match="lineage"):
        bad_lineage.diagnose_product("B001", ("noise",))

    service = EvidenceToolService(
        store_path,
        expected_snapshot_id=SNAPSHOT,
        expected_contract_sha256=CONTRACT,
    )
    with pytest.raises(AgentToolError, match="two to five"):
        service.compare_products(("B001",), ("noise",))

def _stratified_store(tmp_path: Path) -> Path:
    """Seven noise reviews: the five earliest are 5-star, the last two are 1-star."""

    path = tmp_path / "stratified.sqlite"
    initialize_store(
        path,
        snapshot_id=SNAPSHOT,
        contract_sha256=CONTRACT,
        aspect_terms={"noise": ("noise",)},
        polarity_terms={"supporting": ("quiet",), "opposing": ("loud",)},
    )
    reviews = (
        (1, "B001", "noise quiet", 5.0),
        (2, "B001", "noise quiet", 5.0),
        (3, "B001", "noise quiet", 5.0),
        (4, "B001", "noise quiet", 5.0),
        (5, "B001", "noise quiet", 5.0),
        (6, "B001", "noise loud", 1.0),
        (7, "B001", "noise loud", 1.0),
    )
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO products(parent_asin, title) VALUES (?, ?)", ("B001", "Alpha"))
        connection.executemany(
            """
            INSERT INTO reviews(
                evidence_id, source_line, parent_asin, text, rating, timestamp,
                verified_purchase, helpful_vote
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (compute_evidence_id(SNAPSHOT, line), line, product, text, rating, line, 1, 0)
                for line, product, text, rating in reviews
            ],
        )
    return path


def test_diagnosis_sample_covers_low_star_reviews_instead_of_row_order(tmp_path: Path) -> None:
    store = _stratified_store(tmp_path)
    service = EvidenceToolService(
        store,
        expected_snapshot_id=SNAPSHOT,
        expected_contract_sha256=CONTRACT,
        retrieval_limit=5,
    )

    result = service.diagnose_product("B001", ("noise",))

    lines = sorted(evidence.source_line for evidence in result.items[0].evidence)
    assert len(lines) == 5
    assert 6 in lines and 7 in lines, lines
    assert lines != [1, 2, 3, 4, 5]
