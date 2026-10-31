"""Tests for the opt-in environment override that redirects model calls to Ollama.

The frozen DeepSeek configuration is never edited here. Leaving the ECOM_MODEL_*
variables unset must keep today's requests unchanged; setting them is the only
way to reach the local Ollama endpoint.
"""

import json
from types import SimpleNamespace

import pytest

from ecom_review_intelligence.agent_contracts import AgentPlan
from ecom_review_intelligence.agent_insight import InsightDraft
from ecom_review_intelligence.model_gateway import (
    DEFAULT_OLLAMA_BASE_URL,
    DEFAULT_OLLAMA_MODEL,
    DeepSeekChatGateway,
    DeepSeekInsightGateway,
    DeepSeekModelGateway,
    GatewayError,
    RequestBudget,
    build_chat_gateway,
    build_insight_gateway,
)

CONFIG = {
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-v4-flash",
    "request_timeout_seconds": 30,
}
OLLAMA_ENV = {"ECOM_MODEL_PROVIDER": "ollama"}

PLAN = {
    "workflow": "diagnosis",
    "product_terms": ["PRODUCT_1"],
    "aspects": ["noise"],
}

INSIGHT = {
    "reviews": [
        {
            "evidence_alias": "R1",
            "relevant": True,
            "note": "买家提到夜间噪音",
            "quote": "晚上很吵，睡不着",
        }
    ],
    "supported": "有买家提到夜间噪音。",
    "supported_refs": ["R1"],
    "specifics": [],
    "not_supported": "只有一条样本，不能代表整体。",
}

CHAT = {
    "supported": "买家抱怨夜间噪音。",
    "review_points": [
        {"text": "夜间噪音明显。", "citation": "C1-E1", "quote": "晚上很吵"}
    ],
    "stats_points": [],
    "not_supported": "样本很少。",
}


class _Completions:
    def __init__(self) -> None:
        self.responses: list[object] = []
        self.requests: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.requests.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _fake_client() -> tuple[SimpleNamespace, _Completions]:
    completions = _Completions()
    client = SimpleNamespace(
        max_retries=0,
        base_url="https://api.deepseek.com",
        api_key="sk-deepseek",
        chat=SimpleNamespace(completions=completions),
    )
    return client, completions


def _tool_response(arguments: str, name: str) -> SimpleNamespace:
    call = SimpleNamespace(
        id="call-1",
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )
    message = SimpleNamespace(content=None, tool_calls=[call])
    usage = SimpleNamespace(prompt_tokens=11, completion_tokens=7)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=usage,
        model="returned-model",
        system_fingerprint=None,
    )


def _factory(client: object) -> tuple[object, dict[str, object]]:
    seen: dict[str, object] = {}

    def factory(**kwargs: object) -> object:
        seen.update(kwargs)
        return client

    return factory, seen


def test_ollama_insight_builder_uses_local_defaults_without_a_deepseek_key() -> None:
    factory, seen = _factory(_fake_client()[0])

    gateway = build_insight_gateway(CONFIG, client_factory=factory, environment=OLLAMA_ENV)

    assert gateway is not None
    assert seen == {
        "api_key": "ollama",
        "base_url": DEFAULT_OLLAMA_BASE_URL,
        "max_retries": 0,
    }
    assert gateway.model == DEFAULT_OLLAMA_MODEL
    assert gateway.provider == "ollama"


def test_ollama_chat_builder_uses_local_defaults_without_a_deepseek_key() -> None:
    factory, seen = _factory(_fake_client()[0])

    gateway = build_chat_gateway(CONFIG, client_factory=factory, environment=OLLAMA_ENV)

    assert gateway is not None
    assert seen == {
        "api_key": "ollama",
        "base_url": DEFAULT_OLLAMA_BASE_URL,
        "max_retries": 0,
    }
    assert gateway.model == DEFAULT_OLLAMA_MODEL
    assert gateway.provider == "ollama"


def test_ollama_builder_honours_explicit_environment_values() -> None:
    factory, seen = _factory(_fake_client()[0])
    environment = {
        "ECOM_MODEL_PROVIDER": " OLLAMA ",
        "ECOM_MODEL_BASE_URL": "http://127.0.0.1:18181/v1",
        "ECOM_MODEL_NAME": "qwen3:8b",
        "ECOM_MODEL_API_KEY": "local-key",
    }

    gateway = build_insight_gateway(CONFIG, client_factory=factory, environment=environment)

    assert gateway is not None
    assert seen == {
        "api_key": "local-key",
        "base_url": "http://127.0.0.1:18181/v1",
        "max_retries": 0,
    }
    assert gateway.model == "qwen3:8b"


def test_ollama_builder_ignores_a_visitor_selected_deepseek_model() -> None:
    factory, _seen = _factory(_fake_client()[0])

    gateway = build_insight_gateway(
        CONFIG,
        client_factory=factory,
        environment=OLLAMA_ENV,
        model="deepseek-v4-pro",
    )

    assert gateway is not None
    assert gateway.model == DEFAULT_OLLAMA_MODEL


