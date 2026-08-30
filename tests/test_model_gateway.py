import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ecom_review_intelligence.agent_contracts import AgentDraft, AgentPlan, SafeEvidenceCard
from ecom_review_intelligence.model_gateway import (
    DeepSeekModelGateway,
    FakeModelGateway,
    GatewayError,
    ReplayModelGateway,
    RequestBudget,
)


def _plan() -> AgentPlan:
    return AgentPlan(
        workflow="diagnosis",
        product_terms=("PRODUCT_1",),
        aspects=("noise",),
    )


def _draft() -> AgentDraft:
    return AgentDraft(
        workflow="diagnosis",
        selected_product_aliases=("PRODUCT_1",),
        selected_aspects=("noise",),
        claims=(
            {
                "claim_type": "aspect_summary",
                "product_aliases": ("PRODUCT_1",),
                "aspect": "noise",
                "polarity": "opposing",
                "evidence_strength": "WEAK",
                "safe_evidence_card_refs": ("CARD_1",),
            },
        ),
        limitation_codes=("REVIEW_EVIDENCE_ONLY",),
    )


def _card() -> SafeEvidenceCard:
    return SafeEvidenceCard(
        card_alias="CARD_1",
        product_alias="PRODUCT_1",
        aspect="noise",
        evidence_strength="WEAK",
        supporting_count=0,
        opposing_count=1,
        conflicting_count=0,
        insufficient_count=0,
        returned_evidence_count=1,
        quality_limitation_codes=("SILVER_LABELS",),
    )


def test_request_budget_counts_attempts_and_fails_before_fourth_request() -> None:
    budget = RequestBudget(max_requests=3)
    budget.consume()
    budget.consume()
    budget.consume()

    assert budget.used == 3
    with pytest.raises(GatewayError, match="budget") as captured:
        budget.consume()
    assert captured.value.error_code == "MODEL_BUDGET_EXCEEDED"


def test_fake_gateway_returns_typed_scripted_values() -> None:
    gateway = FakeModelGateway(plans=[_plan()], drafts=[_draft()])

    assert gateway.plan("safe question") == _plan()
    assert gateway.draft(_plan(), (_card(),)) == _draft()
    assert gateway.call_kinds == ("plan", "draft")


def test_replay_gateway_requires_reviewed_redacted_hash_valid_fixture(tmp_path: Path) -> None:
    payload = {
        "schema_version": "p6-replay-v1",
        "reviewed_redacted": True,
        "interactions": [
            {"kind": "plan", "value": _plan().model_dump(mode="json")},
            {"kind": "draft", "value": _draft().model_dump(mode="json")},
        ],
    }
    fixture = tmp_path / "replay.json"
    ReplayModelGateway.write_reviewed_fixture(fixture, payload)

    gateway = ReplayModelGateway.from_path(fixture)

    assert gateway.plan("safe question") == _plan()
    assert gateway.draft(_plan(), (_card(),)) == _draft()

    unsafe = dict(payload)
    unsafe["interactions"] = [{"kind": "plan", "value": {"local_path": "D:\\raw.txt"}}]
    unsafe_path = tmp_path / "unsafe.json"
    ReplayModelGateway.write_reviewed_fixture(unsafe_path, unsafe)
    with pytest.raises(GatewayError, match="prohibited"):
        ReplayModelGateway.from_path(unsafe_path)


def test_committed_replay_fixture_is_reviewed_and_runnable() -> None:
    fixture = Path(__file__).parent / "fixtures" / "p6_model_replay.json"
    gateway = ReplayModelGateway.from_path(fixture)

    assert gateway.plan("synthetic question") == _plan()
    assert gateway.draft(_plan(), (_card(),)) == _draft()


class _CaptureCompletions:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.requests: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.requests.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _CaptureClient:
    def __init__(self, responses: list[object]) -> None:
        self.max_retries = 0
        self.chat = SimpleNamespace(completions=_CaptureCompletions(responses))


def _response(
    *,
    tool_arguments: dict[str, object] | str | None = None,
    tool_name: str = "submit_agent_plan",
    content: str | None = None,
    cache_hit_tokens: int | None = None,
    cache_miss_tokens: int | None = None,
):
    tool_calls = None
    if tool_arguments is not None:
        arguments = (
            tool_arguments if isinstance(tool_arguments, str) else json.dumps(tool_arguments)
        )
        tool_calls = [
            SimpleNamespace(
                function=SimpleNamespace(
                    name=tool_name,
                    arguments=arguments,
                )
            )
        ]
    message = SimpleNamespace(tool_calls=tool_calls, content=content)
    usage = SimpleNamespace(
        prompt_tokens=12,
        completion_tokens=4,
        prompt_cache_hit_tokens=cache_hit_tokens,
        prompt_cache_miss_tokens=cache_miss_tokens,
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=usage,
        model="deepseek-v4-flash",
        system_fingerprint="fp-safe",
    )


