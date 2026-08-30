"""Data-validated local evidence contract for the selected P2 scope."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SOURCE_ORDER = ("reviews", "metadata")
_SOURCE_FILENAMES = {
    "reviews": "Appliances.jsonl.gz",
    "metadata": "meta_Appliances.jsonl.gz",
}
_TOKEN_PATTERN = re.compile(r"[a-z]+")
_MIN_ASPECT_MATCHED_REVIEWS = 100
_MIN_ENABLED_ASPECTS = 5
_ASPECT_TERMS: dict[str, tuple[str, ...]] = {
    "ice_quality": ("ice", "cube", "cubes", "nugget", "nuggets", "clear"),
    "noise": ("noise", "noisy", "loud", "quiet", "sound"),
    "reliability": ("broke", "broken", "stopped", "failed", "failure", "reliable", "durable"),
    "cleaning_maintenance": ("clean", "cleaning", "maintenance", "descale", "sanitize"),
    "capacity_speed": ("fast", "quick", "slow", "capacity", "batch", "minutes"),
    "ease_setup": ("easy", "simple", "setup", "install", "instructions"),
    "leakage": ("leak", "leaking", "drip", "water"),
    "size_portability": ("small", "large", "compact", "counter", "portable", "size"),
}
_POLARITY_TERMS: dict[str, tuple[str, ...]] = {
    "supporting": ("great", "good", "love", "easy", "quiet", "fast", "perfect", "reliable"),
    "opposing": ("bad", "poor", "broke", "broken", "noisy", "loud", "leak", "slow", "stopped", "failed"),
}
_STRENGTH_RULES: dict[str, dict[str, int]] = {
    "INSUFFICIENT": {"min_evidence": 0, "max_evidence": 0},
    "WEAK": {"min_evidence": 1, "max_evidence": 1},
    "MODERATE": {"min_evidence": 2, "max_evidence": 4},
    "STRONG": {"min_evidence": 5},
}
_LABEL_POLICY = {
    "silver": "deterministic or test-derived expectation; not human-verified",
    "reviewed": "manual reviewer record required",
    "gold": "prohibited",
}
_FIELD_MAPPING = {
    "product_key": "metadata.parent_asin",
    "review_product_key": "reviews.parent_asin",
    "review_text": "reviews.text",
    "rating": "reviews.rating",
    "timestamp": "reviews.timestamp",
    "verified_purchase": "reviews.verified_purchase",
    "helpful_vote": "reviews.helpful_vote",
    "local_evidence_id": "sha256(evidence-v1 plus snapshot, source role, and one-based JSONL line)",
}


class ContractError(RuntimeError):
    """Raised when P3 scope lineage or policy constraints cannot be trusted."""


@dataclass(frozen=True)
class AspectCoverage:
    aspect: str
    matched_reviews: int
    coverage: float
    enabled: bool


@dataclass(frozen=True)
class EvidenceContract:
    dataset_snapshot_id: str
    audit_sha256: str
    selected_category: str
    git_revision: str
    observed_at: str
    selected_review_count: int
    selected_product_count: int
    review_count_quantiles: dict[str, int]
    aspects: tuple[AspectCoverage, ...]
    enabled_aspects: tuple[str, ...]
    aspect_terms: dict[str, tuple[str, ...]]
    strength_rules: dict[str, dict[str, int]]
    polarity_terms: dict[str, tuple[str, ...]]
    label_policy: dict[str, str]
    field_mapping: dict[str, str]
    contract_sha256: str


@dataclass(frozen=True)
class _SourceInput:
    filename: str
    total_bytes: int
    sha256: str


def build_evidence_contract(
    project_root: Path,
    *,
    git_revision: str,
    observed_at: str | None = None,
) -> EvidenceContract:
    """Validate P1/P2 lineage then profile the selected local category without retaining records."""

    root = project_root.resolve()
    snapshot_id, audit_sha256, selected_category, inputs = _validate_lineage(root)
    selected_parents = _selected_metadata_parents(
        _raw_path(root, inputs["metadata"]),
        selected_category,
    )
    aspect_counts, review_counts = _selected_review_profile(
        _raw_path(root, inputs["reviews"]),
        selected_parents,
    )
    selected_review_count = sum(review_counts.values())
    if not selected_review_count or not selected_parents:
        raise ContractError("Selected scope has no metadata products or no joined reviews.")

    aspects = tuple(
        AspectCoverage(
            aspect=aspect,
            matched_reviews=aspect_counts[aspect],
            coverage=round(aspect_counts[aspect] / selected_review_count, 6),
            enabled=aspect_counts[aspect] >= _MIN_ASPECT_MATCHED_REVIEWS,
        )
        for aspect in _ASPECT_TERMS
    )
    enabled_aspects = tuple(aspect.aspect for aspect in aspects if aspect.enabled)
    if len(enabled_aspects) < _MIN_ENABLED_ASPECTS:
        raise ContractError("Selected scope has fewer than five data-covered aspects.")

    payload = {
        "state": "PASSED",
        "dataset_snapshot_id": snapshot_id,
        "audit_sha256": audit_sha256,
        "selected_category": selected_category,
        "git_revision": git_revision,
        "observed_at": observed_at or _utc_now(),
        "selected_review_count": selected_review_count,
        "selected_product_count": len(selected_parents),
        "review_count_quantiles": _quantiles(list(review_counts.values())),
        "aspects": [asdict(aspect) for aspect in aspects],
        "enabled_aspects": list(enabled_aspects),
        "aspect_terms": {aspect: list(terms) for aspect, terms in _ASPECT_TERMS.items()},
        "strength_rules": _STRENGTH_RULES,
        "polarity_terms": {name: list(terms) for name, terms in _POLARITY_TERMS.items()},
        "label_policy": _LABEL_POLICY,
        "field_mapping": _FIELD_MAPPING,
    }
    contract_sha256 = _canonical_sha(payload)
    return EvidenceContract(
        dataset_snapshot_id=snapshot_id,
        audit_sha256=audit_sha256,
        selected_category=selected_category,
        git_revision=git_revision,
        observed_at=payload["observed_at"],
        selected_review_count=selected_review_count,
        selected_product_count=len(selected_parents),
        review_count_quantiles=payload["review_count_quantiles"],
        aspects=aspects,
        enabled_aspects=enabled_aspects,
        aspect_terms=dict(_ASPECT_TERMS),
        strength_rules=dict(_STRENGTH_RULES),
        polarity_terms=dict(_POLARITY_TERMS),
        label_policy=dict(_LABEL_POLICY),
        field_mapping=dict(_FIELD_MAPPING),
        contract_sha256=contract_sha256,
    )


def write_contract_outputs(project_root: Path, contract: EvidenceContract) -> tuple[Path, Path]:
    """Atomically persist the aggregate P3 policy and readable decision."""

    root = project_root.resolve()
    policy_path = root / "artifacts" / "contracts" / "phase-3-evidence-policy.json"
    decision_path = root / "docs" / "decisions" / "phase-3-evidence-policy.md"
    _write_json_atomic(policy_path, _contract_document(contract))
    _write_text_atomic(decision_path, _render_contract_markdown(contract))
    return policy_path, decision_path


def _validate_lineage(root: Path) -> tuple[str, str, str, dict[str, _SourceInput]]:
    p1 = _read_json(root / "artifacts" / "manifests" / "phase-1-download.json", "P1 manifest")
    p2_audit = _read_json(root / "artifacts" / "audits" / "phase-2-audit.json", "P2 audit")
    p2_scope = _read_json(root / "artifacts" / "manifests" / "phase-2-scope.json", "P2 scope")
    if p1.get("state") != "PASSED" or p2_audit.get("state") != "PASSED" or p2_scope.get("state") != "PASSED":
        raise ContractError("P1 and P2 lineage states must be PASSED.")

    snapshot_id = p1.get("dataset_snapshot_id")
    if not isinstance(snapshot_id, str):
        raise ContractError("P1 manifest has no valid dataset snapshot.")
    if p2_audit.get("dataset_snapshot_id") != snapshot_id or p2_scope.get("dataset_snapshot_id") != snapshot_id:
        raise ContractError("P2 snapshot does not match the frozen P1 snapshot.")

    audit_copy = dict(p2_audit)
    observed_audit_sha = audit_copy.pop("audit_sha256", None)
    if not isinstance(observed_audit_sha, str) or _canonical_sha(audit_copy) != observed_audit_sha:
        raise ContractError("P2 audit SHA-256 does not match its canonical aggregate payload.")
    if p2_scope.get("audit_sha256") != observed_audit_sha:
        raise ContractError("P2 scope audit SHA-256 does not match the P2 audit.")
    selected_category = p2_scope.get("selected_category")
    if selected_category != "Ice Makers":
        raise ContractError("P2 selected scope must be Ice Makers for this contract.")

    sources = p1.get("sources")
    if not isinstance(sources, list):
        raise ContractError("P1 manifest has no source registrations.")
    inputs: dict[str, _SourceInput] = {}
    for source in sources:
        if not isinstance(source, dict):
            raise ContractError("P1 manifest source registration is invalid.")
        role = source.get("source_name")
        filename = source.get("filename")
        total_bytes = source.get("total_bytes")
        sha256 = source.get("sha256")
        if (
            not isinstance(role, str)
            or not isinstance(filename, str)
            or not isinstance(total_bytes, int)
            or not isinstance(sha256, str)
        ):
            raise ContractError("P1 manifest source registration is incomplete.")
        inputs[role] = _SourceInput(filename, total_bytes, sha256)
    if set(inputs) != set(_SOURCE_ORDER):
        raise ContractError("P1 manifest must contain reviews and metadata source roles.")
    for role in _SOURCE_ORDER:
        source = inputs[role]
        if source.filename != _SOURCE_FILENAMES[role]:
            raise ContractError("P1 manifest source filename does not match the approved role.")
        path = _raw_path(root, source)
        if not path.exists() or path.stat().st_size != source.total_bytes:
            raise ContractError("P1 source byte size does not match the frozen manifest.")
        if _file_sha256(path) != source.sha256:
            raise ContractError("P1 source SHA-256 does not match the frozen manifest.")
    raw_snapshot = hashlib.sha256(
        f"reviews:{inputs['reviews'].sha256}\nmetadata:{inputs['metadata'].sha256}\n".encode()
    ).hexdigest()
    if snapshot_id != raw_snapshot:
        raise ContractError("P1 manifest snapshot does not match the source hashes.")
    return snapshot_id, observed_audit_sha, selected_category, inputs


def _selected_metadata_parents(path: Path, selected_category: str) -> set[str]:
    selected: set[str] = set()
    for record in _stream_records(path):
        parent = _non_empty_string(record.get("parent_asin"))
        category = _category_label(record.get("categories"))
        if parent is not None and category == selected_category:
            selected.add(parent)
    return selected


def _selected_review_profile(
    path: Path,
    selected_parents: set[str],
) -> tuple[Counter[str], Counter[str]]:
    aspect_counts: Counter[str] = Counter()
    review_counts: Counter[str] = Counter()
    for record in _stream_records(path):
        parent = _non_empty_string(record.get("parent_asin"))
        if parent not in selected_parents:
            continue
        review_counts[parent] += 1
        tokens = set(_TOKEN_PATTERN.findall(_non_empty_string(record.get("text")) or ""))
        for aspect, terms in _ASPECT_TERMS.items():
            if tokens.intersection(terms):
                aspect_counts[aspect] += 1
    return aspect_counts, review_counts


def _stream_records(path: Path):
    try:
        with gzip.open(path, "rt", encoding="utf-8") as source:
            for line in source:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ContractError(f"Selected source has malformed JSON: {path.name}") from error
                if not isinstance(value, dict):
                    raise ContractError(f"Selected source has a non-object JSON record: {path.name}")
                yield value
    except (OSError, UnicodeDecodeError) as error:
        raise ContractError(f"Cannot stream selected source {path.name}.") from error


def _contract_document(contract: EvidenceContract) -> dict[str, Any]:
    return {
        "state": "PASSED",
        "dataset_snapshot_id": contract.dataset_snapshot_id,
        "audit_sha256": contract.audit_sha256,
        "selected_category": contract.selected_category,
        "git_revision": contract.git_revision,
        "observed_at": contract.observed_at,
        "selected_review_count": contract.selected_review_count,
        "selected_product_count": contract.selected_product_count,
        "review_count_quantiles": contract.review_count_quantiles,
        "aspects": [asdict(aspect) for aspect in contract.aspects],
        "enabled_aspects": list(contract.enabled_aspects),
        "aspect_terms": {aspect: list(terms) for aspect, terms in contract.aspect_terms.items()},
        "strength_rules": contract.strength_rules,
        "polarity_terms": {name: list(terms) for name, terms in contract.polarity_terms.items()},
        "label_policy": contract.label_policy,
        "field_mapping": contract.field_mapping,
        "contract_sha256": contract.contract_sha256,
    }


def _render_contract_markdown(contract: EvidenceContract) -> str:
    lines = [
        "# P3 Ice Makers evidence and evaluation contract",
        "",
        f"- Dataset snapshot ID: {contract.dataset_snapshot_id}",
        f"- P2 audit SHA-256: {contract.audit_sha256}",
        f"- Selected category: {contract.selected_category}",
        f"- Script Git revision: {contract.git_revision}",
        f"- Contract SHA-256: {contract.contract_sha256}",
        "",
        "## Field and identifier contract",
        "",
        "- Product/review joins use exact parent_asin only.",
        "- evidence_id is local only: sha256 of evidence-v1, snapshot ID, source role, and one-based JSONL line.",
        "- Review text, user identifiers, product identifiers, and excerpts remain local.",
        "",
        "## Enabled aspects",
        "",
        "| Aspect | Matched reviews | Coverage | Enabled |",
        "|---|---:|---:|---|",
    ]
    for aspect in contract.aspects:
        lines.append(
            f"| {aspect.aspect} | {aspect.matched_reviews} | {aspect.coverage:.6f} | "
            f"{str(aspect.enabled).lower()} |"
        )
    lines.extend(
        [
            "",
            "## Evidence and evaluation policy",
            "",
            "- Strength: INSUFFICIENT 0, WEAK 1, MODERATE 2–4, STRONG 5 or more matching evidence records.",
            "- Polarity is lexical supporting/opposing/conflicting evidence, not a defect, sentiment, or business-impact estimate.",
            "- Labels: silver is deterministic; reviewed requires a manual reviewer record; gold is prohibited.",
            "- P5 fixed cases must record case ID, workflow, expected evidence/no-answer behavior, label provenance, and freeze time.",
        ]
    )
    return "\n".join(lines) + "\n"


def _raw_path(root: Path, source: _SourceInput) -> Path:
    raw_root = (root / "data" / "raw").resolve()
    path = (raw_root / source.filename).resolve()
    if not path.is_relative_to(raw_root):
        raise ContractError("Manifest source path escapes the local raw-data root.")
    return path


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError(f"Cannot read {label}.") from error
    if not isinstance(value, dict):
        raise ContractError(f"{label} is not a JSON object.")
    return value


def _category_label(value: object) -> str | None:
    if not isinstance(value, list):
        return None
    labels = [_non_empty_string(item) for item in value]
    labels = [label for label in labels if label is not None]
    return labels[-1] if labels else None


def _non_empty_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    return normalized or None


def _quantiles(values: list[int]) -> dict[str, int]:
    ordered = sorted(values)
    if not ordered:
        return {name: 0 for name in ("q10", "q25", "q50", "q75", "q90")}
    return {
        name: ordered[math.ceil((len(ordered) - 1) * probability)]
        for name, probability in (("q10", 0.10), ("q25", 0.25), ("q50", 0.50), ("q75", 0.75), ("q90", 0.90))
    }


def _canonical_sha(value: dict[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    _write_text_atomic(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    with temporary_path.open("w", encoding="utf-8", newline="\n") as destination:
        destination.write(text)
        destination.flush()
        os.fsync(destination.fileno())
    temporary_path.replace(path)


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
