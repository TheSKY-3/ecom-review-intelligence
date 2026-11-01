"""Aggregate the verdict log into the reviewed label set under artifacts/audits."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from ecom_review_intelligence.reviewed_labels import summarize_log, write_reports


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        allow_abbrev=False,
        description="Write the reviewed label set and divergence report from the verdict log.",
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--verdicts-path", type=Path, default=None)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Defaults to <project-root>/artifacts/audits.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = Path(args.project_root).resolve()
    verdicts_path = args.verdicts_path or (
        root / "data" / "processed" / "p7_workbench_verdicts.jsonl"
    )
    output_dir = args.output_dir or (root / "artifacts" / "audits")
    summary = summarize_log(verdicts_path)
    json_path, markdown_path = write_reports(summary, Path(output_dir))
    totals = summary["verdict_totals"]
    print(
        json.dumps(
            {
                "status": "written",
                "cards_total": summary["cards_total"],
                "runs_total": summary["runs_total"],
                "verdict_totals": totals,
                "confirmation_rate": summary["confirmation_rate"],
                "divergence_rate": summary["divergence_rate"],
                "json": json_path.name,
                "markdown": markdown_path.name,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
