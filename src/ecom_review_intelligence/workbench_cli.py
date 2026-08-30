"""Explicit loopback-only startup boundary for the P7 workbench."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TextIO

import uvicorn

from ecom_review_intelligence.workbench_resources import load_local_agent_resources
from ecom_review_intelligence.workbench_service import P6BackedWorkbenchService
from ecom_review_intelligence.workbench_web import create_workbench_app


class _SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise ValueError("Invalid workbench arguments.")


def _port(value: str) -> int:
    port = int(value)
    if not 1024 <= port <= 65535:
        raise argparse.ArgumentTypeError("Port is outside the allowed local range.")
    return port


def _parser() -> argparse.ArgumentParser:
    parser = _SafeParser(allow_abbrev=False, add_help=True)
    parser.add_argument("--host", choices=("127.0.0.1",), default="127.0.0.1")
    parser.add_argument("--port", type=_port, default=8765)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--mode", choices=("synthetic", "local"), default="synthetic")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--acknowledge-local-data", action="store_true")
    return parser


def workbench_main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    server_runner: Callable[..., Any] = uvicorn.run,
) -> int:
    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    try:
        args = _parser().parse_args(argv)
    except (SystemExit, TypeError, ValueError):
        errors.write(
            json.dumps(
                {"schema_version": "p7-workbench-cli-v1", "status": "INVALID_ARGUMENT"},
                sort_keys=True,
            )
            + "\n"
        )
        return 2
    if args.mode == "local" and not args.acknowledge_local_data:
        errors.write(
            json.dumps(
                {"schema_version": "p7-workbench-cli-v1", "status": "INVALID_ARGUMENT"},
                sort_keys=True,
            )
            + "\n"
        )
        return 2
    service = None
    if args.mode == "local":
        try:
            store_path, checkpoint_path, config, product_ids_by_code = (
                load_local_agent_resources(args.project_root)
            )
            service = P6BackedWorkbenchService(
                store_path=store_path,
                checkpoint_path=checkpoint_path,
                config=config,
                product_ids_by_code=product_ids_by_code,
            )
        except Exception:  # noqa: BLE001 - CLI boundary returns a fixed prerequisite error
            errors.write(
                json.dumps(
                    {"schema_version": "p7-workbench-cli-v1", "status": "PREREQUISITE_FAILED"},
                    sort_keys=True,
                )
                + "\n"
            )
            return 3
    if args.check:
        output.write(
            json.dumps(
                {
                    "schema_version": "p7-workbench-cli-v1",
                    "status": "READY",
                    "mode": args.mode,
                    "url": f"http://{args.host}:{args.port}",
                },
                sort_keys=True,
            )
            + "\n"
        )
        return 0
    server_runner(
        create_workbench_app(service=service),
        host=args.host,
        port=args.port,
        reload=False,
        access_log=False,
    )
    return 0
