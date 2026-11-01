"""Run the evidence tools as an MCP stdio server for any MCP client.

Validates P6 lineage the same way the workbench does, then serves the five
deterministic tools on stdin/stdout. Readiness goes to stderr so stdout stays
a pure protocol channel. Claude Desktop config (Developer → Edit Config):

    "ecom-review-intelligence": {
        "command": "<python>",
        "args": ["scripts/run_mcp_server.py", "--project-root", "<project root>"]
    }
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        allow_abbrev=False,
        description="Serve the deterministic evidence tools over the MCP stdio protocol.",
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = Path(args.project_root).resolve()

    from ecom_review_intelligence.mcp_server import SERVER_NAME, SERVER_VERSION, McpEvidenceServer
    from ecom_review_intelligence.workbench_resources import load_local_agent_resources

    try:
        store_path, _checkpoint_path, config, _code_by_asin = load_local_agent_resources(root)
    except (OSError, ValueError, LookupError) as error:
        print(
            json.dumps(
                {"status": "FAILED", "reason": f"local evidence store unavailable: {error}"},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2

    server = McpEvidenceServer(
        store_path=store_path,
        snapshot_id=str(config["dataset_snapshot_id"]),
        contract_sha256=str(config["contract_sha256"]),
    )
    print(
        json.dumps(
            {
                "status": "ready",
                "server": SERVER_NAME,
                "version": SERVER_VERSION,
                "products": len(server.entries),
                "aspects": len(server.aspect_names),
                "store": store_path.name,
            },
            ensure_ascii=False,
        ),
        file=sys.stderr,
    )
    try:
        server.serve(sys.stdin, sys.stdout)
    except (BrokenPipeError, KeyboardInterrupt):
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
