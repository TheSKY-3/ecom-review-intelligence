"""Serve the workbench for a container or a remote box, with explicit opt-ins.

The packaged CLI (`run_phase7_workbench.py`) stays loopback-only on purpose: it
is the desktop entry point, and nobody should be able to expose local review
data by adding one flag to it. This entry point exists for the two cases that
need a different answer, and both require saying so out loud:

- binding a non-loopback address needs ``--allow-non-loopback``;
- accepting a host name other than loopback needs ``--allowed-host`` (or
  ``WORKBENCH_ALLOWED_HOSTS``), because the app rejects unknown Host headers to
  block DNS-rebinding.

Default deployment is `--mode synthetic`, which carries no review data at all.
"""

from __future__ import annotations

import os
import sys
from argparse import ArgumentParser, Namespace
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from ecom_review_intelligence.workbench_resources import load_local_agent_resources
from ecom_review_intelligence.workbench_service import P6BackedWorkbenchService
from ecom_review_intelligence.workbench_web import create_workbench_app

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        allow_abbrev=False,
        description="Serve the workbench outside the loopback-only desktop CLI.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--mode", choices=("synthetic", "local"), default="synthetic")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--allow-non-loopback",
        action="store_true",
        help="Required to bind anything other than 127.0.0.1 / localhost.",
    )
    parser.add_argument(
        "--allowed-host",
        action="append",
        default=[],
        help="Extra Host header values to accept (repeatable).",
    )
    parser.add_argument(
        "--acknowledge-local-data",
        action="store_true",
        help="Required with --mode local: it reads the private review store.",
    )
    parser.add_argument(
        "--verdicts-path",
        type=Path,
        default=None,
        help="Where to append human check records (default: data/processed).",
    )
    return parser


def resolve_allowed_hosts(args: Namespace) -> tuple[str, ...]:
    from_env = os.environ.get("WORKBENCH_ALLOWED_HOSTS", "")
    names = list(args.allowed_host)
    names.extend(part for part in from_env.split(",") if part.strip())
    return tuple(dict.fromkeys(name.strip() for name in names if name.strip()))


def validate(args: Namespace) -> None:
    if args.host not in _LOOPBACK and not args.allow_non_loopback:
        raise SystemExit(
            "Refusing to bind "
            f"{args.host} without --allow-non-loopback: that exposes the workbench "
            "beyond this machine."
        )
    if not 1 <= args.port <= 65535:
        raise SystemExit("Port is outside the allowed range.")
    if args.mode == "local" and not args.acknowledge_local_data:
        raise SystemExit("--mode local reads the private review store; pass --acknowledge-local-data.")


def main(argv: Sequence[str] | None = None, *, server_runner=uvicorn.run) -> int:
    args = build_parser().parse_args(argv)
    validate(args)
    if args.mode == "local":
        root = Path(args.project_root).resolve()
        store, checkpoint, config, product_ids = load_local_agent_resources(root)
        service = P6BackedWorkbenchService(
            store_path=store,
            checkpoint_path=checkpoint,
            config=config,
            product_ids_by_code=product_ids,
            verdict_path=args.verdicts_path,
        )
    else:
        service = None
    app = create_workbench_app(service, extra_allowed_hosts=resolve_allowed_hosts(args))
    server_runner(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
