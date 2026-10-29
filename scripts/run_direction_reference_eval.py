"""Content-level baseline: deterministic workbench direction vs the model-label
reference layer, for the controlled products across every enabled aspect.

Engineering exploration, not a phase gate. Runs the real local workbench path
(deterministic gateway, no LLM), compares each product-aspect direction verdict
against the advisory label layer, and writes aggregate-only artifacts.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fastapi.testclient import TestClient

from ecom_review_intelligence.aspect_reference import (
    agreement_summary,
    build_aspect_reference,
)
from ecom_review_intelligence.workbench_resources import load_local_agent_resources
from ecom_review_intelligence.workbench_service import P6BackedWorkbenchService
from ecom_review_intelligence.workbench_web import (
    assert_public_workbench_safe,
    create_workbench_app,
)

DEFAULT_LABELS = ROOT / "data" / "processed" / "model_labels.jsonl"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--min-direction", type=int, default=5)
    parser.add_argument(
        "--details",
        type=Path,
        default=Path("artifacts/evals/direction-reference-details.json"),
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=Path("artifacts/evaluation-summaries/direction-reference-baseline.json"),
    )
    args = parser.parse_args(argv)
    root = args.project_root.resolve()
    store, checkpoint, config, product_map = load_local_agent_resources(root)

    reference = build_aspect_reference(
        store, args.labels, product_ids=tuple(product_map.values()), min_direction=args.min_direction
    )
    service = P6BackedWorkbenchService(
        store_path=store,
        checkpoint_path=checkpoint,
        config=config,
        product_ids_by_code=product_map,
    )
    client = TestClient(create_workbench_app(service=service))
    deterministic: dict[tuple[str, str], tuple[int, int]] = {}
    observations: list[dict[str, object]] = []
    for code, product_id in product_map.items():
        for aspect in sorted(reference.get(product_id, {})):
            response = client.post(
                "/api/v1/runs",
                json={
                    "workflow": "diagnosis",
                    "product_codes": [code],
                    "product_query": None,
                    "aspect": aspect,
                    "question": "Direction baseline check for evaluation.",
                },
            )
            if response.status_code != 201:
                raise SystemExit(f"Run failed for {code}/{aspect}: {response.status_code}")
            payload = response.json()
            assert_public_workbench_safe(payload)
            totals = next(
                item["signal_totals"]
                for item in payload["product_summaries"]
                if item["product_code"] == code
            )
            deterministic[(product_id, aspect)] = (
                int(totals["supporting"]),
                int(totals["opposing"]),
            )
            observations.append(
                {
                    "product_code": code,
                    "aspect": aspect,
                    "deterministic_supporting": totals["supporting"],
                    "deterministic_opposing": totals["opposing"],
                    "deterministic_insufficient": totals["insufficient"],
                }
            )
    summary = agreement_summary(reference, deterministic)
    details = {
        "schema_version": "direction-reference-details-v1",
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "dataset_snapshot_id": str(config["dataset_snapshot_id"]),
        "contract_sha256": str(config["contract_sha256"]),
        "min_direction": args.min_direction,
        "observations": observations,
    }
    summary_document = {
        "schema_version": "direction-reference-baseline-v1",
        "generated_at": details["generated_at"],
        "dataset_snapshot_id": details["dataset_snapshot_id"],
        "contract_sha256": details["contract_sha256"],
        "labeled_by": "qwen3.8flash@Qoder (advisory reference layer)",
        "status": "PASSED" if summary["comparable_total"] else "NO_COMPARABLE_CASES",
        "limitations": [
            "Silver-vs-advisory comparison: neither side is a human-adjudicated ground truth.",
            (
                "The two sides cover disjoint review subsets: the deterministic verdict comes from"
                " the reviews the lexicon CAN judge, the reference from the blind items it cannot."
                " Agreement therefore measures whether the blind part tells the same story as the"
                " visible part, not label accuracy."
            ),
            (
                "The deterministic side reflects the bounded evidence sample the workbench returns,"
                " not every matched review."
            ),
            "Engineering exploration; not a phase gate and not a business-impact claim.",
        ],
        **summary,
    }
    for path in (args.details, args.summary):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary_document if path == args.summary else details, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": summary_document["status"],
                "comparable_total": summary["comparable_total"],
                "agreement_rate": summary["agreement_rate"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
