"""Export the human check record of one workbench run, without review text."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from ecom_review_intelligence.verdict_export import (
    dump_records,
    read_verdicts,
    render_markdown,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        allow_abbrev=False,
        description="Write the human check record of a workbench run.",
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--run-token", default=None, help="Only this run; omitted = every run.")
    parser.add_argument("--verdicts-path", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None, help="Write here instead of stdout.")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = Path(args.project_root).resolve()
    path = args.verdicts_path or (
        root / "data" / "processed" / "p7_workbench_verdicts.jsonl"
    )
    records = read_verdicts(Path(path), args.run_token)
    body = render_markdown(records) if args.format == "markdown" else dump_records(records)
    if args.output is None:
        print(body)
        return 0
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8", newline="\n")
    print(json.dumps({"status": "written", "records": len(records)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