def test_unset_override_keeps_the_frozen_deepseek_behaviour() -> None:
    client, completions = _fake_client()
    factory, seen = _factory(client)

    assert build_insight_gateway(CONFIG, client_factory=factory, environment={}) is None
    assert build_chat_gateway(CONFIG, client_factory=factory, environment={}) is None

    completions.responses.append(
        _tool_response(json.dumps(INSIGHT), "submit_insight")
    )
    gateway = build_insight_gateway(
        CONFIG,
        client_factory=factory,
        environment={"DEEPSEEK_API_KEY": "sk-visitor"},
    )

    assert gateway is not None
    assert gateway.provider == "deepseek"
    assert gateway.model == "deepseek-v4-flash"
    assert seen == {
        "api_key": "sk-visitor",
        "base_url": "https://api.deepseek.com",
        "max_retries": 0,
    }
    gateway.analyse({})
    request = completions.requests[0]
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in request


def test_deepseek_environment_still_overrides_base_url_and_model() -> None:
    factory, seen = _factory(_fake_client()[0])
    environment = {
        "DEEPSEEK_API_KEY": "sk-test",
        "ECOM_MODEL_BASE_URL": "https://proxy.example/v1",
        "ECOM_MODEL_NAME": "deepseek-other",
    }

    gateway = build_insight_gateway(CONFIG, client_factory=factory, environment=environment)

    assert gateway is not None
    assert gateway.provider == "deepseek"
    assert gateway.model == "deepseek-other"
    assert seen == {
        "api_key": "sk-test",
        "base_url": "https://proxy.example/v1",
        "max_retries": 0,
    }


def test_ollama_insight_request_drops_the_deepseek_thinking_flag() -> None:
    client, completions = _fake_client()
    completions.responses.append(_tool_response(json.dumps(INSIGHT), "submit_insight"))
    gateway = DeepSeekInsightGateway(client=client, model="qwen3:4b", provider="ollama")

    gateway.analyse({})

    request = completions.requests[0]
    assert "extra_body" not in request
    assert request["reasoning_effort"] == "none"


def test_thinking_block_is_stripped_before_insight_json_is_parsed() -> None:
    client, completions = _fake_client()
    arguments = "<thinking>先逐条读评论，再整理 JSON。</thinking>" + json.dumps(INSIGHT)
    completions.responses.append(_tool_response(arguments, "submit_insight"))
    gateway = DeepSeekInsightGateway(client=client, model="qwen3:4b", provider="ollama")

    draft = gateway.analyse({})

    assert isinstance(draft, InsightDraft)
    assert draft.supported == INSIGHT["supported"]


@pytest.mark.parametrize(
    "prefix",
    [
        "<thinking>reasoning</thinking>",
        "plain reasoning text</thinking>",
        "<THINKING>reasoning</THINKING>",
    ],
)
def test_the_last_closing_thinking_tag_bounds_the_payload(prefix: str) -> None:
    client, completions = _fake_client()
    completions.responses.append(_tool_response(prefix + json.dumps(INSIGHT), "submit_insight"))
    gateway = DeepSeekInsightGateway(client=client, model="qwen3:4b", provider="ollama")

    assert gateway.analyse({}).supported == INSIGHT["supported"]


def test_valid_json_that_mentions_a_thinking_tag_is_not_rewritten() -> None:
    client, completions = _fake_client()
    payload = dict(INSIGHT, supported="<thinking>keep this text</thinking>")
    completions.responses.append(_tool_response(json.dumps(payload), "submit_insight"))
    gateway = DeepSeekInsightGateway(client=client, model="qwen3:4b", provider="ollama")

    assert gateway.analyse({}).supported == "<thinking>keep this text</thinking>"


def test_thinking_block_is_stripped_before_agent_plan_json_is_parsed() -> None:
    client, completions = _fake_client()
    arguments = "<thinking>choose a workflow</thinking>" + json.dumps(PLAN)
    completions.responses.append(_tool_response(arguments, "submit_agent_plan"))
    gateway = DeepSeekModelGateway(
        client=client, budget=RequestBudget(3), environment=OLLAMA_ENV
    )

    plan = gateway.plan("noise?")

    assert plan == AgentPlan.model_validate(PLAN)
    assert gateway.model == DEFAULT_OLLAMA_MODEL
    assert gateway.provider == "ollama"
    assert client.base_url == DEFAULT_OLLAMA_BASE_URL
    assert client.api_key == "ollama"
    request = completions.requests[0]
    assert request["model"] == DEFAULT_OLLAMA_MODEL
    assert "extra_body" not in request
    assert request["reasoning_effort"] == "none"
    assert gateway.records[0].succeeded is True


