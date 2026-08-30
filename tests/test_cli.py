from __future__ import annotations

from pathlib import Path

from ecom_review_intelligence.audit import CategoryCandidate, FieldProfile, Phase2Audit
from ecom_review_intelligence.cli import (
    audit_main,
    contract_main,
    download_main,
    main,
    p4_build_main,
    p5_evaluation_main,
    register_main,
)
from ecom_review_intelligence.contract import AspectCoverage, EvidenceContract
from ecom_review_intelligence.download import DownloadError, DownloadResult, RemoteObject
from ecom_review_intelligence.engine import LocalStoreBuild
from ecom_review_intelligence.evaluation import EvaluationResult, RetrievalMetrics
from ecom_review_intelligence.preflight import PreflightResult


def test_main_writes_a_passed_preflight_and_returns_zero(tmp_path: Path) -> None:
    result = PreflightResult(
        observed_at="2026-08-02T00:00:00+00:00",
        python_version="3.12.13",
        python_supported=True,
        operating_system="Windows",
        free_bytes=3 * 1024**3,
        required_free_bytes=2 * 1024**3,
        sources=(),
        state="PASSED",
        blockers=(),
    )
    calls: dict[str, object] = {}

    def collector(project_root: Path, *, timeout_seconds: float) -> PreflightResult:
        calls["root"] = project_root
        calls["timeout"] = timeout_seconds
        return result

    def writer(project_root: Path, received_result: PreflightResult) -> tuple[Path, Path]:
        calls["writer_root"] = project_root
        calls["writer_result"] = received_result
        return project_root / "manifest.json", project_root / "report.md"

    exit_code = main(
        ["--project-root", str(tmp_path), "--timeout-seconds", "12.5"],
        collector=collector,
        writer=writer,
    )

    assert exit_code == 0
    assert calls == {
        "root": tmp_path,
        "timeout": 12.5,
        "writer_root": tmp_path,
        "writer_result": result,
    }


def test_main_returns_two_for_a_blocked_preflight(tmp_path: Path) -> None:
    result = PreflightResult(
        observed_at="2026-08-02T00:00:00+00:00",
        python_version="3.10.0",
        python_supported=False,
        operating_system="Windows",
        free_bytes=0,
        required_free_bytes=2 * 1024**3,
        sources=(),
        state="BLOCKED",
        blockers=("Python version is lower than 3.11.",),
    )

    assert (
        main(
            ["--project-root", str(tmp_path)],
            collector=lambda project_root, timeout_seconds: result,
            writer=lambda project_root, received_result: (
                project_root / "manifest.json",
                project_root / "report.md",
            ),
        )
        == 2
    )


def test_download_main_registers_both_sources_with_injected_dependencies(tmp_path: Path) -> None:
    remote = RemoteObject(
        url="https://example.test/source.gz",
        total_bytes=10,
        content_type="application/gzip",
        accept_ranges=True,
        etag='"stable"',
        last_modified="Thu, 16 Jan 2025 22:41:53 GMT",
    )
    calls: dict[str, object] = {"probed_urls": [], "downloaded": []}

    def prober(url: str, *, timeout_seconds: float) -> RemoteObject:
        calls["probed_urls"].append((url, timeout_seconds))
        return remote

    def downloader(
        source_name: str,
        received_remote: RemoteObject,
        final_path: Path,
        *,
        timeout_seconds: float,
        chunk_size: int,
    ) -> DownloadResult:
        calls["downloaded"].append(
            (source_name, received_remote, final_path, timeout_seconds, chunk_size)
        )
        return DownloadResult(
            source_name=source_name,
            url=received_remote.url,
            final_path=final_path,
            total_bytes=received_remote.total_bytes,
            sha256=source_name * 8,
            etag=received_remote.etag,
            last_modified=received_remote.last_modified,
            resumed_from_bytes=0,
        )

    def writer(
        project_root: Path,
        results: tuple[DownloadResult, ...],
        *,
        observed_at: str,
    ) -> tuple[Path, Path]:
        calls["writer"] = (project_root, results, observed_at)
        return project_root / "manifest.json", project_root / "report.md"

    exit_code = download_main(
        ["--project-root", str(tmp_path), "--timeout-seconds", "12.5", "--chunk-size", "2048"],
        prober=prober,
        downloader=downloader,
        writer=writer,
        clock=lambda: "2026-08-02T00:00:00+00:00",
    )

    assert exit_code == 0
    assert [entry[0] for entry in calls["downloaded"]] == ["reviews", "metadata"]
    assert all(entry[2].parent == tmp_path / "data" / "raw" for entry in calls["downloaded"])
    assert calls["writer"][0] == tmp_path
    assert len(calls["writer"][1]) == 2
    assert calls["writer"][2] == "2026-08-02T00:00:00+00:00"


