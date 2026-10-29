"""Deterministic operator scenario chains over the frozen local evidence store.

A scenario is a declared, short sequence of evidence-tool steps in which every
step after the first runs only when the previous step's own measurements say it
should. The chain is data, not a model decision: the same pack against the same
frozen store produces the same steps, the same stop code, and the same evidence
IDs, so a preset is replayable and auditable.

What the chain may decide is bounded on purpose:

* A step declares its tool, product codes, and aspects; a later step may name
  only the two derived tokens this module recognises (``$top_product`` and
  ``$top_aspect``), which resolve to the previous step's highest-ranked item.
* A step's gate is a closed-enum condition over measured counts, never free
  text and never a model judgement.
* Steps are capped, and the cap is handed to the shared tool ledger so a chain
  cannot outspend a single run by construction.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ecom_review_intelligence.agent_contracts import EvidenceStrength
from ecom_review_intelligence.agent_tools import (
    AgentToolError,
    EvidenceToolService,
    LocalEvidenceItem,
    ToolInvocationLedger,
    WorkflowToolResult,
)

PACK_SCHEMA_VERSION = "p7-operator-scenarios-v1"
RUN_SCHEMA_VERSION = "p7-scenario-run-v1"

TOOL_BY_WORKFLOW: dict[str, str] = {
    "diagnosis": "diagnose_product",
    "comparison": "compare_products",
    "triage": "triage_issues",
}
TOP_PRODUCT = "$top_product"
TOP_ASPECT = "$top_aspect"
ALL_ASPECTS = "*"
MAX_STEPS = 3
STRENGTH_RANK = {"INSUFFICIENT": 0, "WEAK": 1, "MODERATE": 2, "STRONG": 3}


class ScenarioError(RuntimeError):
    """Raised when a scenario pack or an executed chain violates its contract."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class StepCondition(_StrictModel):
    """Closed-enum gate over the previous step's measurements."""

    min_strength: EvidenceStrength | None = None
    min_negative_or_conflicting: int = Field(default=0, ge=0)
    require: Literal["any_item", "every_item"] = "any_item"


class ScenarioStep(_StrictModel):
    step: int = Field(ge=1, le=MAX_STEPS)
    tool: Literal["diagnosis", "comparison", "triage"]
    products: tuple[str, ...] = Field(min_length=1, max_length=5)
    aspects: tuple[str, ...] = Field(min_length=1, max_length=8)
    when: StepCondition | None = None

    @model_validator(mode="after")
    def _check_shape(self) -> ScenarioStep:
        derived = [value for value in self.products if value.startswith("$")]
        if derived:
            if derived != [TOP_PRODUCT] or len(self.products) != 1:
                raise ValueError(f"The only derived product token is {TOP_PRODUCT}.")
            if self.tool != "diagnosis":
                raise ValueError(f"{TOP_PRODUCT} is only valid for a diagnosis step.")
        if self.tool == "diagnosis" and len(self.products) != 1:
            raise ValueError("A diagnosis step requires exactly one product.")
        if self.tool == "comparison" and not 2 <= len(self.products) <= 5:
            raise ValueError("A comparison step requires two to five products.")
        if not any(value == ALL_ASPECTS for value in self.aspects):
            if len(set(self.aspects)) != len(self.aspects):
                raise ValueError("Step aspects must be unique.")
        elif len(self.aspects) != 1:
            raise ValueError(f"{ALL_ASPECTS} must be the only entry in aspects.")
        return self


class Scenario(_StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,48}$")
    title: str = Field(min_length=2, max_length=80)
    steps: tuple[ScenarioStep, ...] = Field(min_length=1, max_length=MAX_STEPS)

    @model_validator(mode="after")
    def _check_chain(self) -> Scenario:
        numbers = [step.step for step in self.steps]
        if numbers != list(range(1, len(self.steps) + 1)):
            raise ValueError("Step numbers must be 1..n in order.")
        if self.steps[0].when is not None:
            raise ValueError("The first step always runs and cannot declare a gate.")
        for step in self.steps[1:]:
            if step.when is None:
                raise ValueError("Every step after the first must declare a gate.")
        return self


