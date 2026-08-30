"""Fixed-case retrieval evaluation with aggregate-only public outputs."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path

from ecom_review_intelligence.engine import (
    EngineError,
    fts_query_aspect,
    query_aspect,
    verify_citation,
)

_WORKFLOWS = frozenset({"diagnosis", "comparison", "triage", "no_answer"})
_RETRIEVERS = {"lexical": query_aspect, "fts5_bm25": fts_query_aspect}


class EvaluationError(RuntimeError):
    """Raised when a fixed evaluation case cannot honor the frozen P3 contract."""


@dataclass(frozen=True)
class FixedCase:
    case_id: str
    workflow: str
    parent_asins: tuple[str, ...]
    aspect: str
    expected_behavior: str
    expected_evidence_ids: tuple[str, ...]
    label_provenance: str
    reviewer_status: str
    frozen_at: str


@dataclass(frozen=True)
class RetrievalMetrics:
    evidence_case_count: int
    expected_evidence_count: int
    retrieved_evidence_count: int
    recalled_evidence_count: int
    recall_at_k: float
    case_hit_rate: float
    citation_checks: int
    citation_integrity: float
    product_join_errors: int
    no_answer_false_positives: int
    mean_latency_ms: float
    p95_latency_ms: float


@dataclass(frozen=True)
class EvaluationResult:
    dataset_snapshot_id: str
    contract_sha256: str
    store_build_revision: str
    case_corpus_sha256: str
    case_count: int
    workflow_counts: dict[str, int]
    label_counts: dict[str, int]
    metrics: dict[str, RetrievalMetrics]
    quality_gates_passed: bool


def generate_fixed_cases(
    store_path: Path,
    *,
    frozen_at: str,
    limit: int = 5,
) -> tuple[FixedCase, ...]:
    """Create 27 deterministic local-only silver cases from the frozen store."""

    if limit <= 0:
        raise EvaluationError("Case-generation limit must be positive.")
    metadata = _store_metadata(store_path)
    aspect_terms = _load_aspect_terms(metadata)
    aspects = tuple(sorted(aspect_terms))
    if len(aspects) != 8:
        raise EvaluationError("P5 requires exactly the eight enabled P3 aspects.")
    cases: list[FixedCase] = []
    workflow_specs = (
        ("diagnosis", aspects, 1, "DIAG"),
        ("comparison", aspects, 2, "COMPARE"),
        ("triage", aspects[:7], 3, "TRIAGE"),
    )
    for workflow, workflow_aspects, scope_size, abbreviation in workflow_specs:
        for index, aspect in enumerate(workflow_aspects, start=1):
            parents = _parents_with_evidence(store_path, tuple(aspect_terms[aspect]), scope_size)
            evidence = query_aspect(store_path, parents, aspect, limit=limit).evidence
            if not evidence:
                raise EvaluationError(f"Cannot derive a deterministic silver case for {aspect}.")
            cases.append(
                FixedCase(
                    case_id=f"P5-{abbreviation}-{index:02d}",
                    workflow=workflow,
                    parent_asins=parents,
                    aspect=aspect,
                    expected_behavior="evidence",
                    expected_evidence_ids=tuple(item.evidence_id for item in evidence),
                    label_provenance="silver",
                    reviewer_status="not_reviewed",
                    frozen_at=frozen_at,
                )
            )
    for index, aspect in enumerate(aspects[:4], start=1):
        parent = _parent_without_evidence(store_path, tuple(aspect_terms[aspect]))
        cases.append(
            FixedCase(
                case_id=f"P5-NOANSWER-{index:02d}",
                workflow="no_answer",
                parent_asins=(parent,),
                aspect=aspect,
                expected_behavior="no_answer",
                expected_evidence_ids=(),
                label_provenance="silver",
                reviewer_status="not_reviewed",
                frozen_at=frozen_at,
            )
        )
    if len(cases) != 27:
        raise EvaluationError("P5 case generator did not produce the required 27 cases.")
    return tuple(cases)


def evaluate_fixed_cases(
    store_path: Path,
    cases: Sequence[FixedCase | Mapping[str, object]],
    *,
    limit: int,
) -> EvaluationResult:
    """Evaluate frozen local cases without exporting IDs, text, or product scopes."""

    if limit <= 0:
        raise EvaluationError("Evaluation limit must be positive.")
    parsed_cases = tuple(_parse_case(value) for value in cases)
    _validate_case_set(parsed_cases)
    metadata = _store_metadata(store_path)
    _load_aspect_terms(metadata)
    metrics = {
        name: _evaluate_retriever(store_path, parsed_cases, limit, retriever)
        for name, retriever in _RETRIEVERS.items()
    }
    quality_gates_passed = all(
        item.citation_integrity == 1.0
        and item.product_join_errors == 0
        and item.no_answer_false_positives == 0
        for item in metrics.values()
    )
    return EvaluationResult(
        dataset_snapshot_id=metadata["dataset_snapshot_id"],
        contract_sha256=metadata["contract_sha256"],
        store_build_revision=metadata.get("build_git_revision", "unknown"),
        case_corpus_sha256=_case_corpus_sha256(parsed_cases),
        case_count=len(parsed_cases),
        workflow_counts=dict(sorted(Counter(item.workflow for item in parsed_cases).items())),
        label_counts=dict(sorted(Counter(item.label_provenance for item in parsed_cases).items())),
        metrics=metrics,
        quality_gates_passed=quality_gates_passed,
    )


def write_local_cases(
    project_root: Path,
    cases: Sequence[FixedCase],
    *,
    generated_by_git_revision: str,
) -> Path:
    """Write local-only fixed cases, including their sensitive scopes and locators."""

    metadata = _store_metadata(project_root / "data" / "processed" / "ice_makers_evidence.sqlite")
    payload = {
        "schema_version": "phase-5-fixed-cases-v1",
        "dataset_snapshot_id": metadata["dataset_snapshot_id"],
        "contract_sha256": metadata["contract_sha256"],
        "generated_by_git_revision": generated_by_git_revision,
        "cases": [asdict(case) for case in cases],
    }
    path = project_root / "artifacts" / "evals" / "phase-5-cases.json"
    _atomic_write_json(path, payload)
    return path


def write_evaluation_outputs(
    project_root: Path,
    result: EvaluationResult,
    *,
    git_revision: str,
    evaluated_at: str,
    limit: int,
) -> tuple[Path, Path]:
    """Write aggregate-only P5 report files suitable for version control."""

    payload = {
        "state": "PASSED" if result.quality_gates_passed else "FAILED",
        "evaluated_at": evaluated_at,
        "git_revision": git_revision,
        "dataset_snapshot_id": result.dataset_snapshot_id,
        "contract_sha256": result.contract_sha256,
        "store_build_revision": result.store_build_revision,
        "case_corpus_sha256": result.case_corpus_sha256,
        "case_count": result.case_count,
        "workflow_counts": result.workflow_counts,
        "label_counts": result.label_counts,
        "retrieval_limit": limit,
        "metrics": {name: asdict(metric) for name, metric in result.metrics.items()},
        "quality_gates": {
            "citation_integrity": 1.0,
            "product_join_errors": 0,
            "no_answer_false_positives": 0,
            "passed": result.quality_gates_passed,
        },
        "interpretation_limit": (
            "Silver expected evidence is deterministically derived from the lexical baseline; "
            "this is a reproducibility and regression check, not an independent relevance or business-impact benchmark."
        ),
        "data_boundary": "Aggregate-only; no product IDs, evidence IDs, review text, or local paths.",
    }
    summary_path = project_root / "artifacts" / "evaluation-summaries" / "phase-5-retrieval.json"
    report_path = project_root / "docs" / "reports" / "phase-5-retrieval-evaluation.md"
    _atomic_write_json(summary_path, payload)
    report = _render_report(payload)
    _atomic_write_text(report_path, report)
    return summary_path, report_path


def _evaluate_retriever(
    store_path: Path,
    cases: tuple[FixedCase, ...],
    limit: int,
    retriever: object,
) -> RetrievalMetrics:
    evidence_case_count = 0
    expected_count = 0
    recalled_count = 0
    hit_case_count = 0
    retrieved_count = 0
    citation_checks = 0
    valid_citations = 0
    product_join_errors = 0
    no_answer_false_positives = 0
    latencies: list[float] = []
    for case in cases:
        started = time.perf_counter_ns()
        try:
            result = retriever(store_path, case.parent_asins, case.aspect, limit=limit)
        except EngineError as error:
            raise EvaluationError(f"Retrieval failed for fixed case {case.case_id}.") from error
        latencies.append((time.perf_counter_ns() - started) / 1_000_000)
        retrieved_ids = {item.evidence_id for item in result.evidence}
        retrieved_count += len(result.evidence)
        citation_checks += len(result.evidence)
        valid_citations += sum(verify_citation(store_path, item.evidence_id) for item in result.evidence)
        product_join_errors += sum(
            item.parent_asin not in case.parent_asins for item in result.evidence
        )
        if case.expected_behavior == "no_answer":
            no_answer_false_positives += int(bool(result.evidence))
            continue
        evidence_case_count += 1
        expected_ids = set(case.expected_evidence_ids)
        recalled = len(expected_ids.intersection(retrieved_ids))
        expected_count += len(expected_ids)
        recalled_count += recalled
        hit_case_count += int(recalled > 0)
    return RetrievalMetrics(
        evidence_case_count=evidence_case_count,
        expected_evidence_count=expected_count,
        retrieved_evidence_count=retrieved_count,
        recalled_evidence_count=recalled_count,
        recall_at_k=_ratio(recalled_count, expected_count),
        case_hit_rate=_ratio(hit_case_count, evidence_case_count),
        citation_checks=citation_checks,
        citation_integrity=_ratio(valid_citations, citation_checks),
        product_join_errors=product_join_errors,
        no_answer_false_positives=no_answer_false_positives,
        mean_latency_ms=round(sum(latencies) / len(latencies), 6),
        p95_latency_ms=round(_percentile(latencies, 0.95), 6),
    )


def _parse_case(value: FixedCase | Mapping[str, object]) -> FixedCase:
    if isinstance(value, FixedCase):
        return value
    if not isinstance(value, Mapping):
        raise EvaluationError("Each fixed case must be an object.")
    parent_asins = value.get("parent_asins")
    expected_ids = value.get("expected_evidence_ids")
    fields = (
        "case_id",
        "workflow",
        "aspect",
        "expected_behavior",
        "label_provenance",
        "reviewer_status",
        "frozen_at",
    )
    if (
        any(not isinstance(value.get(field), str) or not value[field] for field in fields)
        or not isinstance(parent_asins, list)
        or not parent_asins
        or not all(isinstance(item, str) and item for item in parent_asins)
        or not isinstance(expected_ids, list)
        or not all(isinstance(item, str) and item for item in expected_ids)
    ):
        raise EvaluationError("Fixed case fields are invalid.")
    return FixedCase(
        case_id=value["case_id"],
        workflow=value["workflow"],
        parent_asins=tuple(parent_asins),
        aspect=value["aspect"],
        expected_behavior=value["expected_behavior"],
        expected_evidence_ids=tuple(expected_ids),
        label_provenance=value["label_provenance"],
        reviewer_status=value["reviewer_status"],
        frozen_at=value["frozen_at"],
    )


def _validate_case_set(cases: tuple[FixedCase, ...]) -> None:
    if not cases:
        raise EvaluationError("At least one fixed case is required.")
    if len({item.case_id for item in cases}) != len(cases):
        raise EvaluationError("Fixed case IDs must be unique.")
    for case in cases:
        if case.workflow not in _WORKFLOWS:
            raise EvaluationError("Fixed case workflow is invalid.")
        if case.label_provenance != "silver" or case.reviewer_status != "not_reviewed":
            raise EvaluationError("P5 supports only unreviewed silver fixed cases.")
        if case.expected_behavior == "evidence" and not case.expected_evidence_ids:
            raise EvaluationError("Evidence cases require at least one silver expected locator.")
        if case.expected_behavior == "no_answer" and case.expected_evidence_ids:
            raise EvaluationError("No-answer cases must not contain expected locators.")
        if case.expected_behavior not in {"evidence", "no_answer"}:
            raise EvaluationError("Fixed case expected behavior is invalid.")
        if (case.workflow == "no_answer") != (case.expected_behavior == "no_answer"):
            raise EvaluationError("No-answer workflow and expectation must agree.")


def _store_metadata(store_path: Path) -> dict[str, str]:
    try:
        with closing(sqlite3.connect(store_path)) as connection:
            metadata = dict(connection.execute("SELECT key, value FROM store_meta"))
    except sqlite3.Error as error:
        raise EvaluationError("Cannot read the frozen local evidence store.") from error
    if not {"dataset_snapshot_id", "contract_sha256", "aspect_terms"}.issubset(metadata):
        raise EvaluationError("Local evidence store is missing required P4 lineage metadata.")
    return metadata


def _load_aspect_terms(metadata: Mapping[str, str]) -> dict[str, list[str]]:
    try:
        terms = json.loads(metadata["aspect_terms"])
    except (KeyError, json.JSONDecodeError) as error:
        raise EvaluationError("Local evidence store has invalid aspect metadata.") from error
    if (
        not isinstance(terms, dict)
        or not terms
        or not all(
            isinstance(name, str)
            and isinstance(values, list)
            and values
            and all(isinstance(term, str) for term in values)
            for name, values in terms.items()
        )
    ):
        raise EvaluationError("Local evidence store has invalid aspect metadata.")
    return terms


def _parents_with_evidence(store_path: Path, terms: tuple[str, ...], count: int) -> tuple[str, ...]:
    placeholders = " OR ".join("lower(text) LIKE ?" for _ in terms)
    with closing(sqlite3.connect(store_path)) as connection:
        rows = connection.execute(
            f"""
            SELECT parent_asin
            FROM reviews
            WHERE {placeholders}
            GROUP BY parent_asin
            ORDER BY parent_asin
            LIMIT ?
            """,
            [*[f"%{term.lower()}%" for term in terms], count],
        ).fetchall()
    if len(rows) != count:
        raise EvaluationError("Insufficient scoped products for fixed evidence case generation.")
    return tuple(row[0] for row in rows)


def _parent_without_evidence(store_path: Path, terms: tuple[str, ...]) -> str:
    predicates = " OR ".join("lower(reviews.text) LIKE ?" for _ in terms)
    with closing(sqlite3.connect(store_path)) as connection:
        row = connection.execute(
            f"""
            SELECT products.parent_asin
            FROM products
            WHERE NOT EXISTS (
                SELECT 1
                FROM reviews
                WHERE reviews.parent_asin = products.parent_asin AND ({predicates})
            )
            ORDER BY products.parent_asin
            LIMIT 1
            """,
            [f"%{term.lower()}%" for term in terms],
        ).fetchone()
    if row is None:
        raise EvaluationError("No true no-answer scope is available for the frozen aspect.")
    return row[0]


def _case_corpus_sha256(cases: tuple[FixedCase, ...]) -> str:
    return hashlib.sha256(
        json.dumps([asdict(item) for item in cases], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _ratio(numerator: int, denominator: int) -> float:
    return 1.0 if denominator == 0 else round(numerator / denominator, 6)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * percentile) - 1)]


def _atomic_write_json(path: Path, value: Mapping[str, object]) -> None:
    _atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(content, encoding="utf-8")
    temporary_path.replace(path)


def _render_report(payload: Mapping[str, object]) -> str:
    metrics = payload["metrics"]
    assert isinstance(metrics, dict)
    lines = [
        "# P5 fixed-case retrieval evaluation",
        "",
        f"- State: {payload['state']}",
        f"- Evaluation Git revision: {payload['git_revision']}",
        f"- P4 store build revision: {payload['store_build_revision']}",
        f"- Dataset snapshot ID: {payload['dataset_snapshot_id']}",
        f"- P3 contract SHA-256: {payload['contract_sha256']}",
        f"- Fixed cases: {payload['case_count']} ({payload['workflow_counts']})",
        f"- Labels: {payload['label_counts']}",
        "",
        "## Aggregate results",
        "",
        "| Retriever | Recall@k | Case hit rate | Citation integrity | Join errors | No-answer false positives | Mean ms | P95 ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, value in metrics.items():
        assert isinstance(value, dict)
        lines.append(
            "| {name} | {recall} | {hit_rate} | {integrity} | {join_errors} | {false_positives} | {mean} | {p95} |".format(
                name=name,
                recall=value["recall_at_k"],
                hit_rate=value["case_hit_rate"],
                integrity=value["citation_integrity"],
                join_errors=value["product_join_errors"],
                false_positives=value["no_answer_false_positives"],
                mean=value["mean_latency_ms"],
                p95=value["p95_latency_ms"],
            )
        )
    lines.extend(
        [
            "",
            "## Interpretation boundary",
            "",
            str(payload["interpretation_limit"]),
            "",
            str(payload["data_boundary"]),
            "",
        ]
    )
    return "\n".join(lines)