def test_unset_override_model_gateway_sends_the_deepseek_flag() -> None:
    client, completions = _fake_client()
    completions.responses.append(_tool_response(json.dumps(PLAN), "submit_agent_plan"))
    gateway = DeepSeekModelGateway(client=client, budget=RequestBudget(3), environment={})

    gateway.plan("noise?")

    request = completions.requests[0]
    assert request["model"] == "deepseek-v4-flash"
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in request
    assert gateway.provider == "deepseek"
    assert client.base_url == "https://api.deepseek.com"


def test_thinking_block_is_stripped_before_chat_answer_json_is_parsed() -> None:
    client, completions = _fake_client()
    arguments = "<thinking>read the card first</thinking>" + json.dumps(CHAT)
    completions.responses.append(_tool_response(arguments, "submit_chat_answer"))
    gateway = DeepSeekChatGateway(client=client, model="qwen3:4b", provider="ollama")

    outcome = gateway.run_turn([], lambda name, raw: ({}, "tool", (), "ok"))

    assert outcome.draft.supported == CHAT["supported"]
    assert "extra_body" not in completions.requests[0]
    assert completions.requests[0]["reasoning_effort"] == "none"


def test_wrapped_tool_arguments_are_stripped_before_the_executor_runs() -> None:
    client, completions = _fake_client()
    completions.responses.append(
        _tool_response(
            "<thinking>need the card</thinking>" + json.dumps({"card_number": 1}),
            "read_card_reviews",
        )
    )
    completions.responses.append(
        _tool_response(json.dumps(CHAT), "submit_chat_answer")
    )
    gateway = DeepSeekChatGateway(client=client, model="qwen3:4b", provider="ollama")
    seen: list[tuple[str, str]] = []

    def executor(name: str, raw: str) -> tuple[dict[str, object], str, tuple[str, ...], str]:
        seen.append((name, raw))
        return {"ok": True}, "读取证据卡", ("1 条",), "ok"

    gateway.run_turn([], executor)

    assert json.loads(seen[0][1]) == {"card_number": 1}


def test_invalid_model_output_keeps_the_error_code_and_hides_the_raw_text() -> None:
    client, completions = _fake_client()
    completions.responses.append(
        _tool_response("<thinking>RAW-SECRET-TOKEN</thinking>{not-json", "submit_insight")
    )
    gateway = DeepSeekInsightGateway(client=client, model="qwen3:4b", provider="ollama")

    with pytest.raises(GatewayError) as captured:
        gateway.analyse({})

    assert captured.value.error_code == "MODEL_OUTPUT_INVALID"
    assert "RAW-SECRET-TOKEN" not in str(captured.value)
    assert "not-json" not in str(captured.value)


def test_unknown_provider_on_the_agent_gateway_keeps_the_client_untouched() -> None:
    client, _completions = _fake_client()

    with pytest.raises(ValueError) as captured:
        DeepSeekModelGateway(
            client=client,
            budget=RequestBudget(3),
            environment={"ECOM_MODEL_PROVIDER": "bogus-provider"},
        )

    assert "bogus-provider" not in str(captured.value)
    assert client.base_url == "https://api.deepseek.com"
    assert client.api_key == "sk-deepseek"


def test_unknown_provider_fails_closed_without_echoing_the_value() -> None:
    def factory(**_kwargs: object) -> object:
        raise AssertionError("no client may be built for an unknown provider")

    with pytest.raises(ValueError) as captured:
        build_insight_gateway(
            CONFIG,
            client_factory=factory,
            environment={"ECOM_MODEL_PROVIDER": "openai-proxy"},
        )

    assert "openai-proxy" not in str(captured.value)


def test_a_real_sdk_client_is_redirected_to_the_ollama_endpoint() -> None:
    httpx = pytest.importorskip("httpx")
    openai = pytest.importorskip("openai")
    seen: dict[str, str | None] = {}

    def handler(request: object) -> object:
        seen["url"] = str(request.url)  # type: ignore[attr-defined]
        seen["authorization"] = request.headers.get("authorization")  # type: ignore[attr-defined]
        body = {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 0,
            "model": "qwen3:4b",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "submit_agent_plan",
                                    "arguments": json.dumps(PLAN),
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {
                "prompt_tokens": 3,
                "completion_tokens": 4,
                "total_tokens": 7,
            },
        }
        return httpx.Response(200, json=body)

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = openai.OpenAI(
        api_key="sk-never-sent",
        base_url="https://api.deepseek.com",
        http_client=http_client,
        max_retries=0,
    )
    gateway = DeepSeekModelGateway(
        client=client, budget=RequestBudget(3), environment=OLLAMA_ENV
    )

    plan = gateway.plan("noise?")

    assert plan == AgentPlan.model_validate(PLAN)
    assert gateway.model == DEFAULT_OLLAMA_MODEL
    assert seen["url"] == DEFAULT_OLLAMA_BASE_URL + "/chat/completions"
    assert seen["authorization"] == "Bearer ollama"
