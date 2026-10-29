"""Read-only audit of the frozen vocabulary polarity against independent star ratings."""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

# The audit must measure the rule the product actually ships, not a parallel reimplementation.
from ecom_review_intelligence.engine import _TOKEN_PATTERN, _polarity

_SENTENCE_SPLIT = re.compile(r"[.!?\n;:]+")
_NEGATORS = frozenset({"not", "no", "never", "without", "neither", "nor"})
_NEGATION_WINDOW = 2
_POLARITIES = ("supporting", "opposing", "conflicting", "insufficient")
_SCOPES = ("aligned", "mixed", "cross_sentence", "aspect_only")
_RATING_BUCKETS = ("supporting", "opposing", "neutral", "unknown")


class LabelAuditError(RuntimeError):
    """Raised when the local evidence store cannot be audited without reinterpretation."""


@dataclass(frozen=True)
class ReviewSignals:
    polarity: str
    scope: str
    negated: bool


def rating_bucket(rating: float | None) -> str:
    if rating is None:
        return "unknown"
    if rating >= 4:
        return "supporting"
    if rating <= 2:
        return "opposing"
    return "neutral"


def review_signals(
    text: str,
    *,
    aspect: str,
    aspect_terms: dict[str, list[str]],
    polarity_terms: dict[str, list[str]],
) -> ReviewSignals:
    polarity = _polarity(text, polarity_terms)
    if polarity == "insufficient":
        return ReviewSignals(polarity=polarity, scope="aspect_only", negated=False)

    terms = tuple(aspect_terms.get(aspect, ()))
    supporting = set(polarity_terms["supporting"])
    opposing = set(polarity_terms["opposing"])
    sentences = [item for item in _SENTENCE_SPLIT.split(text) if item.strip()]
    aspect_sentences = {
        index
        for index, sentence in enumerate(sentences)
        if any(term in sentence.lower() for term in terms)
    }

    polarity_sentences: set[int] = set()
    negated = False
    for index, sentence in enumerate(sentences):
        tokens = _TOKEN_PATTERN.findall(sentence.lower())
        hits = [
            position
            for position, token in enumerate(tokens)
            if token in supporting or token in opposing
        ]
        if not hits:
            continue
        polarity_sentences.add(index)
        if any(
            any(token in _NEGATORS for token in tokens[max(0, position - _NEGATION_WINDOW) : position])
            for position in hits
        ):
            negated = True

    if polarity_sentences <= aspect_sentences:
        scope = "aligned"
    elif polarity_sentences & aspect_sentences:
        scope = "mixed"
    else:
        scope = "cross_sentence"
    return ReviewSignals(polarity=polarity, scope=scope, negated=negated)


class LabelTally:
    """Aggregate-only accumulator; it never retains review text or local identifiers."""

    def __init__(self) -> None:
        self._scanned: Counter[str] = Counter()
        self._polarity: dict[str, Counter[str]] = {}
        self._scope: dict[str, Counter[str]] = {}
        self._buckets: dict[str, Counter[str]] = {}
        self._agreement: Counter[str] = Counter()
        self._negated: Counter[str] = Counter()

    def seed_aspects(self, aspects: tuple[str, ...]) -> None:
        for aspect in aspects:
            self._polarity.setdefault(aspect, Counter())
            self._scope.setdefault(aspect, Counter())
            self._buckets.setdefault(aspect, Counter())

    def record(self, aspect: str, signals: ReviewSignals, rating: float | None) -> None:
        self.seed_aspects((aspect,))
        bucket = rating_bucket(rating)
        self._scanned[aspect] += 1
        self._polarity[aspect][signals.polarity] += 1
        self._scope[aspect][signals.scope] += 1
        self._buckets[aspect][bucket] += 1
        if signals.negated:
            self._negated[aspect] += 1
        if signals.polarity in _POLARITIES[:2] and bucket in _RATING_BUCKETS[:2]:
            self._agreement[f"{aspect}.comparable"] += 1
            if signals.polarity == bucket:
                self._agreement[f"{aspect}.agree"] += 1
            else:
                self._agreement[f"{aspect}.{'high' if bucket == 'supporting' else 'low'}"] += 1

    def to_public_dict(self) -> dict[str, dict[str, object]]:
        aspects: dict[str, dict[str, object]] = {}
        for aspect in sorted(self._polarity):
            comparable = self._agreement[f"{aspect}.comparable"]
            agree = self._agreement[f"{aspect}.agree"]
            aspects[aspect] = {
                "reviews_scanned": self._scanned[aspect],
                "polarity_counts": _ordered(self._polarity[aspect], _POLARITIES),
                "scope_counts": _ordered(self._scope[aspect], _SCOPES),
                "rating_bucket_counts": _ordered(self._buckets[aspect], _RATING_BUCKETS),
                "negated_count": self._negated[aspect],
                "collision_count": self._polarity[aspect]["conflicting"],
                "rating_comparable_count": comparable,
                "rating_agreement_count": agree,
                "rating_agreement_rate": (agree / comparable) if comparable else None,
                "disagreement_keyword_opposing_rating_high": self._agreement[f"{aspect}.high"],
                "disagreement_keyword_supporting_rating_low": self._agreement[f"{aspect}.low"],
            }
        return {"aspects": aspects}


def audit_store(store_path: Path) -> dict[str, object]:
    tally = LabelTally()
    with _open_read_only(store_path) as connection:
        metadata = dict(connection.execute("SELECT key, value FROM store_meta"))
        try:
            aspect_terms = json.loads(metadata["aspect_terms"])
            polarity_terms = json.loads(metadata["polarity_terms"])
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise LabelAuditError("Local store vocabulary is unavailable for audit.") from error
        tally.seed_aspects(tuple(sorted(aspect_terms)))
        reviews_scanned = 0
        for text, rating in connection.execute("SELECT text, rating FROM reviews ORDER BY source_line"):
            reviews_scanned += 1
            lowered = text.lower()
            for aspect in sorted(aspect_terms):
                if any(term in lowered for term in aspect_terms[aspect]):
                    signals = review_signals(
                        text,
                        aspect=aspect,
                        aspect_terms=aspect_terms,
                        polarity_terms=polarity_terms,
                    )
                    tally.record(aspect, signals, rating)
    report: dict[str, object] = {
        "schema_version": "phase-3b-label-quality-v1",
        "dataset_snapshot_id": metadata["dataset_snapshot_id"],
        "contract_sha256": metadata["contract_sha256"],
        "reviews_scanned": reviews_scanned,
    }
    report.update(tally.to_public_dict())
    return report


def _ordered(counter: Counter[str], keys: tuple[str, ...]) -> dict[str, int]:
    return {key: counter[key] for key in keys}


@contextmanager
def _open_read_only(path: Path) -> Iterator[sqlite3.Connection]:
    try:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as error:
        raise LabelAuditError("Unable to open the local evidence store read-only.") from error
    try:
        yield connection
    finally:
        connection.close()
