"""Reference layer for content-level agent evaluation.

Joins the advisory model-label table (data/processed/model_labels.jsonl, one row
per code-accepted blind item) back to the store's product joins and aggregates
it per product and aspect. The deterministic layer stays authoritative; this
module only builds the reference distribution used to evaluate direction output.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

DIRECTION_POLARITIES = ("supporting", "opposing", "conflicting")


class AspectReferenceError(RuntimeError):
    """Raised when the reference layer cannot be built without reinterpretation."""


@dataclass(frozen=True)
class AspectReferenceCounts:
    labelled: int
    direction: int
    supporting: int
    opposing: int
    conflicting: int
    off_topic: int
    still_unclear: int

    @property
    def opposing_share(self) -> float | None:
        """Opposing share over supporting+opposing; conflicting is excluded."""

        base = self.supporting + self.opposing
        return self.opposing / base if base else None


def load_label_ids(labels_path: Path) -> dict[str, dict[str, str]]:
    labels: dict[str, dict[str, str]] = {}
    with open(labels_path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            labels[str(record["id"])] = {
                "polarity": str(record["polarity"]),
                "reason": str(record["reason"]),
            }
    return labels


def build_aspect_reference(
    store_path: Path,
    labels_path: Path,
    *,
    product_ids: tuple[str, ...] | None = None,
    min_direction: int = 5,
) -> dict[str, dict[str, AspectReferenceCounts]]:
    """Per product_id -> aspect -> reference counts from the advisory label layer."""

    labels = load_label_ids(labels_path)
    if not labels:
        raise AspectReferenceError("The advisory label table is empty or unavailable.")
    connection = sqlite3.connect(f"file:{Path(store_path).as_posix()}?mode=ro", uri=True)
    try:
        if product_ids:
            placeholders = ",".join("?" for _ in product_ids)
            rows = connection.execute(
                f"SELECT source_line, parent_asin FROM reviews WHERE parent_asin IN ({placeholders})",
                list(product_ids),
            ).fetchall()
        else:
            rows = connection.execute("SELECT source_line, parent_asin FROM reviews").fetchall()
    finally:
        connection.close()
    product_by_line = {int(source_line): str(parent_asin) for source_line, parent_asin in rows}
    buckets: dict[tuple[str, str], list[dict[str, str]]] = {}
    for item_id, label in labels.items():
        aspect, _, line = item_id.partition(":")
        product_id = product_by_line.get(int(line))
        if product_id is None:
            continue
        buckets.setdefault((product_id, aspect), []).append(label)
    reference: dict[str, dict[str, AspectReferenceCounts]] = {}
    for (product_id, aspect), group in buckets.items():
        polarity = [label["polarity"] for label in group]
        reasons = [label["reason"] for label in group if label["polarity"] == "insufficient"]
        direction = sum(1 for value in polarity if value in DIRECTION_POLARITIES)
        counts = AspectReferenceCounts(
            labelled=len(group),
            direction=direction,
            supporting=polarity.count("supporting"),
            opposing=polarity.count("opposing"),
            conflicting=polarity.count("conflicting"),
            off_topic=reasons.count("off_topic"),
            still_unclear=sum(1 for value in reasons if value in ("vague", "hypothetical")),
        )
        if counts.direction >= min_direction:
            reference.setdefault(product_id, {})[aspect] = counts
    return reference


def reference_verdict(counts: AspectReferenceCounts, *, pain_threshold: float = 0.5) -> str | None:
    """Categorical reference verdict: does the model-label layer see a pain point?"""

    share = counts.opposing_share
    if share is None:
        return None
    return "pain" if share >= pain_threshold else "ok"


def deterministic_verdict(
    supporting: int, opposing: int, *, pain_threshold: float = 0.5
) -> str | None:
    base = supporting + opposing
    if not base:
        return None
    return "pain" if opposing / base >= pain_threshold else "ok"


def agreement_summary(
    reference: dict[str, dict[str, AspectReferenceCounts]],
    deterministic: dict[tuple[str, str], tuple[int, int]],
) -> dict[str, object]:
    """Compare reference verdicts against the deterministic layer, aggregate only."""

    per_aspect: dict[str, dict[str, int]] = {}
    total = agree = 0
    for (product_id, aspect), (supporting, opposing) in deterministic.items():
        counts = reference.get(product_id, {}).get(aspect)
        if counts is None:
            continue
        expected = reference_verdict(counts)
        observed = deterministic_verdict(supporting, opposing)
        if expected is None or observed is None:
            continue
        slot = per_aspect.setdefault(aspect, {"comparable": 0, "agree": 0})
        slot["comparable"] += 1
        total += 1
        if expected == observed:
            slot["agree"] += 1
            agree += 1
    return {
        "comparable_total": total,
        "agree_total": agree,
        "agreement_rate": round(agree / total, 3) if total else None,
        "per_aspect": {
            aspect: {
                **slot,
                "agreement_rate": round(slot["agree"] / slot["comparable"], 3)
                if slot["comparable"]
                else None,
            }
            for aspect, slot in sorted(per_aspect.items())
        },
    }