def test_download_main_returns_two_without_writing_when_download_fails(tmp_path: Path) -> None:
    remote = RemoteObject(
        url="https://example.test/source.gz",
        total_bytes=10,
        content_type="application/gzip",
        accept_ranges=True,
        etag='"stable"',
        last_modified="Thu, 16 Jan 2025 22:41:53 GMT",
    )
    writer_calls: list[object] = []

    def failing_downloader(
        source_name: str,
        received_remote: RemoteObject,
        final_path: Path,
        *,
        timeout_seconds: float,
        chunk_size: int,
    ) -> DownloadResult:
        raise DownloadError("simulated download failure")

    exit_code = download_main(
        ["--project-root", str(tmp_path)],
        prober=lambda url, timeout_seconds: remote,
        downloader=failing_downloader,
        writer=lambda project_root, results, observed_at: writer_calls.append(
            (project_root, results, observed_at)
        ),
    )

    assert exit_code == 2
    assert writer_calls == []


def test_register_main_registers_existing_files_without_using_downloader(tmp_path: Path) -> None:
    remote = RemoteObject(
        url="https://example.test/source.gz",
        total_bytes=10,
        content_type="application/gzip",
        accept_ranges=True,
        etag='"stable"',
        last_modified="Thu, 16 Jan 2025 22:41:53 GMT",
    )
    calls: dict[str, object] = {"probed_urls": [], "registered": []}

    def prober(url: str, *, timeout_seconds: float) -> RemoteObject:
        calls["probed_urls"].append((url, timeout_seconds))
        return remote

    def registrar(
        source_name: str,
        received_remote: RemoteObject,
        final_path: Path,
    ) -> DownloadResult:
        calls["registered"].append((source_name, received_remote, final_path))
        return DownloadResult(
            source_name=source_name,
            url=received_remote.url,
            final_path=final_path,
            total_bytes=received_remote.total_bytes,
            sha256=source_name * 8,
            etag=received_remote.etag,
            last_modified=received_remote.last_modified,
            resumed_from_bytes=received_remote.total_bytes,
            acquisition_method="manual_direct_download",
        )

    def writer(
        project_root: Path,
        results: tuple[DownloadResult, ...],
        *,
        observed_at: str,
    ) -> tuple[Path, Path]:
        calls["writer"] = (project_root, results, observed_at)
        return project_root / "manifest.json", project_root / "report.md"

    exit_code = register_main(
        ["--project-root", str(tmp_path), "--timeout-seconds", "12.5"],
        prober=prober,
        registrar=registrar,
        writer=writer,
        clock=lambda: "2026-08-03T00:00:00+00:00",
    )

    assert exit_code == 0
    assert [entry[0] for entry in calls["registered"]] == ["reviews", "metadata"]
    assert all(entry[2].parent == tmp_path / "data" / "raw" for entry in calls["registered"])
    assert len(calls["writer"][1]) == 2
    assert calls["writer"][2] == "2026-08-03T00:00:00+00:00"


