"""Catalog tiers and the whole-category aspect overview.

These cover Step 1-3 of the A-plan: expose the products the frozen store already
holds, say how much evidence each one carries, and give the category a noise
floor that a single product's four reviews cannot provide.
"""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from ecom_review_intelligence.catalog import (
    TIER_A_MIN_REVIEWS,
    TIER_B_MIN_REVIEWS,
    catalog_tier_totals,
    code_for_position,
    load_catalog,
    tier_for_review_count,
)
from ecom_review_intelligence.engine import compute_evidence_id, initialize_store
from ecom_review_intelligence.workbench_service import (
    P6BackedWorkbenchService,
    SyntheticWorkbenchService,
)
from ecom_review_intelligence.workbench_web import create_workbench_app

SNAPSHOT = "a" * 64
CONTRACT = "b" * 64


def _fixture(tmp_path, *, counts=(120, 30, 3)):
    store = tmp_path / "evidence.sqlite"
    checkpoint = tmp_path / "checkpoint.sqlite"
    initialize_store(
        store,
        snapshot_id=SNAPSHOT,
        contract_sha256=CONTRACT,
        aspect_terms={"noise": ("noise",), "reliability": ("broke",)},
        polarity_terms={"supporting": ("quiet",), "opposing": ("loud",)},
    )
    line = 0
    product_ids: list[str] = []
    with sqlite3.connect(store) as connection:
        for index, count in enumerate(counts):
            product_id = f"PRIVATE_PRODUCT_{index}"
            product_ids.append(product_id)
            connection.execute(
                "INSERT INTO products(parent_asin,title) VALUES (?,?)",
                (product_id, f"Private local title {index}"),
            )
            for _ in range(count):
                line += 1
                connection.execute(
                    "INSERT INTO reviews(evidence_id,source_line,parent_asin,text,rating,timestamp,verified_purchase,helpful_vote)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (
                        compute_evidence_id(SNAPSHOT, line),
                        line,
                        product_id,
                        "noise loud broke quiet" if line % 2 else "noise quiet",
                        1.0,
                        line,
                        1,
                        0,
                    ),
                )
        connection.execute(
            "INSERT INTO reviews_fts(rowid,text) SELECT rowid,text FROM reviews"
        )
    config = {
        "dataset_snapshot_id": SNAPSHOT,
        "contract_sha256": CONTRACT,
        "retrieval_limit_per_aspect": 5,
        "model": "deterministic-local-workbench",
        "config_sha256": "c" * 64,
    }
    return store, checkpoint, config, product_ids


def _service(tmp_path, *, counts=(120, 30, 3)):
    store, checkpoint, config, product_ids = _fixture(tmp_path, counts=counts)
    mapping = {
        code_for_position(position): product_id
        for position, product_id in enumerate(product_ids, start=1)
    }
    service = P6BackedWorkbenchService(
        store_path=store,
        checkpoint_path=checkpoint,
        trace_path=tmp_path / "traces.jsonl",
        config=config,
        product_ids_by_code=mapping,
    )
    return service, product_ids


def test_tier_thresholds_are_the_documented_ones():
    assert tier_for_review_count(TIER_A_MIN_REVIEWS) == "A"
    assert tier_for_review_count(TIER_A_MIN_REVIEWS - 1) == "B"
    assert tier_for_review_count(TIER_B_MIN_REVIEWS) == "B"
    assert tier_for_review_count(TIER_B_MIN_REVIEWS - 1) == "C"
    assert tier_for_review_count(0) == "C"


def test_catalog_orders_by_review_count_and_keeps_legacy_codes(tmp_path):
    store, _, _, _ = _fixture(tmp_path, counts=(3, 120, 30))

    entries = load_catalog(store)

    assert [entry.review_count for entry in entries] == [120, 30, 3]
    assert [entry.tier for entry in entries] == ["A", "B", "C"]
    assert [entry.code for entry in entries] == ["frostline", "northstar", "harbor"]
    assert code_for_position(4) == "p0004"


def test_catalog_limit_is_a_slice_not_the_totals(tmp_path):
    store, _, _, _ = _fixture(tmp_path, counts=(120, 30, 3))

    shown = load_catalog(store, limit=2)
    totals = catalog_tier_totals(store)

    assert [entry.review_count for entry in shown] == [120, 30]
    assert totals == {"A": 1, "B": 1, "C": 1}


def test_catalog_view_carries_tiers_without_private_ids(tmp_path):
    service, product_ids = _service(tmp_path)

    view = service.catalog_view()
    payload = view.model_dump(mode="json")

    assert view.schema_version == "p7-catalog-v1"
    assert view.total == 3 and view.shown == 3
    assert view.tiers == {"A": 1, "B": 1, "C": 1}
    assert [item.tier for item in view.items] == ["A", "B", "C"]
    for product_id in product_ids:
        assert product_id not in str(payload)


def test_products_beyond_the_legacy_three_resolve(tmp_path):
    service, _ = _service(tmp_path, counts=(120, 30, 3, 1))

    options = service.product_options()
    codes = [view.code for view in options]

    assert codes == ["frostline", "northstar", "harbor", "p0004"]
    assert "Private local title 3" in options[-1].label


def test_category_view_counts_every_aspect_and_ranks_by_coverage(tmp_path):
    service, _ = _service(tmp_path)

    view = service.category_view()

    assert view.review_total == 153
    assert view.product_total == 3
    assert {item.aspect for item in view.aspects} == {"noise", "reliability"}
    coverages = [item.coverage for item in view.aspects]
    assert coverages == sorted(coverages, reverse=True)
    noise = next(item for item in view.aspects if item.aspect == "noise")
    assert noise.matched == 153
    counts = noise.counts
    assert (
        counts.supporting + counts.opposing + counts.conflicting + counts.insufficient
        == noise.matched
    )


def test_catalog_and_category_endpoints_are_public_safe(tmp_path):
    service, product_ids = _service(tmp_path)
    client = TestClient(create_workbench_app(service=service))

    catalog = client.get("/api/v1/catalog")
    category = client.get("/api/v1/category")

    assert catalog.status_code == 200
    assert category.status_code == 200
    body = catalog.text + category.text
    for product_id in product_ids:
        assert product_id not in body
    assert catalog.json()["items"][0]["tier"] == "A"


def test_synthetic_mode_reports_catalog_unavailable():
    client = TestClient(create_workbench_app(service=SyntheticWorkbenchService()))

    assert client.get("/api/v1/catalog").status_code == 404
    assert client.get("/api/v1/category").status_code == 404
