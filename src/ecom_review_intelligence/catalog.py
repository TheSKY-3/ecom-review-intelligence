"""Review-count-ranked catalog over the frozen Ice Makers evidence store.

The P2 scope froze 3,278 products and 136,604 reviews, but the workbench only
ever exposed three hand-picked entries, and the store's review counts are heavily
long-tailed (median four reviews per product). This module makes the coverage
limit explicit instead of hidden: every product gets a tier derived from how many
reviews it can actually be diagnosed from, so the UI can show which products carry
enough evidence and which ones may only be described at category level.

The catalog is read-only and deterministic: same store, same order, same codes.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

TIER_A_MIN_REVIEWS = 100
TIER_B_MIN_REVIEWS = 20

#: How many products the local workbench exposes. 244 is the number of products
#: with at least 100 reviews, which together cover 74.3% of the stored reviews.
DEFAULT_CATALOG_LIMIT = 244

#: The first three codes existed before the catalog and are kept so existing
#: scripts, traces, and regression cases keep addressing the same products.
LEGACY_CODES = ("frostline", "northstar", "harbor")


@dataclass(frozen=True)
class CatalogEntry:
    code: str
    parent_asin: str
    title: str
    review_count: int
    tier: str


def tier_for_review_count(review_count: int) -> str:
    """A >= 100 reviews, B >= 20, C below that (hint only, not a claim)."""

    if review_count >= TIER_A_MIN_REVIEWS:
        return "A"
    if review_count >= TIER_B_MIN_REVIEWS:
        return "B"
    return "C"


def code_for_position(position: int) -> str:
    """1-based: legacy codes first, then p0004, p0005, ..."""

    if position <= len(LEGACY_CODES):
        return LEGACY_CODES[position - 1]
    return f"p{position:04d}"


def _open(store_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(store_path).as_posix()}?mode=ro", uri=True)


def load_catalog(
    store_path: Path, *, limit: int = DEFAULT_CATALOG_LIMIT
) -> tuple[CatalogEntry, ...]:
    """Return the most-reviewed products, with a coverage tier per product."""

    if limit <= 0:
        raise ValueError("Catalog limit must be positive.")
    with _open(store_path) as connection:
        rows = connection.execute(
            """
            SELECT p.parent_asin, coalesce(p.title, ''), count(r.evidence_id)
            FROM products p
            LEFT JOIN reviews r ON r.parent_asin = p.parent_asin
            GROUP BY p.parent_asin, p.title
            ORDER BY count(r.evidence_id) DESC, p.parent_asin
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return tuple(
        CatalogEntry(
            code=code_for_position(position),
            parent_asin=str(parent_asin),
            title=str(title),
            review_count=int(count),
            tier=tier_for_review_count(int(count)),
        )
        for position, (parent_asin, title, count) in enumerate(rows, start=1)
    )


def catalog_tier_totals(store_path: Path) -> dict[str, int]:
    """Product counts per tier over the whole store, not just the shown slice."""

    with _open(store_path) as connection:
        rows = connection.execute(
            """
            SELECT count(r.evidence_id) AS n
            FROM products p
            LEFT JOIN reviews r ON r.parent_asin = p.parent_asin
            GROUP BY p.parent_asin
            """
        ).fetchall()
    totals = {"A": 0, "B": 0, "C": 0}
    for (count,) in rows:
        totals[tier_for_review_count(int(count))] += 1
    return totals
