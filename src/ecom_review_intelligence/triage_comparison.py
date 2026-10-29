"""Triage-ranking comparison harness (C2).

Compares three ways of ordering products for verification within one aspect:

1. deterministic rule — opposing+conflicting evidence counts from the local
   workbench (documented experimental rule, not the shipped triage ordering);
2. advisory reference layer — opposing share from the model-label table;
3. live model ranking — one constrained call that only ever sees numeric
   per-product counts (never review text).

Pure logic only: ranking, Kendall tau-b, the numeric payload, and validation of
the model's ranking output. The runner lives in scripts/run_triage_live_comparison.py.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass


class TriageComparisonError(RuntimeError):
    """Raised when a model ranking cannot be accepted as a strict permutation."""


@dataclass(frozen=True)
class TriageCandidate:
    """Numeric-only candidate for one product in one aspect.

    ``reference_opposing_share`` comes from the advisory label layer; ``None``
    means that product-aspect has no comparable reference data and must be
    excluded by the caller, never guessed.
    """

    product_code: str
    matched_count: int
    evidence_count: int
    supporting: int
    opposing: int
    conflicting: int
    insufficient: int
    reference_opposing_share: float | None


def urgency_score(candidate: TriageCandidate) -> int:
    """Documented experimental rule: problems to verify = opposing + conflicting."""

    return candidate.opposing + candidate.conflicting


def deterministic_triage_scores(
    candidates: Sequence[TriageCandidate],
) -> dict[str, int]:
    return {c.product_code: urgency_score(c) for c in candidates}


def reference_triage_scores(
    candidates: Sequence[TriageCandidate],
) -> dict[str, float]:
    """Higher share = more urgent. Callers must have dropped None shares."""

    scores: dict[str, float] = {}
    for candidate in candidates:
        if candidate.reference_opposing_share is None:
            raise TriageComparisonError(
                "Reference share is missing; exclude the candidate before ranking."
            )
        scores[candidate.product_code] = candidate.reference_opposing_share
    return scores


def rank_from_scores(scores: dict[str, float]) -> dict[str, float]:
    """Dense ranks from scores: higher score = rank 0; ties share one rank."""

    distinct = sorted(set(scores.values()), reverse=True)
    rank_of_value = {value: float(index) for index, value in enumerate(distinct)}
    return {code: rank_of_value[value] for code, value in scores.items()}


def ordered_codes(ranks: dict[str, float]) -> list[str]:
    """Stable display order: rank ascending, then code ascending."""

    return [code for code, _ in sorted(ranks.items(), key=lambda item: (item[1], item[0]))]


def kendall_tau(a: dict[str, float], b: dict[str, float]) -> float | None:
    """Kendall tau-b between two score maps over their common keys.

    Returns ``None`` when fewer than two items are comparable or one side has
    no ordering information (all tied). tau-b corrects for ties on either side.
    """

    common = sorted(set(a) & set(b))
    if len(common) < 2:
        return None
    concordant = discordant = 0
    for index, x in enumerate(common):
        for y in common[index + 1 :]:
            delta_a = a[x] - a[y]
            delta_b = b[x] - b[y]
            product = delta_a * delta_b
            if product > 0:
                concordant += 1
            elif product < 0:
                discordant += 1
    total_pairs = len(common) * (len(common) - 1) / 2

    def tie_pairs(values: list[float]) -> int:
        return sum(count * (count - 1) // 2 for count in Counter(values).values())

    ties_a = tie_pairs([a[code] for code in common])
    ties_b = tie_pairs([b[code] for code in common])
    denominator = math.sqrt((total_pairs - ties_a) * (total_pairs - ties_b))
    if denominator == 0:
        return None
    return (concordant - discordant) / denominator


def aggregate_tau(per_aspect: dict[str, float | None]) -> float | None:
    values = [value for value in per_aspect.values() if value is not None]
    return round(sum(values) / len(values), 3) if values else None


def build_triage_payload(
    aspect: str, candidates: Sequence[TriageCandidate]
) -> dict[str, object]:
    """Numeric-only model payload; review text and raw identifiers never enter."""

    return {
        "aspect": aspect,
        "products": [
            {
                "product_code": candidate.product_code,
                "matched_count": candidate.matched_count,
                "evidence_count": candidate.evidence_count,
                "supporting": candidate.supporting,
                "opposing": candidate.opposing,
                "conflicting": candidate.conflicting,
                "insufficient": candidate.insufficient,
            }
            for candidate in candidates
        ],
    }


def validate_model_ranking(raw: object, expected_codes: Sequence[str]) -> list[str]:
    """Accept only a strict permutation of the candidate codes, most urgent first."""

    if not isinstance(raw, dict):
        raise TriageComparisonError("ranking payload must be an object")
    ranking = raw.get("ranking")
    if not isinstance(ranking, list):
        raise TriageComparisonError("ranking must be a list")
    codes = [str(code) for code in ranking]
    if sorted(codes) != sorted(expected_codes):
        raise TriageComparisonError("ranking must be a permutation of the candidate codes")
    if len(set(codes)) != len(codes):
        raise TriageComparisonError("ranking must not repeat a product code")
    return codes