def test_deepseek_plan_uses_one_forced_non_thinking_native_tool() -> None:
    client = _CaptureClient([_response(tool_arguments=_plan().model_dump(mode="json"))])
    gateway = DeepSeekModelGateway(client=client, budget=RequestBudget(3))

    result = gateway.plan("Diagnose PRODUCT_1 for noise")

    assert result == _plan()
    request = client.chat.completions.requests[0]
    assert request["model"] == "deepseek-v4-flash"
    assert request["stream"] is False
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}
    assert [tool["function"]["name"] for tool in request["tools"]] == ["submit_agent_plan"]
    assert request["tool_choice"]["function"]["name"] == "submit_agent_plan"


def test_deepseek_records_cache_hit_miss_and_output_usage() -> None:
    client = _CaptureClient(
        [
            _response(
                tool_arguments=_plan().model_dump(mode="json"),
                cache_hit_tokens=5,
                cache_miss_tokens=7,
            )
        ]
    )
    gateway = DeepSeekModelGateway(client=client, budget=RequestBudget(3))

    gateway.plan("Diagnose PRODUCT_1 for noise")

    [record] = gateway.records
    assert record.kind == "plan"
    assert record.model == "deepseek-v4-flash"
    assert record.system_fingerprint == "fp-safe"
    assert record.input_tokens == 12
    assert record.cache_hit_input_tokens == 5
    assert record.cache_miss_input_tokens == 7
    assert record.output_tokens == 4
    assert record.latency_ms >= 0


def test_deepseek_classifies_input_as_cache_miss_when_breakdown_is_absent() -> None:
    client = _CaptureClient([_response(tool_arguments=_plan().model_dump(mode="json"))])
    gateway = DeepSeekModelGateway(client=client, budget=RequestBudget(3))

    gateway.plan("Diagnose PRODUCT_1 for noise")

    [record] = gateway.records
    assert record.cache_hit_input_tokens == 0
    assert record.cache_miss_input_tokens == record.input_tokens == 12


def test_deepseek_rejects_inconsistent_cache_usage() -> None:
    client = _CaptureClient(
        [
            _response(
                tool_arguments=_plan().model_dump(mode="json"),
                cache_hit_tokens=5,
                cache_miss_tokens=8,
            )
        ]
    )
    gateway = DeepSeekModelGateway(client=client, budget=RequestBudget(3))

    with pytest.raises(GatewayError) as captured:
        gateway.plan("Diagnose PRODUCT_1 for noise")

    assert captured.value.error_code == "MODEL_OUTPUT_INVALID"
    [record] = gateway.records
    assert record.succeeded is False
    assert record.error_code == "MODEL_OUTPUT_INVALID"
    assert record.cache_hit_input_tokens == 0
    assert record.cache_miss_input_tokens == record.input_tokens == 12


def test_deepseek_draft_serializes_only_safe_cards_and_uses_forced_native_tool() -> None:
    client = _CaptureClient(
        [
            _response(
                tool_arguments=_draft().model_dump(mode="json"),
                tool_name="submit_agent_draft",
            )
        ]
    )
    gateway = DeepSeekModelGateway(client=client, budget=RequestBudget(3))

    result = gateway.draft(_plan(), (_card(),), validation_errors=("UNKNOWN_CARD",))

    assert result == _draft()
    request = client.chat.completions.requests[0]
    serialized = json.dumps(request)
    assert [tool["function"]["name"] for tool in request["tools"]] == [
        "submit_agent_draft"
    ]
    assert request["tool_choice"]["function"]["name"] == "submit_agent_draft"
    assert "response_format" not in request
    parameters = request["tools"][0]["function"]["parameters"]
    claim_variant = parameters["properties"]["claims"]["items"]["anyOf"][0]
    assert claim_variant["properties"]["product_aliases"]["items"]["enum"] == [
        "PRODUCT_1"
    ]
    assert claim_variant["properties"]["aspect"]["enum"] == ["noise"]
    assert claim_variant["properties"]["evidence_strength"]["enum"] == ["WEAK"]
    assert claim_variant["properties"]["polarity"]["enum"] == ["opposing"]
    assert claim_variant["properties"]["safe_evidence_card_refs"]["items"][
        "enum"
    ] == ["CARD_1"]
    system_prompt = request["messages"][0]["content"]
    assert "exactly one claim per safe evidence card" in system_prompt
    assert (
        "copy card_alias, product_alias, aspect, and evidence_strength verbatim"
        in system_prompt
    )
    assert "keep claims in safe evidence card order" in system_prompt
    assert "polarity count is greater than zero" in system_prompt
    safe_payload = json.loads(request["messages"][1]["content"])
    assert safe_payload["safe_evidence_cards"][0]["allowed_polarities"] == [
        "opposing"
    ]
    assert safe_payload["safe_evidence_cards"][0]["required_polarity"] == "opposing"
    assert "CARD_1" in serialized
    assert "PRODUCT_1" in serialized
    assert "evidence_id" not in serialized
    assert "parent_asin" not in serialized
    assert "review_text" not in serialized


