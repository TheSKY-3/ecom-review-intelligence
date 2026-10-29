"""Reproducible private P7 regression through the P6 graph and SQLite store."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from fastapi.testclient import TestClient

from ecom_review_intelligence.workbench_resources import load_local_agent_resources
from ecom_review_intelligence.workbench_service import P6BackedWorkbenchService
from ecom_review_intelligence.workbench_web import (
    assert_public_workbench_safe,
    create_workbench_app,
)

_WORKBENCH_SOURCE_PATHS = (
    "pyproject.toml",
    "scripts/run_phase7_workbench.py",
    "scripts/run_phase7_local_regression.py",
    "scripts/validate_phase7_workbench.cjs",
    "src/ecom_review_intelligence/agent_cli.py",
    "src/ecom_review_intelligence/agent_contracts.py",
    "src/ecom_review_intelligence/agent_graph.py",
    "src/ecom_review_intelligence/agent_policy.py",
    "src/ecom_review_intelligence/agent_tools.py",
    "src/ecom_review_intelligence/engine.py",
    "src/ecom_review_intelligence/model_gateway.py",
    "src/ecom_review_intelligence/workbench_cli.py",
    "src/ecom_review_intelligence/workbench_contracts.py",
    "src/ecom_review_intelligence/workbench_service.py",
    "src/ecom_review_intelligence/workbench_regression.py",
    "src/ecom_review_intelligence/workbench_resources.py",
    "src/ecom_review_intelligence/workbench_web.py",
    "src/ecom_review_intelligence/templates/workbench.html",
    "src/ecom_review_intelligence/static/workbench.css",
    "src/ecom_review_intelligence/static/workbench.js",
    "src/ecom_review_intelligence/static/workbench-theme.js",
    "tests/test_workbench_cli.py",
    "tests/test_workbench_local.py",
    "tests/test_workbench_web.py",
)


def workbench_code_identity(project_root: Path) -> dict[str, object]:
    root = Path(project_root).resolve()
    digest = hashlib.sha256()
    for relative in _WORKBENCH_SOURCE_PATHS:
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update((root / relative).read_bytes() + b"\0")
    revision = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "--", *_WORKBENCH_SOURCE_PATHS],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    return {
        "git_revision": revision,
        "source_sha256": digest.hexdigest(),
        "source_dirty": bool(dirty),
        "source_paths": list(_WORKBENCH_SOURCE_PATHS),
    }


class _SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise ValueError("Invalid regression arguments.")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeParser(allow_abbrev=False)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--workflow",
        action="append",
        choices=("diagnosis", "comparison", "triage"),
        default=[],
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/evals/phase-7-local-regression.json"),
    )
    return parser


def local_regression_main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output_stream, error_stream = stdout or sys.stdout, stderr or sys.stderr
    try:
        args = _parser().parse_args(argv)
    except (SystemExit, TypeError, ValueError):
        return _emit(error_stream, "INVALID_ARGUMENT", 2)
    root = args.project_root.resolve()
    target = args.output.resolve() if args.output.is_absolute() else (root / args.output).resolve()
    temporary = target.with_name(target.name + ".tmp")
    target.unlink(missing_ok=True)
    temporary.unlink(missing_ok=True)
    workflows = tuple(args.workflow) or ("diagnosis", "comparison", "triage")
    try:
        store, checkpoint, config, product_map = load_local_agent_resources(root)
        identity = workbench_code_identity(root)
        if identity["source_dirty"] is not False:
            raise RuntimeError("Local P7 regression requires clean source identity.")
        service = P6BackedWorkbenchService(
            store_path=store,
            checkpoint_path=checkpoint,
            config=config,
            product_ids_by_code=product_map,
        )
        client = TestClient(create_workbench_app(service=service))
        observations = []
        for workflow in workflows:
            codes = (
                ["frostline"]
                if workflow == "diagnosis"
                else ["frostline", "northstar"]
            )
            response = client.post(
                "/api/v1/runs",
                json={
                    "workflow": workflow,
                    "product_codes": codes,
                    "product_query": None,
                    "aspect": "noise",
                    "question": "Which controlled review signals should operations verify?",
                },
            )
            if response.status_code != 201:
                raise RuntimeError("Local P7 request failed.")
            payload = response.json()
            assert_public_workbench_safe(payload)
            if payload["status"] not in {"VERIFIED_RESULT", "DEGRADED_SAFE_RESULT"}:
                raise RuntimeError("Local P7 result was not safely terminal.")
            observations.append(
                {
                    "workflow": workflow,
                    "status": payload["status"],
                    "evidence_count": payload["evidence_count"],
                    "degraded": payload["degraded"],
                }
            )
        receipt = {
            "schema_version": "p7-local-regression-v1",
            "status": "PASSED",
            "observed_at": datetime.now(UTC).isoformat(),
            "mode": "local",
            **identity,
            "dataset_snapshot_id": config["dataset_snapshot_id"],
            "contract_sha256": config["contract_sha256"],
            "config_sha256": config["config_sha256"],
            "observations": observations,
            "gates": {
                "p6_graph_sqlite_path_completed": True,
                "public_response_scan_passed": True,
                "all_requested_workflows_completed": len(observations) == len(workflows),
            },
            "limitations": [
                "This developer regression does not measure target-user adoption or business impact."
            ],
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)
    except Exception:  # noqa: BLE001 - fixed CLI failure boundary
        target.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)
        return _emit(error_stream, "PREREQUISITE_FAILED", 3)
    output_stream.write(
        json.dumps(
            {
                "schema_version": "p7-local-regression-cli-v1",
                "status": "PASSED",
                "workflow_count": len(observations),
            },
            sort_keys=True,
        )
        + "\n"
    )
    return 0


def _emit(stream: TextIO, status: str, code: int) -> int:
    stream.write(
        json.dumps(
            {"schema_version": "p7-local-regression-cli-v1", "status": status},
            sort_keys=True,
        )
        + "\n"
    )
    return code
