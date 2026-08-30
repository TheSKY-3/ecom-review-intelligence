"""Command-line entry points for ECom Review Intelligence."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from ecom_review_intelligence.audit import (
    AuditError,
    Phase2Audit,
    audit_phase2,
    write_phase2_outputs,
    write_scope_decision,
)
from ecom_review_intelligence.contract import (
    ContractError,
    EvidenceContract,
    build_evidence_contract,
    write_contract_outputs,
)
from ecom_review_intelligence.download import (
    DEFAULT_CHUNK_SIZE,
    DownloadError,
    DownloadResult,
    RemoteObject,
    download_remote,
    probe_remote,
    register_existing,
    write_download_outputs,
)
from ecom_review_intelligence.download import DEFAULT_TIMEOUT_SECONDS as DOWNLOAD_TIMEOUT_SECONDS
from ecom_review_intelligence.engine import (
    EngineError,
    LocalStoreBuild,
    build_local_store,
    write_phase4_outputs,
)
from ecom_review_intelligence.evaluation import (
    EvaluationError,
    EvaluationResult,
    FixedCase,
    evaluate_fixed_cases,
    generate_fixed_cases,
    write_evaluation_outputs,
    write_local_cases,
)
from ecom_review_intelligence.preflight import DEFAULT_TIMEOUT_SECONDS as PREFLIGHT_TIMEOUT_SECONDS
from ecom_review_intelligence.preflight import (
    METADATA_SOURCE_URL,
    REVIEW_SOURCE_URL,
    PreflightResult,
    collect_preflight,
    write_preflight_outputs,
)

Collector = Callable[..., PreflightResult]
Writer = Callable[[Path, PreflightResult], tuple[Path, Path]]
DownloadProber = Callable[..., RemoteObject]
Downloader = Callable[..., DownloadResult]
Registrar = Callable[..., DownloadResult]
DownloadWriter = Callable[..., tuple[Path, Path]]
Clock = Callable[[], str]
Auditor = Callable[..., Phase2Audit]
AuditWriter = Callable[[Path, Phase2Audit], tuple[Path, Path]]
ScopeWriter = Callable[[Path, Phase2Audit, str], tuple[Path, Path]]
RevisionResolver = Callable[[Path], str]
ContractBuilder = Callable[..., EvidenceContract]
ContractWriter = Callable[[Path, EvidenceContract], tuple[Path, Path]]
LocalStoreBuilder = Callable[..., LocalStoreBuild]
LocalStoreWriter = Callable[..., tuple[Path, Path]]
CaseGenerator = Callable[..., tuple[FixedCase, ...]]
CaseWriter = Callable[..., Path]
Evaluator = Callable[..., EvaluationResult]
EvaluationWriter = Callable[..., tuple[Path, Path]]
DOWNLOAD_SOURCES = (
    ("reviews", REVIEW_SOURCE_URL, "Appliances.jsonl.gz"),
    ("metadata", METADATA_SOURCE_URL, "meta_Appliances.jsonl.gz"),
)


def main(
    argv: Sequence[str] | None = None,
    *,
    collector: Collector = collect_preflight,
    writer: Writer = write_preflight_outputs,
) -> int:
    """Run a no-download preflight and return a shell-friendly exit code."""

    parser = argparse.ArgumentParser(
        description="Check local prerequisites and official sources without downloading data."
    )
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument(
        "--timeout-seconds",
        type=_positive_float,
        default=PREFLIGHT_TIMEOUT_SECONDS,
        help="Per-source HTTP probe timeout in seconds.",
    )
    args = parser.parse_args(argv)
    project_root = args.project_root.resolve()
    result = collector(project_root, timeout_seconds=args.timeout_seconds)
    manifest_path, report_path = writer(project_root, result)
    print(f"Preflight state: {result.state}")
    print(f"Manifest: {manifest_path}")
    print(f"Report: {report_path}")
    return 0 if result.state == "PASSED" else 2


def download_main(
    argv: Sequence[str] | None = None,
    *,
    prober: DownloadProber = probe_remote,
    downloader: Downloader = download_remote,
    writer: DownloadWriter = write_download_outputs,
    clock: Clock = lambda: datetime.now(UTC).replace(microsecond=0).isoformat(),
) -> int:
    """Download and register the two approved raw source files."""

    parser = argparse.ArgumentParser(
        description="Download and verify the approved local Amazon Appliances source files."
    )
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument(
        "--timeout-seconds",
        type=_positive_float,
        default=DOWNLOAD_TIMEOUT_SECONDS,
        help="Per-source HTTP timeout in seconds.",
    )
    parser.add_argument(
        "--chunk-size",
        type=_positive_int,
        default=DEFAULT_CHUNK_SIZE,
        help="Transfer chunk size in bytes.",
    )
    args = parser.parse_args(argv)
    project_root = args.project_root.resolve()
    raw_root = project_root / "data" / "raw"
    raw_root.mkdir(parents=True, exist_ok=True)

    try:
        results = tuple(
            downloader(
                source_name,
                prober(url, timeout_seconds=args.timeout_seconds),
                raw_root / filename,
                timeout_seconds=args.timeout_seconds,
                chunk_size=args.chunk_size,
            )
            for source_name, url, filename in DOWNLOAD_SOURCES
        )
        manifest_path, report_path = writer(project_root, results, observed_at=clock())
    except (DownloadError, OSError) as error:
        print(f"Download blocked: {error}", file=sys.stderr)
        return 2

    print("Download state: PASSED")
    print(f"Manifest: {manifest_path}")
    print(f"Report: {report_path}")
    return 0


def register_main(
    argv: Sequence[str] | None = None,
    *,
    prober: DownloadProber = probe_remote,
    registrar: Registrar = register_existing,
    writer: DownloadWriter = write_download_outputs,
    clock: Clock = lambda: datetime.now(UTC).replace(microsecond=0).isoformat(),
) -> int:
    """Register the two verified user-downloaded source files without transferring bytes."""

    parser = argparse.ArgumentParser(
        description="Register existing local Amazon Appliances source files without downloading."
    )
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument(
        "--timeout-seconds",
        type=_positive_float,
        default=DOWNLOAD_TIMEOUT_SECONDS,
        help="Per-source HTTP identity-probe timeout in seconds.",
    )
    args = parser.parse_args(argv)
    project_root = args.project_root.resolve()
    raw_root = project_root / "data" / "raw"

    try:
        results = tuple(
            registrar(
                source_name,
                prober(url, timeout_seconds=args.timeout_seconds),
                raw_root / filename,
            )
            for source_name, url, filename in DOWNLOAD_SOURCES
        )
        manifest_path, report_path = writer(project_root, results, observed_at=clock())
    except (DownloadError, OSError) as error:
        print(f"Registration blocked: {error}", file=sys.stderr)
        return 2

    print("Registration state: PASSED")
    print(f"Manifest: {manifest_path}")
    print(f"Report: {report_path}")
    return 0


def audit_main(
    argv: Sequence[str] | None = None,
    *,
    auditor: Auditor = audit_phase2,
    writer: AuditWriter = write_phase2_outputs,
    scope_writer: ScopeWriter = write_scope_decision,
    revision_resolver: RevisionResolver = lambda project_root: resolve_git_revision(project_root),
) -> int:
    """Audit the frozen P1 snapshot and freeze one eligible P2 scope."""

    parser = argparse.ArgumentParser(
        description="Stream-audit the frozen local review snapshot without exporting raw records."
    )
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument(
        "--selected-category",
        type=str,
        default=None,
        help="Eligible category label to freeze; defaults to the P2 recommendation.",
    )
    args = parser.parse_args(argv)
    project_root = args.project_root.resolve()

    try:
        audit = auditor(project_root, git_revision=revision_resolver(project_root))
        audit_path, report_path = writer(project_root, audit)
        if audit.recommended_category is None:
            print("P2 blocked: no category met the frozen eligibility rule.", file=sys.stderr)
            print(f"Audit: {audit_path}")
            print(f"Report: {report_path}")
            return 2
        selected_category = args.selected_category or audit.recommended_category
        scope_manifest_path, scope_report_path = scope_writer(project_root, audit, selected_category)
    except (AuditError, OSError) as error:
        print(f"P2 audit blocked: {error}", file=sys.stderr)
        return 2

    print("P2 state: PASSED")
    print(f"Audit: {audit_path}")
    print(f"Report: {report_path}")
    print(f"Scope manifest: {scope_manifest_path}")
    print(f"Scope decision: {scope_report_path}")
    return 0


def contract_main(
    argv: Sequence[str] | None = None,
    *,
    builder: ContractBuilder = build_evidence_contract,
    writer: ContractWriter = write_contract_outputs,
    revision_resolver: RevisionResolver = lambda project_root: resolve_git_revision(project_root),
) -> int:
    """Freeze the P3 local evidence contract from the selected P2 scope."""

    parser = argparse.ArgumentParser(
        description="Build the local Ice Makers evidence contract from frozen P1/P2 lineage."
    )
    parser.add_argument("--project-root", type=Path, required=True)
    args = parser.parse_args(argv)
    project_root = args.project_root.resolve()

    try:
        contract = builder(project_root, git_revision=revision_resolver(project_root))
        policy_path, decision_path = writer(project_root, contract)
    except (ContractError, OSError) as error:
        print(f"P3 contract blocked: {error}", file=sys.stderr)
        return 2

    print("P3 state: PASSED")
    print(f"Policy: {policy_path}")
    print(f"Decision: {decision_path}")
    return 0


def p4_build_main(
    argv: Sequence[str] | None = None,
    *,
    builder: LocalStoreBuilder = build_local_store,
    writer: LocalStoreWriter = write_phase4_outputs,
    revision_resolver: RevisionResolver = lambda project_root: resolve_git_revision(project_root),
    clock: Clock = lambda: datetime.now(UTC).replace(microsecond=0).isoformat(),
) -> int:
    """Build the ignored local P4 evidence store from frozen P1/P2/P3 lineage."""

    parser = argparse.ArgumentParser(
        description="Materialize the frozen Ice Makers evidence store locally."
    )
    parser.add_argument("--project-root", type=Path, required=True)
    args = parser.parse_args(argv)
    project_root = args.project_root.resolve()

    try:
        result = builder(project_root, git_revision=revision_resolver(project_root))
        summary_path, report_path = writer(project_root, result, verified_at=clock())
    except (EngineError, OSError) as error:
        print(f"P4 local-store build blocked: {error}", file=sys.stderr)
        return 2

    print("P4 state: PASSED")
    print(f"Local store: {result.store_path}")
    print(f"Products: {result.product_count}")
    print(f"Reviews: {result.review_count}")
    print(f"Summary: {summary_path}")
    print(f"Report: {report_path}")
    return 0


def p5_evaluation_main(
    argv: Sequence[str] | None = None,
    *,
    case_generator: CaseGenerator = generate_fixed_cases,
    case_writer: CaseWriter = write_local_cases,
    evaluator: Evaluator = evaluate_fixed_cases,
    output_writer: EvaluationWriter = write_evaluation_outputs,
    revision_resolver: RevisionResolver = lambda project_root: resolve_git_revision(project_root),
    clock: Clock = lambda: datetime.now(UTC).replace(microsecond=0).isoformat(),
) -> int:
    """Freeze local P5 silver cases and evaluate lexical against SQLite FTS5/BM25."""

    parser = argparse.ArgumentParser(
        description="Run local-only fixed-case retrieval evaluation for the frozen Ice Makers store."
    )
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--limit", type=_positive_int, default=5)
    args = parser.parse_args(argv)
    project_root = args.project_root.resolve()
    store_path = project_root / "data" / "processed" / "ice_makers_evidence.sqlite"
    revision = revision_resolver(project_root)
    evaluated_at = clock()

    try:
        cases = case_generator(store_path, frozen_at=evaluated_at, limit=args.limit)
        cases_path = case_writer(project_root, cases, generated_by_git_revision=revision)
        result = evaluator(store_path, cases, limit=args.limit)
        summary_path, report_path = output_writer(
            project_root,
            result,
            git_revision=revision,
            evaluated_at=evaluated_at,
            limit=args.limit,
        )
    except (EvaluationError, OSError) as error:
        print(f"P5 retrieval evaluation blocked: {error}", file=sys.stderr)
        return 2

    state = "PASSED" if result.quality_gates_passed else "FAILED"
    print(f"P5 state: {state}")
    print(f"Local cases: {cases_path}")
    print(f"Summary: {summary_path}")
    print(f"Report: {report_path}")
    return 0 if result.quality_gates_passed else 2


def resolve_git_revision(project_root: Path) -> str:
    """Return the checked-out revision, or unknown when Git cannot provide one."""

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return "unknown"
    revision = result.stdout.strip()
    return revision if result.returncode == 0 and revision else "unknown"


def _positive_float(value: str) -> float:
    number = float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return number


def _positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return number
