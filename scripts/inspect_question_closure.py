"""Show, for every controlled product and aspect, whether that question is done.

This is the read-only view of the close-out judgment: no UI, no model call, no
writes. It measures each product-and-aspect pair against the frozen store and
prints the furthest state the evidence justifies, the reason code blocking it,
and the action an operator would be offered next.

Nothing is treated as read or verified here, so every conflicting row is
honestly reported as unread -- that is the state a fresh run actually starts in.

Usage:
    .venv/Scripts/python.exe scripts/inspect_question_closure.py --project-root .
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ecom_review_intelligence.agent_scenarios import enabled_aspects
from ecom_review_intelligence.agent_tools import EvidenceToolService, ToolInvocationLedger
from ecom_review_intelligence.engine import count_aspect_matches
from ecom_review_intelligence.question_closure import (
    assess_question,
    rows_from_evidence_items,
)
from ecom_review_intelligence.workbench_resources import load_local_agent_resources


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--retrieval-limit", type=int, default=5)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = args.project_root.resolve()
    store_path, _checkpoints, config, product_ids_by_code = load_local_agent_resources(root)
    aspects = enabled_aspects(store_path)
    service = EvidenceToolService(
        store_path,
        expected_snapshot_id=config["dataset_snapshot_id"],
        expected_contract_sha256=config["contract_sha256"],
        retrieval_limit=args.retrieval_limit,
        # This is a read-only inspection sweep, not one operator run, so it
        # declares its own budget instead of consuming the P6 single-tool one.
        ledger=ToolInvocationLedger(
            max_executions=max(1, len(product_ids_by_code) * len(aspects))
        ),
    )

    counts: dict[str, int] = {}
    for code, product_id in product_ids_by_code.items():
        for aspect in aspects:
            result = service.diagnose_product(product_id, (aspect,))
            matched = count_aspect_matches(store_path, (product_id,), aspect)
            rows = rows_from_evidence_items(
                result.items, matched_counts={(product_id, aspect): matched}
            )
            assessment = assess_question(rows, retrieval_limit=args.retrieval_limit)
            counts[assessment.state.value] = counts.get(assessment.state.value, 0) + 1
            row = rows[0]
            blockers = ",".join(code.value for code in assessment.blockers) or "-"
            caveats = ",".join(code.value for code in assessment.caveats) or "-"
            print(
                f"{code:<10} {aspect:<20} {row.strength:<11} "
                f"证据{row.evidence_count}/{matched} 负面{row.negative_or_conflicting:<2} "
                f"冲突{len(row.conflicting_ids):<2} {assessment.state.value:<9} "
                f"卡在: {blockers:<16} 限制: {caveats}"
            )
            for action in assessment.actions:
                print(f"    → {action.code.value:<16} {action.label}")

    print("\n状态分布: " + "  ".join(f"{state}={count}" for state, count in sorted(counts.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
