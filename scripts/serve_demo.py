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
import sqlite3
import sys
from argparse import ArgumentParser, Namespace
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from ecom_review_intelligence.agent_cli import _read_config, _validate_store_lineage
from ecom_review_intelligence.workbench_resources import load_local_agent_resources
from ecom_review_intelligence.workbench_service import P6BackedWorkbenchService
from ecom_review_intelligence.workbench_web import create_workbench_app

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
_CODES = ("frostline", "northstar", "harbor")


def _bundled_paths(root: Path) -> tuple[Path, Path, Path]:
    public = root / "data" / "public"
    return (
        public / "ice_makers_public.sqlite",
        public / "state" / "checkpoints.sqlite",
        public / "state" / "verdicts.jsonl",
    )


def _bundled_product_ids(store: Path) -> dict[str, str]:
    with sqlite3.connect(f"file:{store.as_posix()}?mode=ro", uri=True) as connection:
        rows = connection.execute(
            """SELECT p.parent_asin FROM products p
               JOIN reviews r ON r.parent_asin = p.parent_asin
               GROUP BY p.parent_asin
               ORDER BY count(r.evidence_id) DESC, p.parent_asin
               LIMIT 3"""
        ).fetchall()
    if len(rows) < 3:
        raise SystemExit("The bundled store needs three products; rebuild it with build_public_subset.py.")
    return {code: row[0] for code, row in zip(_CODES, rows, strict=True)}


def _service_for(root: Path, mode: str, verdicts_path: Path | None) -> P6BackedWorkbenchService:
    config_path = root / "artifacts" / "manifests" / "phase-6-agent-config.json"
    if mode == "bundled":
        store, checkpoint, default_verdicts = _bundled_paths(root)
        if not store.is_file():
            raise SystemExit(
                f"The bundled store is missing: {store}\n"
                "Rebuild it from a local category store with "
                "`python scripts/build_public_subset.py --project-root .`."
            )
        config = _read_config(config_path)
        _validate_store_lineage(store, config)
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        return P6BackedWorkbenchService(
            store_path=store,
            checkpoint_path=checkpoint,
            config=config,
            product_ids_by_code=_bundled_product_ids(store),
            verdict_path=verdicts_path or default_verdicts,
        )
    private_store = root / "data" / "processed" / "ice_makers_evidence.sqlite"
    if not private_store.is_file():
        raise SystemExit(
            "The private category store is missing: "
            f"{private_store}\n"
            "--mode local reads the full 165 MB store. Either build it first "
            "(docs/deployment.md) or run the bundled subset instead: "
            "`docker compose up demo` / `--mode bundled`."
        )
    store, checkpoint, config, product_ids = load_local_agent_resources(root)
    return P6BackedWorkbenchService(
        store_path=store,
        checkpoint_path=checkpoint,
        config=config,
        product_ids_by_code=product_ids,
        verdict_path=verdicts_path,
    )


def build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        allow_abbrev=False,
        description="Serve the workbench outside the loopback-only desktop CLI.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Defaults to $PORT when set (Render, Fly, Cloud Run), else 8000.",
    )
    parser.add_argument("--mode", choices=("synthetic", "bundled", "local"), default="bundled")
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
        help="Required with --mode local: it reads the private category store.",
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


def resolve_port(args: Namespace) -> int:
    """Platforms hand the port in $PORT; an explicit flag still wins."""

    if args.port is not None:
        return args.port
    raw = os.environ.get("PORT", "8000").strip()
    try:
        port = int(raw)
    except ValueError as error:
        raise SystemExit(f"PORT is not a number: {raw!r}") from error
    if not 1 <= port <= 65535:
        raise SystemExit(f"PORT is outside the allowed range: {port}")
    return port


def validate(args: Namespace) -> None:
    if args.host not in _LOOPBACK and not args.allow_non_loopback:
        raise SystemExit(
            "Refusing to bind "
            f"{args.host} without --allow-non-loopback: that exposes the workbench "
            "beyond this machine."
        )
    if args.port is not None and not 1 <= args.port <= 65535:
        raise SystemExit("Port is outside the allowed range.")
    if args.mode == "local" and not args.acknowledge_local_data:
        raise SystemExit("--mode local reads the private category store; pass --acknowledge-local-data.")


def main(argv: Sequence[str] | None = None, *, server_runner=uvicorn.run) -> int:
    args = build_parser().parse_args(argv)
    validate(args)
    service = None if args.mode == "synthetic" else _service_for(
        Path(args.project_root).resolve(), args.mode, args.verdicts_path
    )
    app = create_workbench_app(service, extra_allowed_hosts=resolve_allowed_hosts(args))
    port = resolve_port(args)
    server_runner(app, host=args.host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
