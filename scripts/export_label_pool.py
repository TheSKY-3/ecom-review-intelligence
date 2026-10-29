"""Export the lexicon-unjudged pool as a batched blind-labeling package.

The package directory receives raw review text and must live in a local-only
location (tmp/ is gitignored). Ratings never appear in the handoff sheets.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ecom_review_intelligence.label_backfill import LabelBackfillError, export_package

DEFAULT_STORE = ROOT / "data" / "processed" / "ice_makers_evidence.sqlite"
DEFAULT_RULES = ROOT / "docs" / "superpowers" / "specs" / "2026-09-25-labeling-contract.md"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--out", type=Path, required=True, help="Local-only package directory.")
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--aspect", action="append", default=[])
    parser.add_argument(
        "--exclude-answers",
        action="append",
        type=Path,
        default=[],
        help="Answer dir/file whose ids are already labeled (repeatable).",
    )
    args = parser.parse_args(argv)
    try:
        from ecom_review_intelligence.label_backfill import load_answers

        exclude_ids: set[str] = set()
        if args.exclude_answers:
            answers, _ = load_answers(list(args.exclude_answers))
            exclude_ids = set(answers)
        summary = export_package(
            args.store,
            args.out,
            args.rules,
            batch_size=args.batch_size,
            aspects=tuple(args.aspect) or None,
            exclude_ids=exclude_ids,
        )
    except (LabelBackfillError, OSError) as error:
        print(json.dumps({"status": "EXPORT_FAILED", "reason": str(error)[:400]}), file=sys.stderr)
        return 3
    print(json.dumps({"status": "EXPORTED", **summary}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
