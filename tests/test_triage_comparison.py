"""Tests for the C2 triage-ranking comparison harness."""

from __future__ import annotations

import math

import pytest

from ecom_review_intelligence.triage_comparison import (
    TriageCandidate,
    TriageComparisonError,
    aggregate_tau,
    build_triage_payload,
    deterministic_triage_scores,
    kendall_tau,
    ordered_codes,
    rank_from_scores,
    reference_triage_scores,
    validate_model_ranking,
)


def _candidate(code: str, *, opposing: int = 0, conflicting: int = 0, matched: int = 10, share: float | None = None) -> TriageCandidate:
    return TriageCandidate(
        product_code=code,
        matched_count=matched,
        evidence_count=3,
        supporting=2,
        opposing=opposing,
        conflicting=conflicting,
        insufficient=1,
        reference_opposing_share=share,
    )


def test_deterministic_scores_rank_by_opposing_plus_conflicting() -> None:
    candidates = [
        _candidate("frostline", opposing=3, matched=100),
        _candidate("northstar", opposing=1, conflicting=1, matched=900),
        _candidate("harbor", opposing=0),
    ]
    scores = deterministic_triage_scores(candidates)
    assert scores == {"frostline": 3, "northstar": 2, "harbor": 0}
    ranks = rank_from_scores(scores)
    assert ordered_codes(ranks) == ["frostline", "northstar", "harbor"]


def test_rank_from_scores_shares_dense_rank_on_ties() -> None:
    ranks = rank_from_scores({"a": 5, "b": 5, "c": 2, "d": 9})
    assert ranks == {"a": 1.0, "b": 1.0, "c": 2.0, "d": 0.0}
    assert ordered_codes(ranks) == ["d", "a", "b", "c"]


def test_reference_scores_require_comparable_share() -> None:
    with pytest.raises(TriageComparisonError):
        reference_triage_scores([_candidate("a", share=None)])
    scores = reference_triage_scores([_candidate("a", share=0.25), _candidate("b", share=0.75)])
    assert scores == {"a": 0.25, "b": 0.75}
    assert ordered_codes(rank_from_scores(scores)) == ["b", "a"]


def test_kendall_tau_perfect_reversed_and_incomparable() -> None:
    a = {"x": 0.0, "y": 1.0, "z": 2.0}
    assert kendall_tau(a, {"x": 0.0, "y": 1.0, "z": 2.0}) == 1.0
    assert kendall_tau(a, {"x": 2.0, "y": 1.0, "z": 0.0}) == -1.0
    # One side fully tied carries no ordering information.
    assert kendall_tau({"x": 0.0, "y": 0.0}, a) is None
    # Fewer than two comparable items.
    assert kendall_tau({"x": 0.0}, {"x": 0.0}) is None


def test_kendall_tau_corrects_for_ties() -> None:
    # a: x,y tied at rank 0, z at rank 1; b strictly x < y < z.
    a = {"x": 0.0, "y": 0.0, "z": 1.0}
    b = {"x": 0.0, "y": 1.0, "z": 2.0}
    tau = kendall_tau(a, b)
    assert tau is not None
    assert math.isclose(tau, 2 / math.sqrt(6), rel_tol=1e-9)


def test_build_triage_payload_is_numeric_only() -> None:
    payload = build_triage_payload("noise", [_candidate("frostline", opposing=2)])
    assert payload["aspect"] == "noise"
    assert payload["products"] == [
        {
            "product_code": "frostline",
            "matched_count": 10,
            "evidence_count": 3,
            "supporting": 2,
            "opposing": 2,
            "conflicting": 0,
            "insufficient": 1,
        }
    ]
    assert set(payload) == {"aspect", "products"}


def test_validate_model_ranking_accepts_only_permutations() -> None:
    assert validate_model_ranking({"ranking": ["b", "a"]}, ["a", "b"]) == ["b", "a"]
    for raw in (
        "b,a",
        {"ranking": "ab"},
        {"ranking": ["a", "a"]},
        {"ranking": ["a"]},
        {"ranking": ["a", "b", "c"]},
        {},
    ):
        with pytest.raises(TriageComparisonError):
            validate_model_ranking(raw, ["a", "b"])


def test_aggregate_tau_ignores_missing_aspects() -> None:
    assert aggregate_tau({"noise": 1.0, "leakage": None, "size": -1.0}) == 0.0
    assert aggregate_tau({"noise": None}) is None
    assert aggregate_tau({}) is None
