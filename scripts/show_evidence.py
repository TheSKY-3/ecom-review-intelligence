"""Inspect local P6 evidence rows for one workbench product alias and aspect.

Owner-side companion tool for the P7 workbench: the workbench shows aggregate
cards only, by design; this script prints the underlying local review rows so
the store owner can perform the "next step" human check offline.

Local, private data: output contains raw review text and local evidence IDs.
Do not commit, publish, or paste it into public material.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ecom_review_intelligence.engine import query_aspect
from ecom_review_intelligence.workbench_resources import load_local_agent_resources

ALIASES = ("frostline", "northstar", "harbor")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alias", choices=ALIASES, required=True, help="Workbench product alias.")
    parser.add_argument(
        "--aspect",
        required=True,
        help="Frozen P3 aspect, for example noise or cleaning_maintenance.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="How many matched reviews to show. Default 5, which is the workbench limit.",
    )
    parser.add_argument("--full", action="store_true", help="Print full review text without truncation.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not 1 <= args.limit <= 50:
        print("ERROR: --limit must be between 1 and 50.", file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parents[1]
    try:
        store_path, _checkpoint_path, _config, alias_to_product = load_local_agent_resources(root)
        result = query_aspect(store_path, (alias_to_product[args.alias],), args.aspect, limit=args.limit)
    except Exception as error:  # noqa: BLE001 - CLI boundary prints one fixed local error line
        print(f"ERROR: {error}", file=sys.stderr)
        return 3

    import sqlite3

    connection = sqlite3.connect(f"file:{store_path.as_posix()}?mode=ro", uri=True)
    aspect_terms = json.loads(
        connection.execute("SELECT value FROM store_meta WHERE key = 'aspect_terms'").fetchone()[0]
    )
    predicates = " OR ".join("lower(text) LIKE ?" for _ in aspect_terms[args.aspect])
    parameters = [alias_to_product[args.alias], *[f"%{term.lower()}%" for term in aspect_terms[args.aspect]]]
    total_matched = connection.execute(
        f"SELECT count(*) FROM reviews WHERE parent_asin = ? AND ({predicates})",
        parameters,
    ).fetchone()[0]
    connection.close()

    counts: dict[str, int] = {}
    for evidence in result.evidence:
        counts[evidence.polarity] = counts.get(evidence.polarity, 0) + 1

    print("[本地私有数据] 仅限项目所有者本地核查;勿截图外发、勿提交到仓库。")
    print(f"product  : {args.alias} -> {alias_to_product[args.alias]}")
    print(f"aspect   : {args.aspect}")
    print(
        f"strength : {result.strength} (retrieved {len(result.evidence)}, "
        f"total matched in store: {total_matched})"
    )
    print(f"polarity : {json.dumps(counts, sort_keys=True)} (within retrieved evidence)")
    for index, evidence in enumerate(result.evidence, start=1):
        text = evidence.text.replace("\n", " ")
        if not args.full:
            text = text[:280] + ("..." if len(text) > 280 else "")
        print("-" * 72)
        print(
            f"#{index} {evidence.polarity} | rating={evidence.rating} "
            f"helpful={evidence.helpful_vote} | line={evidence.source_line} "
            f"| evidence_id={evidence.evidence_id[:16]}..."
        )
        print(f"   {text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