def test_audit_main_writes_audit_and_selected_scope_with_injected_dependencies(
    tmp_path: Path,
) -> None:
    profile = FieldProfile(
        total_rows=1,
        malformed_rows=0,
        field_presence={"parent_asin": 1},
        non_empty={"parent_asin": 1},
        valid_rating_rows=1,
        timestamp_rows=1,
        timestamp_min=1,
        timestamp_max=1,
        valid_verified_purchase_rows=1,
        valid_helpful_vote_rows=1,
    )
    audit = Phase2Audit(
        snapshot_id="a" * 64,
        observed_at="2026-08-03T00:00:00+00:00",
        git_revision="test-revision",
        parameters={},
        metadata_profile=profile,
        review_profile=profile,
        metadata_parent_conflicts=0,
        inputs={
            "reviews": {
                "filename": "Appliances.jsonl.gz",
                "total_bytes": 10,
                "sha256": "c" * 64,
            },
            "metadata": {
                "filename": "meta_Appliances.jsonl.gz",
                "total_bytes": 11,
                "sha256": "d" * 64,
            },
        },
        candidates=(
            CategoryCandidate(
                category="Quiet Appliances",
                metadata_products=10,
                joined_reviews=500,
                review_to_metadata_coverage=1.0,
                text_coverage=1.0,
                valid_rating_coverage=1.0,
                timestamp_coverage=1.0,
                eligible=True,
                recommendation_score=1.0,
            ),
        ),
        recommended_category="Quiet Appliances",
        audit_sha256="b" * 64,
    )
    calls: dict[str, object] = {}

    def auditor(project_root: Path, *, git_revision: str) -> Phase2Audit:
        calls["auditor"] = (project_root, git_revision)
        return audit

    def writer(project_root: Path, received_audit: Phase2Audit) -> tuple[Path, Path]:
        calls["writer"] = (project_root, received_audit)
        return project_root / "audit.json", project_root / "audit.md"

    def scope_writer(
        project_root: Path,
        received_audit: Phase2Audit,
        selected_category: str,
    ) -> tuple[Path, Path]:
        calls["scope_writer"] = (project_root, received_audit, selected_category)
        return project_root / "scope.json", project_root / "scope.md"

    exit_code = audit_main(
        ["--project-root", str(tmp_path)],
        auditor=auditor,
        writer=writer,
        scope_writer=scope_writer,
        revision_resolver=lambda project_root: "test-revision",
    )

    assert exit_code == 0
    assert calls["auditor"] == (tmp_path, "test-revision")
    assert calls["writer"] == (tmp_path, audit)
    assert calls["scope_writer"] == (tmp_path, audit, "Quiet Appliances")


def test_contract_main_writes_a_contract_with_injected_dependencies(tmp_path: Path) -> None:
    contract = EvidenceContract(
        dataset_snapshot_id="a" * 64,
        audit_sha256="b" * 64,
        selected_category="Ice Makers",
        git_revision="test-revision",
        observed_at="2026-08-03T00:00:00+00:00",
        selected_review_count=100,
        selected_product_count=10,
        review_count_quantiles={"q10": 1},
        aspects=(AspectCoverage("noise", 100, 1.0, True),),
        enabled_aspects=("noise",),
        aspect_terms={"noise": ("noise",)},
        strength_rules={"STRONG": {"min_evidence": 5}},
        polarity_terms={"supporting": ("good",), "opposing": ("bad",)},
        label_policy={"gold": "prohibited"},
        field_mapping={"review_text": "reviews.text"},
        contract_sha256="c" * 64,
    )
    calls: dict[str, object] = {}

    def builder(project_root: Path, *, git_revision: str) -> EvidenceContract:
        calls["builder"] = (project_root, git_revision)
        return contract

    def writer(project_root: Path, received_contract: EvidenceContract) -> tuple[Path, Path]:
        calls["writer"] = (project_root, received_contract)
        return project_root / "policy.json", project_root / "decision.md"

    exit_code = contract_main(
        ["--project-root", str(tmp_path)],
        builder=builder,
        writer=writer,
        revision_resolver=lambda project_root: "test-revision",
    )

    assert exit_code == 0
    assert calls["builder"] == (tmp_path, "test-revision")
    assert calls["writer"] == (tmp_path, contract)


