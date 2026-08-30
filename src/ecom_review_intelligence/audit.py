"""Streaming, aggregate-only quality audit for the frozen P1 review snapshot."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

P1_MANIFEST_RELATIVE_PATH = Path("artifacts/manifests/phase-1-download.json")
P2_AUDIT_RELATIVE_PATH = Path("artifacts/audits/phase-2-audit.json")
P2_REPORT_RELATIVE_PATH = Path("docs/reports/phase-2-audit.md")
P2_SCOPE_MANIFEST_RELATIVE_PATH = Path("artifacts/manifests/phase-2-scope.json")
P2_SCOPE_REPORT_RELATIVE_PATH = Path("docs/decisions/phase-2-scope.md")
_SOURCE_ORDER = ("reviews", "metadata")
_SOURCE_FILENAMES = {
    "reviews": "Appliances.jsonl.gz",
    "metadata": "meta_Appliances.jsonl.gz",
}
_PARAMETERS: dict[str, int | float] = {
    "min_metadata_products": 10,
    "min_joined_reviews": 500,
    "min_review_to_metadata_coverage": 0.90,
    "min_text_coverage": 0.90,
    "min_valid_rating_coverage": 0.95,
    "min_timestamp_coverage": 0.90,
    "weight_product_percentile": 0.20,
    "weight_review_percentile": 0.25,
    "weight_join_coverage": 0.20,
    "weight_text_coverage": 0.15,
    "weight_rating_coverage": 0.10,
    "weight_timestamp_coverage": 0.10,
}


class AuditError(RuntimeError):
    """Raised when a frozen audit input or safe output contract is invalid."""


@dataclass(frozen=True)
class FieldProfile:
    """Aggregate schema, completeness, and validity observations for one source."""

    total_rows: int
    malformed_rows: int
    field_presence: dict[str, int]
    non_empty: dict[str, int]
    valid_rating_rows: int
    timestamp_rows: int
    timestamp_min: int | None
    timestamp_max: int | None
    valid_verified_purchase_rows: int
    valid_helpful_vote_rows: int
    joined_rows: int = 0
    unmatched_rows: int = 0


@dataclass(frozen=True)
class CategoryCandidate:
    """Aggregate comparability and data-quality metrics for one subcategory."""

    category: str
    metadata_products: int
    joined_reviews: int
    review_to_metadata_coverage: float
    text_coverage: float
    valid_rating_coverage: float
    timestamp_coverage: float
    eligible: bool
    recommendation_score: float | None


@dataclass(frozen=True)
class Phase2Audit:
    """Reproducible P2 audit result with no retained review-level data."""

    snapshot_id: str
    observed_at: str
    git_revision: str
    parameters: dict[str, int | float]
    inputs: dict[str, dict[str, str | int]]
    metadata_profile: FieldProfile
    review_profile: FieldProfile
    metadata_parent_conflicts: int
    candidates: tuple[CategoryCandidate, ...]
    recommended_category: str | None
    audit_sha256: str


@dataclass(frozen=True)
class _SourceInput:
    source_name: str
    filename: str
    total_bytes: int
    sha256: str


@dataclass
class _ProfileAccumulator:
    total_rows: int = 0
    malformed_rows: int = 0
    valid_rating_rows: int = 0
    timestamp_rows: int = 0
    timestamp_min: int | None = None
    timestamp_max: int | None = None
    valid_verified_purchase_rows: int = 0
    valid_helpful_vote_rows: int = 0
    joined_rows: int = 0
    unmatched_rows: int = 0

    def __post_init__(self) -> None:
        self.field_presence: Counter[str] = Counter()
        self.non_empty: Counter[str] = Counter()

    def observe(self, record: dict[str, Any]) -> None:
        self.total_rows += 1
        for field, value in record.items():
            self.field_presence[field] += 1
            if _is_non_empty(value):
                self.non_empty[field] += 1

        rating = record.get("rating")
        if _is_valid_rating(rating):
            self.valid_rating_rows += 1
        timestamp = record.get("timestamp")
        if _is_valid_timestamp(timestamp):
            self.timestamp_rows += 1
            self.timestamp_min = timestamp if self.timestamp_min is None else min(self.timestamp_min, timestamp)
            self.timestamp_max = timestamp if self.timestamp_max is None else max(self.timestamp_max, timestamp)
        if isinstance(record.get("verified_purchase"), bool):
            self.valid_verified_purchase_rows += 1
        helpful_vote = record.get("helpful_vote")
        if isinstance(helpful_vote, int) and not isinstance(helpful_vote, bool) and helpful_vote >= 0:
            self.valid_helpful_vote_rows += 1

    def freeze(self) -> FieldProfile:
        return FieldProfile(
            total_rows=self.total_rows,
            malformed_rows=self.malformed_rows,
            field_presence=dict(sorted(self.field_presence.items())),
            non_empty=dict(sorted(self.non_empty.items())),
            valid_rating_rows=self.valid_rating_rows,
            timestamp_rows=self.timestamp_rows,
            timestamp_min=self.timestamp_min,
            timestamp_max=self.timestamp_max,
            valid_verified_purchase_rows=self.valid_verified_purchase_rows,
            valid_helpful_vote_rows=self.valid_helpful_vote_rows,
            joined_rows=self.joined_rows,
            unmatched_rows=self.unmatched_rows,
        )


@dataclass
class _CandidateAccumulator:
    metadata_products: int = 0
    joined_reviews: int = 0
    text_rows: int = 0
    valid_rating_rows: int = 0
    timestamp_rows: int = 0

    def __post_init__(self) -> None:
        self.joined_parents: set[str] = set()


def audit_phase2(
    project_root: Path,
    *,
    git_revision: str,
    observed_at: str | None = None,
) -> Phase2Audit:
    """Validate P1 inputs then stream aggregate data-quality and scope metrics."""

    root = project_root.resolve()
    snapshot_id, inputs = _load_and_verify_snapshot(root)
    metadata_path = _raw_source_path(root, inputs["metadata"])
    review_path = _raw_source_path(root, inputs["reviews"])

    metadata_profile, category_by_parent, category_display, parent_conflicts = _audit_metadata(
        metadata_path
    )
    candidates = _initialize_candidates(category_by_parent)
    review_profile = _audit_reviews(review_path, category_by_parent, candidates)
    candidate_results = _freeze_candidates(candidates, category_display)
    recommended_category = next(
        (candidate.category for candidate in candidate_results if candidate.eligible),
        None,
    )
    input_records = {
        source_name: {
            "filename": inputs[source_name].filename,
            "total_bytes": inputs[source_name].total_bytes,
            "sha256": inputs[source_name].sha256,
        }
        for source_name in _SOURCE_ORDER
    }
    audit_without_hash = {
        "state": "PASSED" if recommended_category is not None else "BLOCKED",
        "dataset_snapshot_id": snapshot_id,
        "observed_at": observed_at or _utc_now(),
        "git_revision": git_revision,
        "parameters": dict(_PARAMETERS),
        "inputs": input_records,
        "metadata_profile": asdict(metadata_profile),
        "review_profile": asdict(review_profile),
        "metadata_parent_conflicts": parent_conflicts,
        "candidates": [asdict(candidate) for candidate in candidate_results],
        "recommended_category": recommended_category,
    }
    audit_sha256 = _sha256_text(_canonical_json(audit_without_hash))
    return Phase2Audit(
        snapshot_id=snapshot_id,
        observed_at=audit_without_hash["observed_at"],
        git_revision=git_revision,
        parameters=dict(_PARAMETERS),
        inputs=input_records,
        metadata_profile=metadata_profile,
        review_profile=review_profile,
        metadata_parent_conflicts=parent_conflicts,
        candidates=tuple(candidate_results),
        recommended_category=recommended_category,
        audit_sha256=audit_sha256,
    )


def write_phase2_outputs(project_root: Path, audit: Phase2Audit) -> tuple[Path, Path]:
    """Atomically write aggregate-only P2 JSON and Markdown artifacts."""

    root = project_root.resolve()
    audit_path = root / P2_AUDIT_RELATIVE_PATH
    report_path = root / P2_REPORT_RELATIVE_PATH
    document = _audit_document(audit)
    _write_json_atomic(audit_path, document)
    _write_text_atomic(report_path, _render_audit_markdown(audit))
    return audit_path, report_path


def write_scope_decision(
    project_root: Path,
    audit: Phase2Audit,
    selected_category: str,
) -> tuple[Path, Path]:
    """Atomically freeze an eligible P2 scope with its audit lineage."""

    selected_candidate = next(
        (candidate for candidate in audit.candidates if candidate.category == selected_category),
        None,
    )
    if selected_candidate is None:
        raise AuditError("Selected category is not present in the P2 candidate comparison.")
    if not selected_candidate.eligible:
        raise AuditError("Selected category is not eligible for evidence-engine scope.")

    root = project_root.resolve()
    decision = {
        "state": "PASSED",
        "decision_at": audit.observed_at,
        "dataset_snapshot_id": audit.snapshot_id,
        "audit_sha256": audit.audit_sha256,
        "git_revision": audit.git_revision,
        "selected_category": selected_candidate.category,
        "recommended_category": audit.recommended_category,
        "selection_mode": (
            "predeclared_recommendation"
            if selected_candidate.category == audit.recommended_category
            else "explicit_eligible_override"
        ),
        "selection_metrics": asdict(selected_candidate),
        "selection_rule": {
            "parameters": audit.parameters,
            "recommended_category": audit.recommended_category,
        },
    }
    manifest_path = root / P2_SCOPE_MANIFEST_RELATIVE_PATH
    report_path = root / P2_SCOPE_REPORT_RELATIVE_PATH
    _write_json_atomic(manifest_path, decision)
    _write_text_atomic(report_path, _render_scope_markdown(decision))
    return manifest_path, report_path


def _load_and_verify_snapshot(root: Path) -> tuple[str, dict[str, _SourceInput]]:
    manifest_path = root / P1_MANIFEST_RELATIVE_PATH
    manifest = _read_json(manifest_path, "P1 manifest")
    if manifest.get("state") != "PASSED":
        raise AuditError("P1 manifest state must be PASSED before P2 audit.")
    raw_sources = manifest.get("sources")
    if not isinstance(raw_sources, list):
        raise AuditError("P1 manifest has no valid source registrations.")

    inputs: dict[str, _SourceInput] = {}
    for record in raw_sources:
        if not isinstance(record, dict):
            raise AuditError("P1 manifest source registration is not an object.")
        source_name = record.get("source_name")
        filename = record.get("filename")
        total_bytes = record.get("total_bytes")
        sha256 = record.get("sha256")
        if (
            not isinstance(source_name, str)
            or not isinstance(filename, str)
            or not isinstance(total_bytes, int)
            or not isinstance(sha256, str)
        ):
            raise AuditError("P1 manifest source registration has invalid required fields.")
        if source_name in inputs:
            raise AuditError("P1 manifest has duplicate source roles.")
        inputs[source_name] = _SourceInput(source_name, filename, total_bytes, sha256)

    if set(inputs) != set(_SOURCE_ORDER):
        raise AuditError("P1 manifest must contain exactly reviews and metadata source roles.")
    for source_name in _SOURCE_ORDER:
        source = inputs[source_name]
        if source.filename != _SOURCE_FILENAMES[source_name]:
            raise AuditError("P1 manifest source filename does not match the approved role.")
        path = _raw_source_path(root, source)
        if not path.exists() or path.stat().st_size != source.total_bytes:
            raise AuditError("P1 source byte count does not match the frozen manifest.")
        if _file_sha256(path) != source.sha256:
            raise AuditError("P1 source SHA-256 does not match the frozen manifest.")

    expected_snapshot_id = _snapshot_id(inputs)
    if manifest.get("dataset_snapshot_id") != expected_snapshot_id:
        raise AuditError("P1 manifest dataset snapshot ID does not match source hashes.")
    return expected_snapshot_id, inputs


def _audit_metadata(
    metadata_path: Path,
) -> tuple[FieldProfile, dict[str, str], dict[str, str], int]:
    profile = _ProfileAccumulator()
    category_by_parent: dict[str, str] = {}
    category_display: dict[str, str] = {}
    conflicts: set[str] = set()

    for record in _stream_records(metadata_path, profile):
        if record is None:
            continue
        parent_asin = _non_empty_string(record.get("parent_asin"))
        category = _category_label(record.get("categories"))
        if parent_asin is None or category is None:
            continue
        category_key = category.casefold()
        existing_display = category_display.get(category_key)
        if existing_display is None or category < existing_display:
            category_display[category_key] = category
        existing_category = category_by_parent.get(parent_asin)
        if existing_category is None:
            category_by_parent[parent_asin] = category_key
        elif existing_category != category_key:
            conflicts.add(parent_asin)

    for parent_asin in conflicts:
        category_by_parent.pop(parent_asin, None)
    return profile.freeze(), category_by_parent, category_display, len(conflicts)


def _initialize_candidates(category_by_parent: dict[str, str]) -> dict[str, _CandidateAccumulator]:
    candidates: dict[str, _CandidateAccumulator] = {}
    for category_key in category_by_parent.values():
        candidates.setdefault(category_key, _CandidateAccumulator()).metadata_products += 1
    return candidates


def _audit_reviews(
    review_path: Path,
    category_by_parent: dict[str, str],
    candidates: dict[str, _CandidateAccumulator],
) -> FieldProfile:
    profile = _ProfileAccumulator()
    for record in _stream_records(review_path, profile):
        if record is None:
            continue
        parent_asin = _non_empty_string(record.get("parent_asin"))
        category_key = category_by_parent.get(parent_asin or "")
        if category_key is None:
            profile.unmatched_rows += 1
            continue

        profile.joined_rows += 1
        candidate = candidates[category_key]
        candidate.joined_reviews += 1
        candidate.joined_parents.add(parent_asin)
        if _non_empty_string(record.get("text")) is not None:
            candidate.text_rows += 1
        if _is_valid_rating(record.get("rating")):
            candidate.valid_rating_rows += 1
        if _is_valid_timestamp(record.get("timestamp")):
            candidate.timestamp_rows += 1
    return profile.freeze()


def _stream_records(path: Path, profile: _ProfileAccumulator):
    try:
        with gzip.open(path, "rt", encoding="utf-8") as source:
            for line in source:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    profile.malformed_rows += 1
                    yield None
                    continue
                if not isinstance(record, dict):
                    profile.malformed_rows += 1
                    yield None
                    continue
                profile.observe(record)
                yield record
    except (OSError, UnicodeDecodeError) as error:
        raise AuditError(f"Cannot stream frozen gzip source {path.name}.") from error


def _freeze_candidates(
    candidates: dict[str, _CandidateAccumulator],
    category_display: dict[str, str],
) -> list[CategoryCandidate]:
    product_percentiles = _percentiles(
        {category: candidate.metadata_products for category, candidate in candidates.items()}
    )
    review_percentiles = _percentiles(
        {category: candidate.joined_reviews for category, candidate in candidates.items()}
    )
    frozen: list[CategoryCandidate] = []
    for category_key, candidate in candidates.items():
        join_coverage = _ratio(len(candidate.joined_parents), candidate.metadata_products)
        text_coverage = _ratio(candidate.text_rows, candidate.joined_reviews)
        rating_coverage = _ratio(candidate.valid_rating_rows, candidate.joined_reviews)
        timestamp_coverage = _ratio(candidate.timestamp_rows, candidate.joined_reviews)
        eligible = (
            candidate.metadata_products >= _PARAMETERS["min_metadata_products"]
            and candidate.joined_reviews >= _PARAMETERS["min_joined_reviews"]
            and join_coverage >= _PARAMETERS["min_review_to_metadata_coverage"]
            and text_coverage >= _PARAMETERS["min_text_coverage"]
            and rating_coverage >= _PARAMETERS["min_valid_rating_coverage"]
            and timestamp_coverage >= _PARAMETERS["min_timestamp_coverage"]
        )
        score = None
        if eligible:
            score = (
                _PARAMETERS["weight_product_percentile"] * product_percentiles[category_key]
                + _PARAMETERS["weight_review_percentile"] * review_percentiles[category_key]
                + _PARAMETERS["weight_join_coverage"] * join_coverage
                + _PARAMETERS["weight_text_coverage"] * text_coverage
                + _PARAMETERS["weight_rating_coverage"] * rating_coverage
                + _PARAMETERS["weight_timestamp_coverage"] * timestamp_coverage
            )
        frozen.append(
            CategoryCandidate(
                category=category_display[category_key],
                metadata_products=candidate.metadata_products,
                joined_reviews=candidate.joined_reviews,
                review_to_metadata_coverage=round(join_coverage, 6),
                text_coverage=round(text_coverage, 6),
                valid_rating_coverage=round(rating_coverage, 6),
                timestamp_coverage=round(timestamp_coverage, 6),
                eligible=eligible,
                recommendation_score=None if score is None else round(score, 6),
            )
        )
    return sorted(
        frozen,
        key=lambda candidate: (
            not candidate.eligible,
            -(candidate.recommendation_score or 0.0),
            candidate.category.casefold(),
        ),
    )


def _percentiles(values: dict[str, int]) -> dict[str, float]:
    ordered = sorted(set(values.values()))
    if len(ordered) <= 1:
        return {key: 1.0 for key in values}
    positions = {value: index / (len(ordered) - 1) for index, value in enumerate(ordered)}
    return {key: positions[value] for key, value in values.items()}


def _audit_document(audit: Phase2Audit) -> dict[str, Any]:
    state = "PASSED" if audit.recommended_category is not None else "BLOCKED"
    return {
        "state": state,
        "dataset_snapshot_id": audit.snapshot_id,
        "observed_at": audit.observed_at,
        "git_revision": audit.git_revision,
        "parameters": audit.parameters,
        "inputs": audit.inputs,
        "metadata_profile": asdict(audit.metadata_profile),
        "review_profile": asdict(audit.review_profile),
        "metadata_parent_conflicts": audit.metadata_parent_conflicts,
        "candidates": [asdict(candidate) for candidate in audit.candidates],
        "recommended_category": audit.recommended_category,
        "audit_sha256": audit.audit_sha256,
    }


def _render_audit_markdown(audit: Phase2Audit) -> str:
    lines = [
        "# P2 stream audit and scope-selection report",
        "",
        f"- State: {'PASSED' if audit.recommended_category is not None else 'BLOCKED'}",
        f"- Observed at (UTC): {audit.observed_at}",
        f"- Dataset snapshot ID: {audit.snapshot_id}",
        f"- Script Git revision: {audit.git_revision}",
        f"- Audit SHA-256: {audit.audit_sha256}",
        "",
        "## Data grain and validation",
        "",
        (
            "- Metadata grain: one source JSON object per metadata line; duplicate/conflicting "
            "parent keys are counted and excluded from candidate product counts."
        ),
        "- Review grain: one source JSON object per review line; joins are exact on parent_asin.",
        "- Inputs were re-hashed against the frozen P1 manifest before parsing.",
        "",
        "## Aggregate quality profile",
        "",
        "| Source | Rows | Malformed | Joined | Unmatched | Valid ratings | Timestamps | Valid verified purchase | Valid helpful vote |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for source_name, profile in (("metadata", audit.metadata_profile), ("reviews", audit.review_profile)):
        lines.append(
            f"| {source_name} | {profile.total_rows} | {profile.malformed_rows} | "
            f"{profile.joined_rows} | {profile.unmatched_rows} | {profile.valid_rating_rows} | "
            f"{profile.timestamp_rows} | {profile.valid_verified_purchase_rows} | "
            f"{profile.valid_helpful_vote_rows} |"
        )
    lines.extend(
        [
            "",
            f"- Conflicting metadata parent keys excluded: {audit.metadata_parent_conflicts}",
            "",
            "## Candidate comparison",
            "",
            "| Category | Products | Joined reviews | Join coverage | Text coverage | Rating coverage | Timestamp coverage | Eligible | Score |",
            "|---|---:|---:|---:|---:|---:|---:|---|---:|",
        ]
    )
    for candidate in audit.candidates:
        score = "-" if candidate.recommendation_score is None else f"{candidate.recommendation_score:.6f}"
        lines.append(
            f"| {_markdown_cell(candidate.category)} | {candidate.metadata_products} | "
            f"{candidate.joined_reviews} | {candidate.review_to_metadata_coverage:.6f} | "
            f"{candidate.text_coverage:.6f} | {candidate.valid_rating_coverage:.6f} | "
            f"{candidate.timestamp_coverage:.6f} | {str(candidate.eligible).lower()} | {score} |"
        )
    lines.extend(
        [
            "",
            "## Selection rule",
            "",
            "- Eligibility and weighted score use the frozen parameters in the JSON artifact.",
            f"- Recommended category: {audit.recommended_category or 'none; P3 must not start'}",
            "",
            "## Publication and interpretation boundary",
            "",
            (
                "- This report contains only aggregates. It omits review text, user identifiers, "
                "product identifiers, titles, source-record locations, and local absolute paths."
            ),
            (
                "- Coverage and candidate score establish data suitability for this project scope "
                "only. They do not estimate product quality, defect rate, sales, or business impact."
            ),
        ]
    )
    return "\n".join(lines) + "\n"


def _render_scope_markdown(decision: dict[str, Any]) -> str:
    metrics = decision["selection_metrics"]
    if not isinstance(metrics, dict):
        raise AuditError("P2 scope decision has invalid selection metrics.")
    return "\n".join(
        [
            "# P2 selected scope decision",
            "",
            "- State: PASSED",
            f"- Decision at (UTC): {decision['decision_at']}",
            f"- Dataset snapshot ID: {decision['dataset_snapshot_id']}",
            f"- Audit SHA-256: {decision['audit_sha256']}",
            f"- Script Git revision: {decision['git_revision']}",
            f"- Selection mode: {decision['selection_mode']}",
            f"- Predeclared recommendation: {decision['recommended_category']}",
            "",
            "## Selected scope",
            "",
            f"- Subcategory: {decision['selected_category']}",
            f"- Metadata products: {metrics['metadata_products']}",
            f"- Joined reviews: {metrics['joined_reviews']}",
            f"- Review-to-metadata coverage: {metrics['review_to_metadata_coverage']:.6f}",
            f"- Text coverage: {metrics['text_coverage']:.6f}",
            f"- Valid-rating coverage: {metrics['valid_rating_coverage']:.6f}",
            f"- Timestamp coverage: {metrics['timestamp_coverage']:.6f}",
            f"- Recommendation score: {metrics['recommendation_score']:.6f}",
            "",
            "## Decision boundary",
            "",
            (
                "- This scope is selected by the frozen P2 comparability heuristic, not by a claim "
                "about product quality, market size, or business impact."
            ),
            "- The decision contains aggregate metrics only and no product/review record data.",
        ]
    ) + "\n"


def _raw_source_path(root: Path, source: _SourceInput) -> Path:
    raw_root = (root / "data" / "raw").resolve()
    path = (raw_root / source.filename).resolve()
    if not path.is_relative_to(raw_root):
        raise AuditError("P1 manifest source path escapes the local raw-data directory.")
    return path


def _snapshot_id(inputs: dict[str, _SourceInput]) -> str:
    payload = "".join(f"{source_name}:{inputs[source_name].sha256}\n" for source_name in _SOURCE_ORDER)
    return _sha256_text(payload)


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
        raise AuditError(f"Cannot read {label}.") from error
    if not isinstance(value, dict):
        raise AuditError(f"{label} is not a JSON object.")
    return value


def _is_non_empty(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return value is not None


def _non_empty_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    return normalized or None


def _category_label(value: object) -> str | None:
    if not isinstance(value, list):
        return None
    labels = [_non_empty_string(item) for item in value]
    non_empty_labels = [label for label in labels if label is not None]
    return non_empty_labels[-1] if non_empty_labels else None


def _is_valid_rating(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and 1.0 <= float(value) <= 5.0
    )


def _is_valid_timestamp(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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


def _markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