def test_deepseek_draft_rejects_empty_cards_before_http() -> None:
    client = _CaptureClient([])
    gateway = DeepSeekModelGateway(client=client, budget=RequestBudget(3))

    with pytest.raises(GatewayError) as captured:
        gateway.draft(_plan(), ())

    assert captured.value.error_code == "MODEL_OUTPUT_INVALID"
    assert client.chat.completions.requests == []


def test_deepseek_draft_schema_handles_mixed_evidenced_and_empty_cards() -> None:
    empty = SafeEvidenceCard(
        card_alias="CARD_2",
        product_alias="PRODUCT_1",
        aspect="reliability",
        evidence_strength="INSUFFICIENT",
        supporting_count=0,
        opposing_count=0,
        conflicting_count=0,
        insufficient_count=0,
        returned_evidence_count=0,
        quality_limitation_codes=("INSUFFICIENT_EVIDENCE",),
    )
    plan = AgentPlan(
        workflow="diagnosis",
        product_terms=("PRODUCT_1",),
        aspects=("noise", "reliability"),
    )
    draft = AgentDraft(
        workflow="diagnosis",
        selected_product_aliases=("PRODUCT_1",),
        selected_aspects=("noise", "reliability"),
        claims=(
            _draft().claims[0],
            {
                "claim_type": "insufficient_evidence",
                "product_aliases": ("PRODUCT_1",),
                "aspect": "reliability",
                "polarity": "insufficient",
                "evidence_strength": "INSUFFICIENT",
                "safe_evidence_card_refs": (),
            },
        ),
        limitation_codes=("REVIEW_EVIDENCE_ONLY",),
    )
    client = _CaptureClient(
        [
            _response(
                tool_arguments=draft.model_dump(mode="json"),
                tool_name="submit_agent_draft",
            )
        ]
    )
    gateway = DeepSeekModelGateway(client=client, budget=RequestBudget(3))

    assert gateway.draft(plan, (_card(), empty)) == draft
    request = client.chat.completions.requests[0]
    variants = request["tools"][0]["function"]["parameters"]["properties"][
        "claims"
    ]["items"]["anyOf"]
    assert variants[0]["properties"]["safe_evidence_card_refs"]["minItems"] == 1
    assert variants[1]["properties"]["safe_evidence_card_refs"]["maxItems"] == 0
    payload = json.loads(request["messages"][1]["content"])
    assert payload["safe_evidence_cards"][1]["required_polarity"] == "insufficient"


def test_failed_http_attempt_consumes_budget_and_maps_safe_error() -> None:
    client = _CaptureClient([RuntimeError("authorization header and provider body")])
    budget = RequestBudget(3)
    gateway = DeepSeekModelGateway(client=client, budget=budget)

    with pytest.raises(GatewayError) as captured:
        gateway.plan("safe question")

    assert captured.value.error_code == "API_UNAVAILABLE"
    assert "authorization" not in str(captured.value).lower()
    assert budget.used == 1
    [record] = gateway.records
    assert record.succeeded is False
    assert record.error_code == "API_UNAVAILABLE"
    assert record.input_tokens == record.output_tokens == 0


def test_invalid_model_json_is_rejected_locally() -> None:
    client = _CaptureClient(
        [_response(tool_arguments="not-json", tool_name="submit_agent_draft")]
    )
    gateway = DeepSeekModelGateway(client=client, budget=RequestBudget(3))

    with pytest.raises(GatewayError) as captured:
        gateway.draft(_plan(), (_card(),))

    assert captured.value.error_code == "MODEL_OUTPUT_INVALID"


def test_draft_rejects_provider_echo_of_safe_input_payload() -> None:
    echoed = json.dumps(
        {
            "plan": _plan().model_dump(mode="json"),
            "safe_evidence_cards": [_card().model_dump(mode="json")],
            "validation_errors": [],
        }
    )
    client = _CaptureClient([_response(content=echoed)])
    gateway = DeepSeekModelGateway(client=client, budget=RequestBudget(3))

    with pytest.raises(GatewayError) as captured:
        gateway.draft(_plan(), (_card(),))

    assert captured.value.error_code == "MODEL_OUTPUT_INVALID"
