import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from ecom_review_intelligence.agent_scenarios import (
    Scenario,
    ScenarioError,
    ScenarioPack,
    ScenarioStep,
    StepCondition,
    StepMeasurement,
    evaluate_condition,
    load_pack,
    run_pack,
    scenario_run_document,
)
from ecom_review_intelligence.agent_tools import AgentToolError, ToolInvocationLedger
from ecom_review_intelligence.engine import compute_evidence_id, initialize_store, verify_citation

SNAPSHOT = "a" * 64
CONTRACT = "b" * 64
PRODUCTS = {"alpha": "B001", "beta": "B002", "gamma": "B003"}


def _item(
    product_id: str = "B001",
    aspect: str = "noise",
    strength: str = "MODERATE",
    opposing: int = 0,
    conflicting: int = 0,
    evidence_count: int = 2,
) -> StepMeasurement:
    return StepMeasurement(
        product_id=product_id,
        aspect=aspect,
        strength=strength,
        supporting=max(evidence_count - opposing - conflicting, 0),
        opposing=opposing,
        conflicting=conflicting,
        insufficient=0,
        evidence_ids=tuple(f"e{index}" for index in range(evidence_count)),
    )


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    path = tmp_path / "evidence.sqlite"
    initialize_store(
        path,
        snapshot_id=SNAPSHOT,
        contract_sha256=CONTRACT,
        aspect_terms={
            "noise": ("noise", "noisy", "loud", "quiet"),
            "reliability": ("broke", "failed", "reliable"),
        },
        polarity_terms={
            "supporting": ("good", "quiet", "reliable"),
            "opposing": ("bad", "loud", "broke", "failed"),
        },
    )
    reviews: list[tuple[int, str, str]] = []
    line = 1
    for _ in range(5):
        reviews.append((line, "B001", "quiet good noise"))
        line += 1
    for _ in range(5):
        reviews.append((line, "B002", "loud bad noise"))
        line += 1
    for _ in range(2):
        reviews.append((line, "B003", "quiet noise"))
        line += 1
    for _ in range(2):
        reviews.append((line, "B001", "broke failed reliability"))
        line += 1
    for _ in range(5):
        reviews.append((line, "B003", "reliable good reliability"))
        line += 1
    with sqlite3.connect(path) as connection:
        connection.executemany(
            "INSERT INTO products(parent_asin, title) VALUES (?, ?)",
            (("B001", "Alpha Quiet"), ("B002", "Beta Loud"), ("B003", "Gamma Mixed")),
        )
        connection.executemany(
            """
            INSERT INTO reviews(
                evidence_id, source_line, parent_asin, text, rating, timestamp,
                verified_purchase, helpful_vote
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (compute_evidence_id(SNAPSHOT, at), at, product, text, 3.0, 1, 1, 0)
                for at, product, text in reviews
            ],
        )
    return path


def _pack(*scenarios: dict[str, object]) -> ScenarioPack:
    return ScenarioPack.model_validate(
        {
            "schema_version": "p7-operator-scenarios-v1",
            "pack_id": "test-pack",
            "scenarios": list(scenarios),
        }
    )


def _run(
    store_path: Path, pack: ScenarioPack, *, snapshot: str = SNAPSHOT, contract: str = CONTRACT
):
    return run_pack(
        pack,
        store_path=store_path,
        expected_snapshot_id=snapshot,
        expected_contract_sha256=contract,
        product_ids_by_code=PRODUCTS,
        retrieval_limit=5,
    )


def test_condition_any_item_passes_when_one_row_meets_the_bar() -> None:
    outcome = evaluate_condition(
        StepCondition(min_negative_or_conflicting=2),
        (_item(opposing=0), _item(opposing=2)),
    )

    assert outcome.satisfied is True
    assert outcome.measured["passing_item_count"] == 1
    assert outcome.measured["max_negative_or_conflicting"] == 2


def test_condition_every_item_requires_all_rows() -> None:
    condition = StepCondition(min_negative_or_conflicting=2, require="every_item")

    assert evaluate_condition(condition, (_item(opposing=2), _item(opposing=3))).satisfied is True
    assert evaluate_condition(condition, (_item(opposing=2), _item(opposing=0))).satisfied is False


def test_condition_strength_floor_rejects_weaker_rows() -> None:
    condition = StepCondition(min_strength="STRONG")

    assert evaluate_condition(condition, (_item(strength="STRONG"),)).satisfied is True
    assert evaluate_condition(condition, (_item(strength="MODERATE"),)).satisfied is False
    assert evaluate_condition(condition, ()).satisfied is False


def test_pack_rejects_a_gate_on_the_first_step() -> None:
    with pytest.raises(ValidationError):
        _pack(
            {
                "id": "bad_chain",
                "title": "第一步不该有条件",
                "steps": [
                    {
                        "step": 1,
                        "tool": "diagnosis",
                        "products": ["alpha"],
                        "aspects": ["noise"],
                        "when": {"min_negative_or_conflicting": 1},
                    }
                ],
            }
        )


def test_pack_rejects_an_ungated_later_step() -> None:
    with pytest.raises(ValidationError):
        _pack(
            {
                "id": "bad_chain",
                "title": "第二步必须有条件",
                "steps": [
                    {"step": 1, "tool": "diagnosis", "products": ["alpha"], "aspects": ["noise"]},
                    {"step": 2, "tool": "comparison", "products": ["alpha", "beta"], "aspects": ["noise"]},
                ],
            }
        )


def test_pack_rejects_a_derived_product_outside_diagnosis() -> None:
    with pytest.raises(ValidationError):
        ScenarioStep.model_validate(
            {
                "step": 2,
                "tool": "comparison",
                "products": ["$top_product", "beta"],
                "aspects": ["noise"],
                "when": {},
            }
        )


def test_pack_rejects_all_aspects_mixed_with_a_named_aspect() -> None:
    with pytest.raises(ValidationError):
        ScenarioStep.model_validate(
            {"step": 1, "tool": "triage", "products": ["alpha"], "aspects": ["*", "noise"]}
        )


def test_ledger_keeps_the_single_tool_rule_by_default() -> None:
    ledger = ToolInvocationLedger()

    ledger.consume()

    with pytest.raises(AgentToolError, match="only one business evidence tool"):
        ledger.consume()


def test_ledger_honors_a_declared_budget() -> None:
    ledger = ToolInvocationLedger(max_executions=2)

    ledger.consume()
    ledger.consume()

    with pytest.raises(AgentToolError, match="at most 2"):
        ledger.consume()
    assert ledger.execution_count == 2


def test_chain_fires_the_second_step_when_the_gate_passes(store_path: Path) -> None:
    pack = _pack(
        {
            "id": "firing_chain",
            "title": "有负面就横向比",
            "steps": [
                {"step": 1, "tool": "diagnosis", "products": ["beta"], "aspects": ["noise"]},
                {
                    "step": 2,
                    "tool": "comparison",
                    "products": ["alpha", "beta", "gamma"],
                    "aspects": ["noise"],
                    "when": {"min_negative_or_conflicting": 1},
                },
            ],
        }
    )

    (result,) = _run(store_path, pack)

    assert result.stop_code == "COMPLETED"
    assert [step.status for step in result.steps] == ["executed", "executed"]
    assert result.steps[0].items[0].negative_or_conflicting == 5
    assert result.steps[1].products == ("alpha", "beta", "gamma")


def test_chain_stops_when_the_gate_fails(store_path: Path) -> None:
    pack = _pack(
        {
            "id": "gated_chain",
            "title": "没负面就不横向比",
            "steps": [
                {"step": 1, "tool": "diagnosis", "products": ["alpha"], "aspects": ["noise"]},
                {
                    "step": 2,
                    "tool": "comparison",
                    "products": ["alpha", "beta", "gamma"],
                    "aspects": ["noise"],
                    "when": {"min_negative_or_conflicting": 1},
                },
            ],
        }
    )

    (result,) = _run(store_path, pack)

    assert result.stop_code == "CONDITION_NOT_MET"
    assert [step.status for step in result.steps] == ["executed", "gated_out"]
    assert result.steps[1].items == ()
    assert result.steps[1].condition is not None
    assert result.steps[1].condition.measured["max_negative_or_conflicting"] == 0


def test_chain_derives_product_and_aspect_from_the_previous_step(store_path: Path) -> None:
    pack = _pack(
        {
            "id": "derived_chain",
            "title": "先定位最严重的一项再诊断",
            "steps": [
                {
                    "step": 1,
                    "tool": "triage",
                    "products": ["alpha", "beta", "gamma"],
                    "aspects": ["*"],
                },
                {
                    "step": 2,
                    "tool": "diagnosis",
                    "products": ["$top_product"],
                    "aspects": ["$top_aspect"],
                    "when": {"min_strength": "WEAK", "min_negative_or_conflicting": 1},
                },
            ],
        }
    )

    (result,) = _run(store_path, pack)

    triage, diagnosis = result.steps
    top = max(
        triage.items,
        key=lambda item: (item.negative_or_conflicting, item.strength == "STRONG"),
    )
    assert diagnosis.status == "executed"
    assert diagnosis.products == ("beta",)
    assert diagnosis.aspects == ("noise",)
    assert top.product_id == "B002"
    assert top.aspect == "noise"


def test_scenario_run_repeats_identically(store_path: Path) -> None:
    pack = _pack(
        {
            "id": "replay_chain",
            "title": "同一条链跑两遍应该一样",
            "steps": [
                {
                    "step": 1,
                    "tool": "triage",
                    "products": ["alpha", "beta", "gamma"],
                    "aspects": ["*"],
                },
                {
                    "step": 2,
                    "tool": "diagnosis",
                    "products": ["$top_product"],
                    "aspects": ["$top_aspect"],
                    "when": {"min_negative_or_conflicting": 1},
                },
            ],
        }
    )

    first = scenario_run_document(
        pack,
        _run(store_path, pack),
        dataset_snapshot_id=SNAPSHOT,
        contract_sha256=CONTRACT,
    )
    second = scenario_run_document(
        pack,
        _run(store_path, pack),
        dataset_snapshot_id=SNAPSHOT,
        contract_sha256=CONTRACT,
    )

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert first["lineage"]["dataset_snapshot_id"] == SNAPSHOT
    assert first["lineage"]["contract_sha256"] == CONTRACT
    assert first["totals"]["executed_step_count"] == 2


def test_scenario_run_records_only_citable_evidence_ids(store_path: Path) -> None:
    pack = _pack(
        {
            "id": "citation_chain",
            "title": "记录下来的证据编号都要能核回原文行",
            "steps": [
                {"step": 1, "tool": "diagnosis", "products": ["beta"], "aspects": ["noise"]}
            ],
        }
    )

    (result,) = _run(store_path, pack)

    identifiers = [identifier for item in result.steps[0].items for identifier in item.evidence_ids]
    assert identifiers
    assert all(verify_citation(store_path, identifier) for identifier in identifiers)


def test_scenario_run_rejects_a_store_with_the_wrong_lineage(store_path: Path) -> None:
    pack = _pack(
        {
            "id": "lineage_chain",
            "title": "证据库对不上就不能跑",
            "steps": [{"step": 1, "tool": "diagnosis", "products": ["beta"], "aspects": ["noise"]}],
        }
    )

    with pytest.raises(ScenarioError, match="lineage"):
        _run(store_path, pack, contract="c" * 64)


def test_scenario_run_rejects_an_unknown_product_code(store_path: Path) -> None:
    pack = _pack(
        {
            "id": "unknown_chain",
            "title": "商品编号必须来自受控清单",
            "steps": [{"step": 1, "tool": "diagnosis", "products": ["delta"], "aspects": ["noise"]}],
        }
    )

    with pytest.raises(ScenarioError, match="unknown products"):
        _run(store_path, pack)


def test_shipped_operator_pack_is_valid_and_ordered() -> None:
    root = Path(__file__).resolve().parents[1]

    pack = load_pack(root / "artifacts" / "manifests" / "operator-scenarios-v1.json")

    assert pack.pack_id == "operator-presets-v1"
    assert [scenario.id for scenario in pack.scenarios] == [
        "noise_conflict_compare",
        "noise_guard_frostline",
        "worst_aspect_first",
    ]
    assert pack.scenarios[0].steps[0] == ScenarioStep(
        step=1, tool="diagnosis", products=("northstar",), aspects=("noise",)
    )


def test_load_pack_reports_unreadable_files(tmp_path: Path) -> None:
    with pytest.raises(ScenarioError, match="Unable to read"):
        load_pack(tmp_path / "missing.json")

    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    with pytest.raises(ScenarioError, match="Unable to read"):
        load_pack(broken)


def test_scenario_rejects_out_of_order_step_numbers() -> None:
    with pytest.raises(ValidationError):
        Scenario.model_validate(
            {
                "id": "bad_order",
                "title": "步号必须连续",
                "steps": [
                    {"step": 1, "tool": "diagnosis", "products": ["alpha"], "aspects": ["noise"]},
                    {
                        "step": 3,
                        "tool": "diagnosis",
                        "products": ["beta"],
                        "aspects": ["noise"],
                        "when": {},
                    },
                ],
            }
        )
