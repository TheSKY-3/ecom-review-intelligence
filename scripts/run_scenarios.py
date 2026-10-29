"""Run the declared operator scenario presets against the frozen local store.

Every chain is data: the pack names each step's tool, products, aspects, and the
closed-enum gate that must pass before the next step spends more evidence
budget. Nothing here calls a model, so the same pack and the same frozen store
reproduce the same steps, stop codes, and evidence IDs.

The artifact is aggregate-plus-locators only: it records evidence IDs and
per-item counts, never review text. It carries its own lineage (dataset snapshot
id and contract SHA-256) so it can be audited alone.

Usage:
    .venv/Scripts/python.exe scripts/run_scenarios.py --project-root .
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ecom_review_intelligence.agent_scenarios import (
    ScenarioError,
    load_pack,
    run_pack,
    scenario_run_document,
)
from ecom_review_intelligence.workbench_resources import load_local_agent_resources

DEFAULT_PACK = Path("artifacts") / "manifests" / "operator-scenarios-v1.json"
DEFAULT_OUT = Path("artifacts") / "scenarios" / "scenario-run-v1.json"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--pack", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = args.project_root.resolve()
    pack_path = args.pack if args.pack is not None else root / DEFAULT_PACK
    out_path = args.out if args.out is not None else root / DEFAULT_OUT

    store_path, _checkpoints, config, product_ids_by_code = load_local_agent_resources(root)
    pack = load_pack(pack_path)
    try:
        results = run_pack(
            pack,
            store_path=store_path,
            expected_snapshot_id=config["dataset_snapshot_id"],
            expected_contract_sha256=config["contract_sha256"],
            product_ids_by_code=product_ids_by_code,
            retrieval_limit=int(config["retrieval_limit_per_aspect"]),
        )
    except ScenarioError as error:
        print(f"scenario run failed: {error}", file=sys.stderr)
        return 1

    document = scenario_run_document(
        pack,
        results,
        dataset_snapshot_id=config["dataset_snapshot_id"],
        contract_sha256=config["contract_sha256"],
        retrieval_limit=int(config["retrieval_limit_per_aspect"]),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    for result in results:
        print(f"[{result.stop_code}] {result.scenario_id}  {result.title}")
        for step in result.steps:
            if step.status == "gated_out":
                measured = step.condition.measured if step.condition else {}
                print(
                    f"  step {step.step} {step.tool:<11} 跳过"
                    f"（最大负面/冲突 {measured.get('max_negative_or_conflicting', 0)} 条，未达门槛）"
                )
                continue
            worst = max(
                (item.negative_or_conflicting for item in step.items), default=0
            )
            print(
                f"  step {step.step} {step.tool:<11} "
                f"{'、'.join(step.products)} × {'、'.join(step.aspects)} "
                f"共 {len(step.items)} 项，最多负面/冲突 {worst} 条"
            )
    totals = document["totals"]
    print(
        f"\n共 {totals['scenario_count']} 条链，执行 {totals['executed_step_count']} 步，"
        f"被门槛拦下 {totals['gated_out_step_count']} 步"
    )
    print(f"工件: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
