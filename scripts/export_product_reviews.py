"""Export one product-and-aspect review set as a local CSV for human review.

Writes raw review text, so it only ever writes under the gitignored ``tmp/``
scratch workspace unless a caller says otherwise.

Usage:
    .venv/Scripts/python.exe scripts/export_product_reviews.py ^
        --product-code frostline --aspect noise
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ecom_review_intelligence.engine import query_aspect
from ecom_review_intelligence.review_export import (
    MAX_EXPORT_ROWS,
    ExportError,
    export_product_aspect,
)
from ecom_review_intelligence.workbench_resources import load_local_agent_resources


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--product-code", required=True)
    parser.add_argument("--aspect", required=True)
    parser.add_argument("--out", type=Path, default=None, help="Target directory.")
    parser.add_argument("--judged-limit", type=int, default=None)
    parser.add_argument("--max-rows", type=int, default=MAX_EXPORT_ROWS)
    parser.add_argument("--split", action="store_true", help="Also write one file per bucket.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = args.project_root.resolve()
    store_path, _checkpoints, config, product_ids_by_code = load_local_agent_resources(root)
    product_id = product_ids_by_code.get(args.product_code)
    if product_id is None:
        print(
            json.dumps(
                {"status": "EXPORT_FAILED", "reason": f"unknown product code: {args.product_code}"},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2

    judged_limit = args.judged_limit or int(config["retrieval_limit_per_aspect"])
    sample = query_aspect(store_path, (product_id,), args.aspect, limit=judged_limit)
    participated_ids = frozenset(item.evidence_id for item in sample.evidence)

    stamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    directory = args.out or (root / "tmp" / "exports" / f"{args.product_code}-{args.aspect}-{stamp}")
    try:
        result = export_product_aspect(
            store_path,
            directory,
            product_code=args.product_code,
            product_id=product_id,
            product_label=args.product_code,
            aspect=args.aspect,
            participated_ids=participated_ids,
            judged_limit=judged_limit,
            max_rows=args.max_rows,
            split=args.split,
        )
    except ExportError as error:
        print(
            json.dumps({"status": "EXPORT_FAILED", "reason": str(error)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2

    print(
        f"导出完成：{args.product_code} × {args.aspect}\n"
        f"  全库匹配 {result.matched_total} 条，导出 {len(result.rows)} 条"
        + ("（已按上限截断）" if result.truncated else "")
        + f"\n  其中参与本次判断 {result.participated_count} 条（judged_limit={judged_limit}）\n"
        + "  极性分布："
        + "、".join(f"{label}={count}" for label, count in result.bucket_counts.items())
        + f"\n  文件：{result.csv_path}"
    )
    for path in result.split_paths:
        print(f"  拆分：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
