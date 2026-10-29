"""Accept model labeling answers by code and merge the master table + audit.

Exits non-zero on any contract violation; the audit report written to
artifacts/audits/ is aggregate-only (counts, rates, source digests) and never
carries review text, spans, or identifiers.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ecom_review_intelligence.label_backfill import LabelBackfillError, merge_answers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool", action="append", type=Path, required=True)
    parser.add_argument("--answers", action="append", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="Master jsonl (local only).")
    parser.add_argument("--report", type=Path, help="Aggregate audit json (artifacts/audits/).")
    parser.add_argument("--labeled-by", required=True)
    parser.add_argument("--labeled-date", default=datetime.now(tz=UTC).date().isoformat())
    args = parser.parse_args(argv)
    try:
        rows, summary, problems = merge_answers(
            list(args.pool),
            list(args.answers),
            labeled_by=args.labeled_by,
            labeled_date=args.labeled_date,
        )
    except (LabelBackfillError, OSError) as error:
        print(json.dumps({"status": "MERGE_FAILED", "reason": str(error)[:400]}), file=sys.stderr)
        return 3
    if problems:
        print(
            json.dumps(
                {"status": "CONTRACT_VIOLATIONS", "count": len(problems), "sample": problems[:20]},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 3
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"status": "MERGED", "labelled_total": summary["labelled_total"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