class ScenarioPack(_StrictModel):
    schema_version: Literal["p7-operator-scenarios-v1"] = PACK_SCHEMA_VERSION
    pack_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,48}$")
    scenarios: tuple[Scenario, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def _check_unique(self) -> ScenarioPack:
        ids = [scenario.id for scenario in self.scenarios]
        if len(set(ids)) != len(ids):
            raise ValueError("Scenario ids must be unique.")
        return self


@dataclass(frozen=True)
class StepMeasurement:
    """One product-and-aspect row as measured by the executed tool."""

    product_id: str
    aspect: str
    strength: str
    supporting: int
    opposing: int
    conflicting: int
    insufficient: int
    evidence_ids: tuple[str, ...]

    @property
    def evidence_count(self) -> int:
        return len(self.evidence_ids)

    @property
    def negative_or_conflicting(self) -> int:
        return self.opposing + self.conflicting

    def as_document(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "aspect": self.aspect,
            "strength": self.strength,
            "evidence_count": self.evidence_count,
            "supporting": self.supporting,
            "opposing": self.opposing,
            "conflicting": self.conflicting,
            "insufficient": self.insufficient,
            "negative_or_conflicting": self.negative_or_conflicting,
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True)
class ConditionOutcome:
    """A gate decision together with the numbers that produced it."""

    satisfied: bool
    rule: dict[str, Any]
    measured: dict[str, Any]

    def as_document(self) -> dict[str, Any]:
        return {
            "satisfied": self.satisfied,
            "rule": self.rule,
            "measured": self.measured,
        }


@dataclass(frozen=True)
class ScenarioStepResult:
    step: int
    tool: str
    status: Literal["executed", "gated_out"]
    products: tuple[str, ...]
    aspects: tuple[str, ...]
    condition: ConditionOutcome | None
    items: tuple[StepMeasurement, ...]

    def as_document(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "tool": self.tool,
            "status": self.status,
            "products": list(self.products),
            "aspects": list(self.aspects),
            "condition": None if self.condition is None else self.condition.as_document(),
            "items": [item.as_document() for item in self.items],
        }


@dataclass(frozen=True)
class ScenarioResult:
    scenario_id: str
    title: str
    stop_code: str
    steps: tuple[ScenarioStepResult, ...]

    def as_document(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "title": self.title,
            "stop_code": self.stop_code,
            "steps": [step.as_document() for step in self.steps],
        }


def load_pack(path: Path) -> ScenarioPack:
    """Read and validate one scenario pack from disk."""

    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ScenarioError(f"Unable to read the scenario pack: {error}") from error
    try:
        return ScenarioPack.model_validate(payload)
    except ValueError as error:
        raise ScenarioError(f"The scenario pack is invalid: {error}") from error


def enabled_aspects(store_path: Path) -> tuple[str, ...]:
    """The frozen aspect vocabulary this store was built with, sorted for replay."""

    try:
        with sqlite3.connect(f"file:{Path(store_path).as_posix()}?mode=ro", uri=True) as handle:
            row = handle.execute(
                "SELECT value FROM store_meta WHERE key = 'aspect_terms'"
            ).fetchone()
    except sqlite3.Error as error:
        raise ScenarioError("Unable to read the local evidence store metadata.") from error
    if row is None:
        raise ScenarioError("The local evidence store has no aspect vocabulary.")
    return tuple(sorted(json.loads(row[0])))


def evaluate_condition(
    condition: StepCondition, items: tuple[StepMeasurement, ...]
) -> ConditionOutcome:
    """Apply one closed-enum gate to the previous step's measurements."""

    threshold = (
        None if condition.min_strength is None else STRENGTH_RANK[condition.min_strength.value]
    )

    def passes(item: StepMeasurement) -> bool:
        if threshold is not None and STRENGTH_RANK.get(item.strength, 0) < threshold:
            return False
        return item.negative_or_conflicting >= condition.min_negative_or_conflicting

    hits = tuple(item for item in items if passes(item))
    if condition.require == "any_item":
        satisfied = bool(hits)
    else:
        satisfied = bool(items) and len(hits) == len(items)
    rule = {
        "min_strength": None if condition.min_strength is None else condition.min_strength.value,
        "min_negative_or_conflicting": condition.min_negative_or_conflicting,
        "require": condition.require,
    }
    measured = {
        "item_count": len(items),
        "passing_item_count": len(hits),
        "max_negative_or_conflicting": max(
            (item.negative_or_conflicting for item in items), default=0
        ),
        "strengths": sorted({item.strength for item in items}),
    }
    return ConditionOutcome(satisfied=satisfied, rule=rule, measured=measured)


def _measure(items: tuple[LocalEvidenceItem, ...]) -> tuple[StepMeasurement, ...]:
    measurements: list[StepMeasurement] = []
    for item in items:
        counts = {"supporting": 0, "opposing": 0, "conflicting": 0, "insufficient": 0}
        for evidence in item.evidence:
            if evidence.polarity in counts:
                counts[evidence.polarity] += 1
        measurements.append(
            StepMeasurement(
                product_id=item.product_id,
                aspect=item.aspect,
                strength=item.strength,
                supporting=counts["supporting"],
                opposing=counts["opposing"],
                conflicting=counts["conflicting"],
                insufficient=counts["insufficient"],
                evidence_ids=tuple(evidence.evidence_id for evidence in item.evidence),
            )
        )
    return tuple(measurements)


def _top_item(items: tuple[StepMeasurement, ...]) -> StepMeasurement | None:
    """The highest-ranked item, using the same ordering the triage tool applies."""

    if not items:
        return None
    return min(
        items,
        key=lambda item: (
            -STRENGTH_RANK.get(item.strength, 0),
            -item.negative_or_conflicting,
            -item.evidence_count,
            item.aspect,
            item.product_id,
        ),
    )


def _resolve_products(
    step: ScenarioStep,
    *,
    product_ids_by_code: dict[str, str],
    code_by_product_id: dict[str, str],
    previous: ScenarioStepResult | None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Turn a step's declared products into store IDs plus the codes we report."""

    if step.products == (TOP_PRODUCT,):
        top = None if previous is None else _top_item(previous.items)
        if top is None:
            raise ScenarioError(f"Step {step.step} has no previous measurement to derive from.")
        code = code_by_product_id.get(top.product_id, top.product_id)
        return (top.product_id,), (code,)
    unknown = [code for code in step.products if code not in product_ids_by_code]
    if unknown:
        raise ScenarioError(f"Step {step.step} names unknown products: {unknown}.")
    return tuple(product_ids_by_code[code] for code in step.products), step.products


def _resolve_aspects(
    step: ScenarioStep,
    *,
    all_aspects: tuple[str, ...],
    previous: ScenarioStepResult | None,
) -> tuple[str, ...]:
    if step.aspects == (ALL_ASPECTS,):
        return all_aspects
    if step.aspects == (TOP_ASPECT,):
        top = None if previous is None else _top_item(previous.items)
        if top is None:
            raise ScenarioError(f"Step {step.step} has no previous measurement to derive from.")
        return (top.aspect,)
    return step.aspects


def _execute(
    step: ScenarioStep, service: EvidenceToolService, product_ids: tuple[str, ...], aspects: tuple[str, ...]
) -> WorkflowToolResult:
    if step.tool == "diagnosis":
        return service.diagnose_product(product_ids[0], aspects)
    if step.tool == "comparison":
        return service.compare_products(product_ids, aspects)
    return service.triage_issues(product_ids, aspects)


def run_scenario(
    scenario: Scenario,
    *,
    store_path: Path,
    expected_snapshot_id: str,
    expected_contract_sha256: str,
    product_ids_by_code: dict[str, str],
    all_aspects: tuple[str, ...],
    retrieval_limit: int = 5,
) -> ScenarioResult:
    """Execute one declared chain; never calls a model."""

    if len(scenario.steps) > MAX_STEPS:
        raise ScenarioError(f"A chain may not exceed {MAX_STEPS} steps.")
    service = EvidenceToolService(
        store_path,
        expected_snapshot_id=expected_snapshot_id,
        expected_contract_sha256=expected_contract_sha256,
        retrieval_limit=retrieval_limit,
        ledger=ToolInvocationLedger(max_executions=len(scenario.steps)),
    )
    code_by_product_id = {value: key for key, value in product_ids_by_code.items()}
    executed: list[ScenarioStepResult] = []
    stop_code = "COMPLETED"
    for step in scenario.steps:
        previous = executed[-1] if executed else None
        if step.when is not None:
            assert previous is not None
            outcome = evaluate_condition(step.when, previous.items)
            if not outcome.satisfied:
                executed.append(
                    ScenarioStepResult(
                        step=step.step,
                        tool=step.tool,
                        status="gated_out",
                        products=step.products,
                        aspects=step.aspects,
                        condition=outcome,
                        items=(),
                    )
                )
                stop_code = "CONDITION_NOT_MET"
                break
        else:
            outcome = None
        product_ids, product_codes = _resolve_products(
            step,
            product_ids_by_code=product_ids_by_code,
            code_by_product_id=code_by_product_id,
            previous=previous,
        )
        aspects = _resolve_aspects(step, all_aspects=all_aspects, previous=previous)
        try:
            result = _execute(step, service, product_ids, aspects)
        except AgentToolError as error:
            raise ScenarioError(f"Step {step.step} of {scenario.id} failed: {error}") from error
        executed.append(
            ScenarioStepResult(
                step=step.step,
                tool=step.tool,
                status="executed",
                products=product_codes,
                aspects=aspects,
                condition=outcome,
                items=_measure(result.items),
            )
        )
    return ScenarioResult(
        scenario_id=scenario.id,
        title=scenario.title,
        stop_code=stop_code,
        steps=tuple(executed),
    )


def run_pack(
    pack: ScenarioPack,
    *,
    store_path: Path,
    expected_snapshot_id: str,
    expected_contract_sha256: str,
    product_ids_by_code: dict[str, str],
    retrieval_limit: int = 5,
) -> tuple[ScenarioResult, ...]:
    """Execute every scenario in one pack against the same frozen store."""

    aspects = enabled_aspects(store_path)
    return tuple(
        run_scenario(
            scenario,
            store_path=store_path,
            expected_snapshot_id=expected_snapshot_id,
            expected_contract_sha256=expected_contract_sha256,
            product_ids_by_code=product_ids_by_code,
            all_aspects=aspects,
            retrieval_limit=retrieval_limit,
        )
        for scenario in pack.scenarios
    )


def scenario_run_document(
    pack: ScenarioPack,
    results: tuple[ScenarioResult, ...],
    *,
    dataset_snapshot_id: str,
    contract_sha256: str,
    retrieval_limit: int = 5,
) -> dict[str, Any]:
    """Self-describing artifact: it exports alone and carries its own lineage."""

    return {
        "schema_version": RUN_SCHEMA_VERSION,
        "pack_id": pack.pack_id,
        "pack_schema_version": pack.schema_version,
        "lineage": {
            "dataset_snapshot_id": dataset_snapshot_id,
            "contract_sha256": contract_sha256,
            "retrieval_limit_per_aspect": retrieval_limit,
            "max_steps_per_chain": MAX_STEPS,
        },
        "scenarios": [result.as_document() for result in results],
        "totals": {
            "scenario_count": len(results),
            "executed_step_count": sum(
                1 for result in results for step in result.steps if step.status == "executed"
            ),
            "gated_out_step_count": sum(
                1 for result in results for step in result.steps if step.status == "gated_out"
            ),
            "stop_codes": {
                code: sum(1 for result in results if result.stop_code == code)
                for code in sorted({result.stop_code for result in results})
            },
        },
    }
