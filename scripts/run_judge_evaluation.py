"""D1 judge evaluation: collect real workbench answers, then judge each one.

For a bounded plan of runs (diagnosis per product, one comparison, one triage
per aspect) the script calls the same local workbench API the UI uses --
insight and chat answers included -- and hands every answer to a constrained
judge call that must submit a closed verdict (pass/warn/fail + issue codes)
against the run's own statistics.

Default is dry-run: runs are collected (free, local), insight/chat/judge
calls are skipped, and the gateway factories are wired to ``None`` so no
accidental spend is possible. ``--live`` enables the model calls and REQUIRES
``--acknowledge-live-api``. The API key is read only from the process
environment (DEEPSEEK_API_KEY); it is never a command-line argument, never
written to disk, never logged. The details artifact (full answers, judge
rationales) stays under ``artifacts/evals/`` (unpublished); the summary and
report are aggregate-only.

Engineering exploration, not a phase gate.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fastapi.testclient import TestClient
from openai import OpenAI

from ecom_review_intelligence.evidence_judge import (
    JUDGE_SYSTEM_PROMPT,
    JUDGE_TOOL_NAME,
    EvidenceJudgeError,
    JudgeCase,
    chat_answer_digest,
    count_issue_codes,
    evaluate_cases,
    insight_answer_digest,
    judge_rates,
    judge_tool_schema,
    per_workflow_counts,
    render_judge_report,
    validate_judge_summary,
)
from ecom_review_intelligence.workbench_resources import load_local_agent_resources
from ecom_review_intelligence.workbench_service import P6BackedWorkbenchService
from ecom_review_intelligence.workbench_web import (
    assert_public_workbench_safe,
    create_workbench_app,
)

DETAILS_PATH = Path("artifacts/evals/agent-judge-eval-details.json")
SUMMARY_PATH = Path("artifacts/evaluation-summaries/agent-judge-eval.json")
REPORT_PATH = Path("docs/reports/agent-judge-evaluation-2026-11.md")

LIMITATIONS = [
    (
        "The judge is the same config-pinned model as the agent: it grades semantic "
        "fidelity against the run's own statistics, not human-adjudicated ground truth."
    ),
    (
        "Judge verdicts come from one forced tool-call per answer with thinking "
        "disabled and default sampling; verdict stability across runs was not "
        "measured (one missing-rationale judge error appeared in 9 final calls and "
        "is recorded fail-closed)."
    ),
    (
        "Answers were collected from the bundled local evidence store; the judge sees "
        "only the answer digest plus that run's counts, never the review corpus."
    ),
    (
        "Two earlier iterations produced false fails because the judge payload "
        "lacked the per-product and lexicon screen counts the chat legitimately "
        "cites; the final payload mirrors the chat's stat sources exactly."
    ),
    "A judge error is counted as an error (fail-closed), not silently dropped.",
    "Engineering exploration; not a phase gate and not a business-impact claim.",
]


def _post(client: TestClient, path: str, payload: dict[str, object]) -> dict[str, object]:
    response = client.post(path, json=payload)
    if response.status_code not in (200, 201):
        raise RuntimeError(f"{path} -> HTTP {response.status_code}")
    body = response.json()
    assert_public_workbench_safe(body)
    return body


def _run_stats(
    run: dict[str, object],
    service: P6BackedWorkbenchService,
    product_map: dict[str, str],
    aspect: str,
) -> dict[str, object]:
    """Ground truth the judge checks the answer against, in two layers.

    ``aggregate`` is the run-level rollup; ``products`` carries, per product,
    the display label, the stratified sample counts, and the per-product
    lexicon screen counts read with the SAME helper the chat's
    ``full_population_totals`` stat uses (workbench_service._lexicon_totals).
    Earlier runs measured false fails when the judge could not see the numbers
    the chat legitimately cites; both layers close that gap.
    """

    aggregate: dict[str, object] = {
        "matched_count": 0,
        "supporting": 0,
        "opposing": 0,
        "conflicting": 0,
    }
    for summary in run.get("product_summaries") or ():
        if not isinstance(summary, dict):
            continue
        aggregate["matched_count"] += int(summary.get("matched_count") or 0)  # type: ignore[arg-type]
        totals = summary.get("signal_totals") or {}
        for key in ("supporting", "opposing", "conflicting"):
            aggregate[key] += int(totals.get(key) or 0)  # type: ignore[operator]
    lexicon = run.get("lexicon_totals")
    if isinstance(lexicon, dict):
        aggregate["lexicon_matched"] = int(lexicon.get("matched_count") or 0)
        for key in ("supporting", "opposing", "conflicting", "insufficient"):
            aggregate[f"lexicon_{key}"] = int(lexicon.get(key) or 0)

    sample_by_code = {
        str(summary.get("product_code")): summary
        for summary in run.get("product_summaries") or ()
        if isinstance(summary, dict)
    }
    products: list[dict[str, object]] = []
    for view in run.get("products") or ():
        if not isinstance(view, dict) or view.get("code") not in product_map:
            continue
        code = str(view["code"])
        summary = sample_by_code.get(code, {})
        product_totals = service._lexicon_totals([product_map[code]], aspect)
        products.append(
            {
                "product_code": code,
                "label": str(view.get("label") or code),
                "matched_count": int(summary.get("matched_count") or 0),
                "sample_supporting": int((summary.get("signal_totals") or {}).get("supporting") or 0),
                "sample_opposing": int((summary.get("signal_totals") or {}).get("opposing") or 0),
                "sample_conflicting": int((summary.get("signal_totals") or {}).get("conflicting") or 0),
                "lexicon_matched": product_totals.matched_count,
                "lexicon_supporting": product_totals.supporting,
                "lexicon_opposing": product_totals.opposing,
                "lexicon_conflicting": product_totals.conflicting,
                "lexicon_insufficient": product_totals.insufficient,
            }
        )
    return {"aggregate": aggregate, "products": products}


def _chat_message(workflow: str, aspect: str) -> str:
    if workflow == "comparison":
        return f"两台相比，{aspect}哪边更让人放心？请给出能核对原文的说法。"
    if workflow == "triage":
        return f"{aspect}问题先查哪一台？为什么？"
    return f"这款的{aspect}问题普遍吗？请给出能核对原文的说法。"


def _collect_cases(
    client: TestClient,
    service: P6BackedWorkbenchService,
    product_map: dict[str, str],
    aspects: list[str],
    max_items: int,
    live: bool,
) -> tuple[list[JudgeCase], int]:
    """Walk a bounded run plan and build one judge case per answer.

    Dry-run never touches the insight/chat endpoints (no spend possible);
    each planned answer is recorded with status ``SKIPPED_DRY_RUN`` so the
    downstream judging path is exercised without a model.
    """

    cases: list[JudgeCase] = []
    generation_calls = 0
    seq = 0
    for aspect in aspects:
        plans: list[tuple[str, list[str]]] = [
            ("diagnosis", [code]) for code in sorted(product_map)
        ]
        codes = sorted(product_map)
        if len(codes) >= 2:
            plans.append(("comparison", codes[:2]))
        if len(codes) >= 2:
            plans.append(("triage", codes))
        for workflow, run_codes in plans:
            if seq >= max_items:
                return cases, generation_calls
            try:
                run = _post(
                    client,
                    "/api/v1/runs",
                    {
                        "workflow": workflow,
                        "product_codes": run_codes,
                        "product_query": None,
                        "aspect": aspect,
                        "question": "Judge evaluation baseline check.",
                    },
                )
            except (RuntimeError, ValueError) as error:
                cases.append(
                    JudgeCase(
                        item_id=f"{seq:02d}-{workflow}",
                        workflow=workflow,
                        aspect=aspect,
                        product_codes=tuple(run_codes),
                        question="",
                        answer_text="",
                        answer_status=f"RUN_FAILED ({str(error)[:80]})",
                        model_calls=0,
                    )
                )
                seq += 1
                continue
            stats = _run_stats(run, service, product_map, aspect)
            run_token = str(run.get("run_token"))
            evidence = [item for item in run.get("evidence") or () if isinstance(item, dict)]
            planned: list[tuple[str, dict[str, object] | None]] = [
                ("insight", {"run_token": run_token, "card_alias": "", "question": None}),
                ("chat", {"run_token": run_token, "message": _chat_message(workflow, aspect)}),
            ]
            for kind, payload in planned:
                if seq >= max_items:
                    return cases, generation_calls
                item_id = f"{seq:02d}-{kind}"
                seq += 1
                view: dict[str, object] | None = None
                if live:
                    if kind == "insight":
                        if not evidence:
                            cases.append(
                                JudgeCase(
                                    item_id=item_id,
                                    workflow=kind,
                                    aspect=aspect,
                                    product_codes=tuple(run_codes),
                                    question="",
                                    answer_text="",
                                    answer_status="NO_EVIDENCE_CARDS",
                                    model_calls=0,
                                    stats=stats,
                                )
                            )
                            continue
                        payload["card_alias"] = str(evidence[0].get("evidence_alias"))
                    try:
                        view = _post(client, f"/api/v1/{'insights' if kind == 'insight' else 'chat'}", payload)
                    except (RuntimeError, ValueError) as error:
                        cases.append(
                            JudgeCase(
                                item_id=item_id,
                                workflow=kind,
                                aspect=aspect,
                                product_codes=tuple(run_codes),
                                question=str(payload.get("message") or payload.get("question") or ""),
                                answer_text="",
                                answer_status=f"CALL_FAILED ({str(error)[:80]})",
                                model_calls=0,
                                stats=stats,
                            )
                        )
                        continue
                else:
                    view = {"status": "SKIPPED_DRY_RUN"}
                model_calls = int(view.get("model_calls") or 0)
                generation_calls += model_calls
                digest = (
                    insight_answer_digest(view)
                    if kind == "insight"
                    else chat_answer_digest(view)
                )
                cases.append(
                    JudgeCase(
                        item_id=item_id,
                        workflow=kind,
                        aspect=aspect,
                        product_codes=tuple(run_codes),
                        question=str(payload.get("message") or ""),
                        answer_text=digest,
                        answer_status=str(view.get("status")),
                        model_calls=model_calls,
                        stats=stats,
                    )
                )
    return cases, generation_calls


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--aspects", type=str, default="noise", help="Comma-separated frozen aspects.")
    parser.add_argument("--products", type=str, default=None, help="Comma-separated product-code filter.")
    parser.add_argument("--max-items", type=int, default=24, help="Hard cap on judged answers.")
    parser.add_argument("--live", action="store_true", help="Enable real model calls (costs money).")
    parser.add_argument(
        "--acknowledge-live-api",
        action="store_true",
        help="Explicit acknowledgement required together with --live.",
    )
    parser.add_argument("--details", type=Path, default=DETAILS_PATH)
    parser.add_argument("--summary", type=Path, default=SUMMARY_PATH)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    args = parser.parse_args(argv)
    if args.live and not args.acknowledge_live_api:
        parser.error("--live requires --acknowledge-live-api")

    root = args.project_root.resolve()
    store, checkpoint, config, product_map = load_local_agent_resources(root)
    key = os.environ.get("DEEPSEEK_API_KEY") if args.live else None
    if args.live and not key:
        print(json.dumps({"status": "MISSING_API_KEY", "hint": "set DEEPSEEK_API_KEY in env"}))
        return 2

    wanted_products = (
        {item.strip() for item in args.products.split(",") if item.strip()}
        if args.products
        else None
    )
    if wanted_products:
        product_map = {code: pid for code, pid in product_map.items() if code in wanted_products}
    aspects = [item.strip() for item in args.aspects.split(",") if item.strip()]
    if not product_map or not aspects:
        print(json.dumps({"status": "EMPTY_PLAN"}))
        return 2

    # Dry-run wires both factories to None: even an accidental insight/chat
    # call cannot reach the API, let alone spend money.
    service = P6BackedWorkbenchService(
        store_path=store,
        checkpoint_path=checkpoint,
        config=config,
        product_ids_by_code=product_map,
        **({} if args.live else {"insight_gateway_factory": lambda: None, "chat_gateway_factory": lambda: None}),
    )
    client = TestClient(create_workbench_app(service=service, insight_rate_limit=(200, 600.0)))

    cases, generation_calls = _collect_cases(
        client, service, product_map, aspects, args.max_items, args.live
    )

    judge_calls = 0
    tokens = {"input": 0, "output": 0}
    judge_fn = None
    if args.live:
        live_client = OpenAI(
            api_key=key,
            base_url=str(config["base_url"]),
            max_retries=0,
            timeout=float(config["request_timeout_seconds"]),
        )

        def judge_fn(payload: dict[str, object]) -> object:
            nonlocal judge_calls
            response = live_client.chat.completions.create(  # type: ignore[attr-defined]
                model=str(config["model"]),
                messages=[
                    {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(payload, sort_keys=True, ensure_ascii=False)},
                ],
                tools=judge_tool_schema(),
                tool_choice={"type": "function", "function": {"name": JUDGE_TOOL_NAME}},
                stream=False,
                max_tokens=int(config["max_output_tokens"]),
                timeout=float(config["request_timeout_seconds"]),
                extra_body={"thinking": {"type": "disabled"}},
            )
            message = response.choices[0].message
            tool_calls = message.tool_calls or ()
            if len(tool_calls) != 1 or tool_calls[0].function.name != JUDGE_TOOL_NAME:
                raise EvidenceJudgeError("judge did not submit exactly one verdict tool call")
            usage = getattr(response, "usage", None)
            tokens["input"] += int(getattr(usage, "prompt_tokens", 0) or 0)
            tokens["output"] += int(getattr(usage, "completion_tokens", 0) or 0)
            judge_calls += 1
            return json.loads(tool_calls[0].function.arguments)

    entries, verdict_counts = evaluate_cases(cases, judge_fn)
    generated_at = datetime.now(tz=UTC).isoformat()
    summary_document = {
        "schema_version": "agent-judge-eval-v1",
        "generated_at": generated_at,
        "dataset_snapshot_id": str(config["dataset_snapshot_id"]),
        "contract_sha256": str(config["contract_sha256"]),
        "mode": "live" if args.live else "dry-run",
        "model": str(config["model"]),
        "cases_collected": len(cases),
        "generation_model_calls": generation_calls,
        "judge_model_calls": judge_calls,
        "judge_tokens": tokens if args.live else None,
        "verdict_counts": verdict_counts,
        "rates": judge_rates(verdict_counts),
        "issue_code_counts": count_issue_codes(entries),
        "per_workflow": per_workflow_counts(entries),
        "limitations": LIMITATIONS,
    }
    validate_judge_summary(summary_document)

    details_entries = [
        {
            **entry,
            "question": case.question,
            "answer_text": case.answer_text,
            "ground_truth_stats": dict(case.stats),
        }
        for case, entry in zip(cases, entries, strict=True)
    ]
    details_document = {
        "schema_version": "agent-judge-eval-details-v1",
        "generated_at": generated_at,
        "dataset_snapshot_id": str(config["dataset_snapshot_id"]),
        "contract_sha256": str(config["contract_sha256"]),
        "cases": details_entries,
    }
    report = render_judge_report(summary_document, entries)
    for path, text in (
        (args.details, json.dumps(details_document, ensure_ascii=False, indent=1) + "\n"),
        (args.summary, json.dumps(summary_document, ensure_ascii=False, indent=1) + "\n"),
        (args.report, report),
    ):
        resolved = path if path.is_absolute() else root / path
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(text, encoding="utf-8")

    status = "DRY_RUN" if not args.live else "PASSED"
    if args.live and cases and judge_calls == 0:
        status = "LIVE_JUDGE_FAILED"
    print(
        json.dumps(
            {
                "status": status,
                "mode": summary_document["mode"],
                "cases_collected": len(cases),
                "judge_calls": judge_calls,
                "verdict_counts": verdict_counts,
                "rates": summary_document["rates"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
