"""Fail-closed final audit for the P6 Agent completion gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO

from ecom_review_intelligence.agent_cli import _read_config
from ecom_review_intelligence.agent_evaluation import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_STORE_PATH,
    LOCAL_CORPUS_PATH,
    LOCAL_DETAILS_PATH,
    PUBLIC_REPORT_PATH,
    PUBLIC_SUMMARY_PATH,
    aggregate_results,
    assert_public_evaluation_safe,
    code_identity_paths,
    git_code_identity,
    normalized_source_bytes,
    render_report,
    validate_case_corpus,
)
from ecom_review_intelligence.agent_live_evaluation import (
    LIVE_LOCAL_DETAILS_PATH,
    LIVE_PUBLIC_REPORT_PATH,
    LIVE_PUBLIC_SUMMARY_PATH,
    aggregate_live_results,
    prepare_live_selection,
    render_live_report,
    validate_live_public_summary,
)
from ecom_review_intelligence.engine import compute_evidence_id, compute_store_content_sha256
from ecom_review_intelligence.model_gateway import ReplayModelGateway


class CompletionAuditError(RuntimeError):
    """Raised when P6 lacks exact evidence required for final closure."""


_P1_PATH = Path("artifacts/manifests/phase-1-download.json")
_P2_PATH = Path("artifacts/manifests/phase-2-scope.json")
_P2_AUDIT_PATH = Path("artifacts/audits/phase-2-audit.json")
_P3_PATH = Path("artifacts/contracts/phase-3-evidence-policy.json")
_P4_PATH = Path("artifacts/store-summaries/phase-4-build.json")
_REPLAY_PATH = Path("tests/fixtures/p6_model_replay.json")
_PROHIBITED_TRACKED_PREFIXES = (
    "data/raw/",
    "data/processed/",
    "artifacts/evals/",
    "简历/",
    "规划/",
)


def validate_lineage_chain(
    *,
    p1: Mapping[str, Any],
    scope: Mapping[str, Any],
    contract: Mapping[str, Any],
    store: Mapping[str, Any],
    config: Mapping[str, Any],
    offline: Mapping[str, Any],
    live: Mapping[str, Any],
    selection: Mapping[str, Any],
) -> None:
    if any(document.get("state") != "PASSED" for document in (p1, scope, contract, store)):
        raise CompletionAuditError("An upstream phase is not passed.")
    snapshot = p1.get("dataset_snapshot_id")
    contract_hash = contract.get("contract_sha256")
    config_hash = config.get("config_sha256")
    corpus_hash = offline.get("corpus_sha256")
    selection_hash = selection.get("selection_sha256")
    if not all(
        isinstance(value, str) and len(value) == 64
        for value in (snapshot, contract_hash, config_hash, corpus_hash, selection_hash)
    ):
        raise CompletionAuditError("P6 lineage values are invalid.")
    sources = p1.get("sources")
    if not isinstance(sources, list):
        raise CompletionAuditError("P1 source registration is invalid.")
    source_hashes = {
        item.get("source_name"): item.get("sha256")
        for item in sources
        if isinstance(item, Mapping)
    }
    if set(source_hashes) != {"reviews", "metadata"} or any(
        not isinstance(value, str) or len(value) != 64 for value in source_hashes.values()
    ):
        raise CompletionAuditError("P1 source registration is invalid.")
    recomputed_snapshot = hashlib.sha256(
        (
            f"reviews:{source_hashes['reviews']}\n"
            f"metadata:{source_hashes['metadata']}\n"
        ).encode()
    ).hexdigest()
    if snapshot != recomputed_snapshot:
        raise CompletionAuditError("P1 snapshot hash is invalid.")
    if any(
        document.get("dataset_snapshot_id") != snapshot
        for document in (scope, contract, store, config, offline, live)
    ):
        raise CompletionAuditError("Dataset snapshot lineage does not match.")
    if any(
        document.get("contract_sha256") != contract_hash
        for document in (store, config, offline, live)
    ):
        raise CompletionAuditError("Evidence contract lineage does not match.")
    if any(
        document.get("config_sha256") != config_hash for document in (offline, live)
    ):
        raise CompletionAuditError("P6 configuration lineage does not match.")
    if (
        live.get("corpus_sha256") != corpus_hash
        or selection.get("source_corpus_sha256") != corpus_hash
        or live.get("selection_sha256") != selection_hash
    ):
        raise CompletionAuditError("Evaluation selection lineage does not match.")
    if (
        offline.get("gates", {}).get("all_blocking_gates_passed") is not True
        or live.get("gates", {}).get("all_blocking_gates_passed") is not True
    ):
        raise CompletionAuditError("A P6 blocking gate has not passed.")


def validate_scope_chain(
    *, audit: Mapping[str, Any], scope: Mapping[str, Any], contract: Mapping[str, Any]
) -> None:
    audit_copy = dict(audit)
    recorded_hash = audit_copy.pop("audit_sha256", None)
    if (
        audit.get("state") != "PASSED"
        or recorded_hash != _canonical_hash(audit_copy)
        or scope.get("audit_sha256") != recorded_hash
        or contract.get("audit_sha256") != recorded_hash
        or scope.get("dataset_snapshot_id") != audit.get("dataset_snapshot_id")
        or contract.get("dataset_snapshot_id") != audit.get("dataset_snapshot_id")
        or scope.get("selected_category") != contract.get("selected_category")
    ):
        raise CompletionAuditError("The P2/P3 scope lineage is invalid.")
    selected = scope.get("selected_category")
    candidates = audit.get("candidates")
    if not isinstance(candidates, list):
        raise CompletionAuditError("The P2 audit candidates are invalid.")
    matching = [
        item
        for item in candidates
        if isinstance(item, Mapping) and item.get("category") == selected
    ]
    if len(matching) != 1 or scope.get("selection_metrics") != matching[0]:
        raise CompletionAuditError("The selected P2 category is not bound to its audit row.")


def validate_tracked_paths(paths: Sequence[str]) -> None:
    for original in paths:
        normalized = original.replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        folded = normalized.casefold()
        if (
            folded == ".env"
            or (folded.startswith(".env.") and folded != ".env.example")
            or any(
                folded == prefix.casefold() or folded.startswith(prefix.casefold())
                for prefix in _PROHIBITED_TRACKED_PREFIXES
            )
            or "checkpoint" in Path(normalized).name.casefold()
            or "/checkpoints/" in f"/{folded}/"
        ):
            raise CompletionAuditError("A private or runtime path is tracked.")


def validate_tracked_content(root: Path, paths: Sequence[str]) -> None:
    patterns = (
        re.compile(rb"(?i)sk-[A-Za-z0-9_-]{20,}"),
        re.compile(rb"(?i)Bearer\s+[A-Za-z0-9._-]{20,}"),
        re.compile(
            rb"(?im)^[ \t]*(?:export[ \t]+|set[ \t]+)?"
            rb"DEEPSEEK_API_KEY[ \t]*[:=][ \t]*[^\s$%<{][^\r\n]{5,}"
        ),
    )
    for relative in paths:
        path = Path(root) / relative
        if not path.is_file():
            continue
        content = path.read_bytes()
        if any(pattern.search(content) for pattern in patterns):
            raise CompletionAuditError("A tracked file contains a secret-like value.")


def validate_clean_tracked_worktree(root: Path) -> None:
    status = subprocess.run(
        ["git", "-C", str(Path(root).resolve()), "status", "--porcelain", "--untracked-files=no"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    if status.strip():
        raise CompletionAuditError("The tracked worktree is not clean.")


def audit_phase6_completion(project_root: Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    p1 = _read_json(root / _P1_PATH)
    audit = _read_json(root / _P2_AUDIT_PATH)
    scope = _read_json(root / _P2_PATH)
    contract = _read_json(root / _P3_PATH)
    store_summary = _read_json(root / _P4_PATH)
    config = _read_config(root / DEFAULT_CONFIG_PATH)
    offline = _read_json(root / PUBLIC_SUMMARY_PATH)
    live = _read_json(root / LIVE_PUBLIC_SUMMARY_PATH)
    corpus = validate_case_corpus(_read_json(root / LOCAL_CORPUS_PATH))
    selected, selection = prepare_live_selection(corpus, offline, config)

    identity = git_code_identity(root)
    validate_clean_tracked_worktree(root)
    _validate_registered_sources(root, p1)
    validate_scope_chain(audit=audit, scope=scope, contract=contract)

    assert_public_evaluation_safe(offline)
    validate_live_public_summary(live)
    offline_details = _read_details(root / LOCAL_DETAILS_PATH, "p6-offline-details-v1")
    live_details = _read_details(root / LIVE_LOCAL_DETAILS_PATH, "p6-live-details-v1")
    _validate_source_identity(root, offline, identity)
    _validate_source_identity(root, live, identity)
    recomputed_offline = aggregate_results(
        corpus, offline_details, git_revision=offline["git_revision"]
    )
    recomputed_offline.update(
        source_sha256=identity["source_sha256"], git_dirty=False
    )
    if recomputed_offline != offline:
        raise CompletionAuditError("The offline summary does not match private results.")
    recomputed_live = aggregate_live_results(
        selection,
        live_details,
        selected_cases=selected,
        config=config,
        identity={
            "git_revision": live["git_revision"],
            "source_sha256": identity["source_sha256"],
            "git_dirty": False,
        },
    )
    if recomputed_live != live:
        raise CompletionAuditError("The live summary does not match private results.")
    validate_lineage_chain(
        p1=p1,
        scope=scope,
        contract=contract,
        store=store_summary,
        config=config,
        offline=offline,
        live=live,
        selection=selection,
    )
    contract_copy = dict(contract)
    recorded_contract_hash = contract_copy.pop("contract_sha256", None)
    if recorded_contract_hash != _canonical_hash(contract_copy):
        raise CompletionAuditError("The P3 contract hash is invalid.")
    _validate_store(root / DEFAULT_STORE_PATH, config, store_summary)
    if (root / LIVE_PUBLIC_REPORT_PATH).read_text(encoding="utf-8") != render_live_report(live):
        raise CompletionAuditError("The live report does not match its summary.")
    offline_report = (root / PUBLIC_REPORT_PATH).read_text(encoding="utf-8")
    assert_public_evaluation_safe(
        offline_report,
        allow_lineage_hashes=True,
    )
    if offline_report != render_report(offline):
        raise CompletionAuditError("The offline report does not match its summary.")
    ReplayModelGateway.from_path(root / _REPLAY_PATH)
    tracked = _git_lines(root, ["ls-files", "-z"], separator="\0")
    validate_tracked_paths(tracked)
    validate_tracked_content(root, tracked)
    _require_clean_tracked_artifacts(
        root,
        (
            PUBLIC_SUMMARY_PATH,
            PUBLIC_REPORT_PATH,
            LIVE_PUBLIC_SUMMARY_PATH,
            LIVE_PUBLIC_REPORT_PATH,
        ),
    )
    _require_private_ignored(
        root,
        (
            LOCAL_CORPUS_PATH,
            LOCAL_DETAILS_PATH,
            LIVE_LOCAL_DETAILS_PATH,
            DEFAULT_STORE_PATH,
            Path("data/raw") / p1["sources"][0]["filename"],
            Path("data/raw") / p1["sources"][1]["filename"],
        ),
    )
    if (
        identity["git_dirty"]
        or live.get("git_dirty") is not False
        or live.get("source_sha256") != identity["source_sha256"]
    ):
        raise CompletionAuditError("Live evidence does not match clean current source.")
    revision = live.get("git_revision")
    if not isinstance(revision, str) or subprocess.run(
        ["git", "-C", str(root), "cat-file", "-e", f"{revision}^{{commit}}"],
        capture_output=True,
        check=False,
    ).returncode != 0:
        raise CompletionAuditError("The evaluated Git revision is unavailable.")
    return {
        "schema_version": "p6-completion-audit-v1",
        "status": "READY_TO_CLOSE_P6",
        "offline_case_count": offline["case_count"],
        "live_case_count": live["case_count"],
        "live_run_count": live["run_count"],
    }


def _validate_registered_sources(root: Path, p1: Mapping[str, Any]) -> None:
    sources = p1.get("sources")
    if not isinstance(sources, list) or len(sources) != 2:
        raise CompletionAuditError("P1 sources are invalid.")
    for source in sources:
        if not isinstance(source, Mapping):
            raise CompletionAuditError("P1 sources are invalid.")
        path = root / "data/raw" / str(source.get("filename", ""))
        if not path.is_file() or path.stat().st_size != source.get("total_bytes"):
            raise CompletionAuditError("A registered P1 source does not match its manifest.")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != source.get("sha256"):
            raise CompletionAuditError("A registered P1 source does not match its manifest.")


def _read_details(path: Path, schema_version: str) -> list[dict[str, Any]]:
    document = _read_json(path)
    if set(document) != {"schema_version", "results"} or document.get(
        "schema_version"
    ) != schema_version:
        raise CompletionAuditError("An evaluation details schema is invalid.")
    results = document.get("results")
    if not isinstance(results, list) or not all(isinstance(item, dict) for item in results):
        raise CompletionAuditError("Evaluation details are invalid.")
    return results


def _validate_source_identity(
    root: Path, summary: Mapping[str, Any], current: Mapping[str, Any]
) -> None:
    revision = summary.get("git_revision")
    if (
        summary.get("git_dirty") is not False
        or summary.get("source_sha256") != current.get("source_sha256")
        or not isinstance(revision, str)
        or _source_hash_at_revision(root, revision) != summary.get("source_sha256")
    ):
        raise CompletionAuditError("Evaluation source identity is stale or invalid.")


def _source_hash_at_revision(root: Path, revision: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise CompletionAuditError("Evaluation Git revision is invalid.")
    digest = hashlib.sha256()
    for path in code_identity_paths(root):
        relative = path.relative_to(root).as_posix()
        result = subprocess.run(
            ["git", "-C", str(root), "show", f"{revision}:{relative}"],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise CompletionAuditError("Evaluated source is unavailable from Git.")
        digest.update(
            relative.encode("utf-8")
            + b"\0"
            + normalized_source_bytes(result.stdout)
            + b"\0"
        )
    return digest.hexdigest()


def _require_private_ignored(root: Path, paths: Sequence[Path]) -> None:
    for path in paths:
        relative = Path(path).as_posix()
        if not (root / path).is_file() or subprocess.run(
            ["git", "-C", str(root), "check-ignore", "-q", "--", relative],
            check=False,
        ).returncode != 0:
            raise CompletionAuditError("A required private artifact is missing or not ignored.")


def _validate_store(
    path: Path, config: Mapping[str, Any], store_summary: Mapping[str, Any]
) -> None:
    with sqlite3.connect(path) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        metadata = dict(connection.execute("SELECT key, value FROM store_meta"))
        product_count = connection.execute("SELECT count(*) FROM products").fetchone()[0]
        review_count = connection.execute("SELECT count(*) FROM reviews").fetchone()[0]
        fts_count = connection.execute("SELECT count(*) FROM reviews_fts").fetchone()[0]
        orphan_count = connection.execute(
            "SELECT count(*) FROM reviews r LEFT JOIN products p ON p.parent_asin=r.parent_asin WHERE p.parent_asin IS NULL"
        ).fetchone()[0]
        fts_mismatch_count = connection.execute(
            "SELECT count(*) FROM reviews r LEFT JOIN reviews_fts f ON f.rowid=r.rowid WHERE f.rowid IS NULL OR f.text != r.text"
        ).fetchone()[0]
        fts_extra_count = connection.execute(
            "SELECT count(*) FROM reviews_fts f LEFT JOIN reviews r ON r.rowid=f.rowid WHERE r.rowid IS NULL"
        ).fetchone()[0]
        evidence_rows = connection.execute(
            "SELECT source_line, evidence_id FROM reviews ORDER BY source_line"
        )
        evidence_valid = all(
            evidence_id == compute_evidence_id(config["dataset_snapshot_id"], source_line)
            for source_line, evidence_id in evidence_rows
        )
    if (
        integrity != "ok"
        or metadata.get("dataset_snapshot_id") != config["dataset_snapshot_id"]
        or metadata.get("contract_sha256") != config["contract_sha256"]
        or product_count != store_summary.get("product_count")
        or review_count != store_summary.get("review_count")
        or fts_count != store_summary.get("fts_row_count")
        or not re.fullmatch(r"[0-9a-f]{64}", str(store_summary.get("content_sha256", "")))
        or compute_store_content_sha256(path) != store_summary.get("content_sha256")
        or orphan_count != 0
        or fts_mismatch_count != 0
        or fts_extra_count != 0
        or not evidence_valid
    ):
        raise CompletionAuditError("The P4 store lineage does not match P6.")


def _require_clean_tracked_artifacts(root: Path, paths: Sequence[Path]) -> None:
    for path in paths:
        relative = path.as_posix()
        tracked = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--error-unmatch", relative],
            capture_output=True,
            check=False,
        )
        clean = subprocess.run(
            ["git", "-C", str(root), "diff", "--quiet", "HEAD", "--", relative],
            check=False,
        )
        if tracked.returncode != 0 or clean.returncode != 0:
            raise CompletionAuditError("A public P6 artifact is uncommitted or dirty.")


def _canonical_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise CompletionAuditError("A completion artifact is not a JSON object.")
    return document


def _git_lines(root: Path, arguments: list[str], *, separator: str) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        check=True,
        text=True,
    )
    return [item for item in result.stdout.split(separator) if item]


class _AuditParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise ValueError("Invalid completion audit arguments.")


def audit_main(
    argv: list[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    parser = _AuditParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    output, errors = stdout or sys.stdout, stderr or sys.stderr
    try:
        args = parser.parse_args(argv)
        result = audit_phase6_completion(args.project_root)
        print(json.dumps(result, sort_keys=True), file=output)
        return 0
    except Exception:  # noqa: BLE001 - final audit must not expose paths or local payloads
        print(
            json.dumps(
                {"schema_version": "p6-completion-audit-cli-v1", "status": "FAILED"},
                sort_keys=True,
            ),
            file=errors,
        )
        return 1