def test_p4_build_main_returns_zero_with_an_injected_builder(tmp_path: Path) -> None:
    result = LocalStoreBuild(
        store_path=tmp_path / "data" / "processed" / "ice_makers_evidence.sqlite",
        dataset_snapshot_id="a" * 64,
        contract_sha256="b" * 64,
        product_count=2,
        review_count=3,
        git_revision="test-revision",
        content_sha256="c" * 64,
    )
    calls: dict[str, object] = {}

    def builder(project_root: Path, *, git_revision: str) -> LocalStoreBuild:
        calls["builder"] = (project_root, git_revision)
        return result

    def writer(
        project_root: Path, received_result: LocalStoreBuild, *, verified_at: str
    ) -> tuple[Path, Path]:
        calls["writer"] = (project_root, received_result, verified_at)
        return project_root / "summary.json", project_root / "report.md"

    exit_code = p4_build_main(
        ["--project-root", str(tmp_path)],
        builder=builder,
        writer=writer,
        revision_resolver=lambda project_root: "test-revision",
        clock=lambda: "2026-08-03T00:00:00+00:00",
    )

    assert exit_code == 0
    assert calls["builder"] == (tmp_path, "test-revision")
    assert calls["writer"] == (tmp_path, result, "2026-08-03T00:00:00+00:00")


def test_p5_evaluation_main_runs_injected_fixed_case_workflow(tmp_path: Path) -> None:
    metric = RetrievalMetrics(
        evidence_case_count=1,
        expected_evidence_count=1,
        retrieved_evidence_count=1,
        recalled_evidence_count=1,
        recall_at_k=1.0,
        case_hit_rate=1.0,
        citation_checks=1,
        citation_integrity=1.0,
        product_join_errors=0,
        no_answer_false_positives=0,
        mean_latency_ms=1.0,
        p95_latency_ms=1.0,
    )
    result = EvaluationResult(
        dataset_snapshot_id="a" * 64,
        contract_sha256="b" * 64,
        store_build_revision="store-revision",
        case_corpus_sha256="c" * 64,
        case_count=1,
        workflow_counts={"diagnosis": 1},
        label_counts={"silver": 1},
        metrics={"lexical": metric, "fts5_bm25": metric},
        quality_gates_passed=True,
    )
    calls: dict[str, object] = {}
    fixed_cases = (object(),)

    def generator(store_path: Path, *, frozen_at: str, limit: int) -> tuple[object, ...]:
        calls["generator"] = (store_path, frozen_at, limit)
        return fixed_cases

    def case_writer(
        project_root: Path, cases: tuple[object, ...], *, generated_by_git_revision: str
    ) -> Path:
        calls["case_writer"] = (project_root, cases, generated_by_git_revision)
        return project_root / "cases.json"

    def evaluator(store_path: Path, cases: tuple[object, ...], *, limit: int) -> EvaluationResult:
        calls["evaluator"] = (store_path, cases, limit)
        return result

    def output_writer(
        project_root: Path,
        received_result: EvaluationResult,
        *,
        git_revision: str,
        evaluated_at: str,
        limit: int,
    ) -> tuple[Path, Path]:
        calls["output_writer"] = (
            project_root,
            received_result,
            git_revision,
            evaluated_at,
            limit,
        )
        return project_root / "summary.json", project_root / "report.md"

    exit_code = p5_evaluation_main(
        ["--project-root", str(tmp_path), "--limit", "3"],
        case_generator=generator,
        case_writer=case_writer,
        evaluator=evaluator,
        output_writer=output_writer,
        revision_resolver=lambda project_root: "test-revision",
        clock=lambda: "2026-08-03T00:00:00+00:00",
    )

    expected_store = tmp_path / "data" / "processed" / "ice_makers_evidence.sqlite"
    assert exit_code == 0
    assert calls["generator"] == (expected_store, "2026-08-03T00:00:00+00:00", 3)
    assert calls["case_writer"] == (tmp_path, fixed_cases, "test-revision")
    assert calls["evaluator"] == (expected_store, fixed_cases, 3)
