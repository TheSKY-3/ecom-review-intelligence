"""Unit tests for the bounded chat tool loop and its builder."""

import json
from types import SimpleNamespace

import pytest

from ecom_review_intelligence.agent_chat import ChatAnswerDraft
from ecom_review_intelligence.model_gateway import (
    DeepSeekChatGateway,
    GatewayError,
    build_chat_gateway,
)

DRAFT = {
    "supported": "买家抱怨夜间噪音。",
    "review_points": [],
    "stats_points": [],
    "not_supported": "样本很少,不能推断普遍程度。",
}


def _call(name, arguments, call_id="call-1"):
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(
            name=name,
            arguments=(
                arguments
                if isinstance(arguments, str)
                else json.dumps(arguments, ensure_ascii=False)
            ),
        ),
    )


def _response(*calls, content=None):
    message = SimpleNamespace(content=content, tool_calls=list(calls))
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class _Completions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _client(responses):
    completions = _Completions(responses)
    client = SimpleNamespace(
        max_retries=0,
        chat=SimpleNamespace(completions=completions),
    )
    return client, completions


def _gateway(responses, **kwargs):
    client, completions = _client(responses)
    return DeepSeekChatGateway(client=client, model="deepseek-test", **kwargs), completions


def test_turn_runs_a_tool_then_submits_the_answer():
    gateway, completions = _gateway(
        [
            _response(_call("read_card_reviews", {"card_number": 1})),
            _response(_call("submit_chat_answer", DRAFT, "call-2")),
        ]
    )
    seen = []

    def execute(name, arguments):
        seen.append((name, arguments))
        return {"card": 1}, "读取证据卡 1", ("C1 · 商品 A", "5 条评论原文"), "ok"

    outcome = gateway.run_turn(
        [{"role": "user", "content": "晚上噪音怎么样?"}], execute
    )

    assert isinstance(outcome.draft, ChatAnswerDraft)
    assert outcome.draft.supported == DRAFT["supported"]
    assert outcome.model_calls == 2
    assert [step.title for step in outcome.steps] == ["读取证据卡 1", "提交回答"]
    first_step = outcome.steps[0]
    assert first_step.tool == "read_card_reviews"
    assert first_step.round == 1
    assert first_step.status == "ok"
    assert first_step.detail == ("C1 · 商品 A", "5 条评论原文")
    assert first_step.duration_ms >= 1
    assert outcome.steps[1].round == 2
    assert outcome.steps[1].detail == ("引用 0 条评论", "统计 0 项")
    assert seen == [("read_card_reviews", '{"card_number": 1}')]

    first = completions.requests[0]
    assert first["model"] == "deepseek-test"
    assert first["tool_choice"] == "auto"
    assert first["stream"] is False
    assert first["temperature"] == 0
    assert first["extra_body"] == {"thinking": {"type": "disabled"}}
    assert first["messages"][0]["role"] == "system"
    tool_names = [item["function"]["name"] for item in first["tools"]]
    assert tool_names == [
        "read_card_reviews",
        "query_run_stats",
        "create_run",
        "submit_chat_answer",
    ]

    tool_messages = [
        item for item in completions.requests[1]["messages"] if item.get("role") == "tool"
    ]
    assert tool_messages == [
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "content": json.dumps({"card": 1}, ensure_ascii=False, sort_keys=True),
        }
    ]


def test_submit_as_the_first_call_needs_no_tool_round():
    gateway, completions = _gateway([_response(_call("submit_chat_answer", DRAFT))])

    outcome = gateway.run_turn([], lambda name, arguments: ({}, "unused", (), "ok"))

    assert outcome.model_calls == 1
    assert [step.title for step in outcome.steps] == ["提交回答"]
    assert len(completions.requests) == 1


def test_text_only_reply_fails_closed():
    gateway, _ = _gateway([_response(content="I think the noise is bad.")])

    with pytest.raises(GatewayError) as error:
        gateway.run_turn([], lambda name, arguments: ({}, "unused", (), "ok"))

    assert error.value.error_code == "MODEL_OUTPUT_INVALID"


def test_unparseable_submit_arguments_fail_closed():
    gateway, _ = _gateway([_response(_call("submit_chat_answer", "{not json"))])

    with pytest.raises(GatewayError) as error:
        gateway.run_turn([], lambda name, arguments: ({}, "unused", (), "ok"))

    assert error.value.error_code == "MODEL_OUTPUT_INVALID"


def test_submit_missing_a_required_field_fails_closed():
    gateway, _ = _gateway(
        [_response(_call("submit_chat_answer", {"supported": "只有结论", "not_supported": ""}))]
    )

    with pytest.raises(GatewayError) as error:
        gateway.run_turn([], lambda name, arguments: ({}, "unused", (), "ok"))

    assert error.value.error_code == "MODEL_OUTPUT_INVALID"


def test_missing_submit_exhausts_the_round_budget():
    gateway, _ = _gateway(
        [
            _response(_call("read_card_reviews", {"card_number": 1})),
            _response(_call("read_card_reviews", {"card_number": 1})),
        ],
        max_tool_rounds=1,
    )

    with pytest.raises(GatewayError) as error:
        gateway.run_turn([], lambda name, arguments: ({"card": 1}, "读卡", (), "ok"))

    assert error.value.error_code == "MODEL_ROUNDS_EXCEEDED"
    assert len(error.value.steps) == 2, "executed steps survive the failed turn"


def test_provider_failure_becomes_api_unavailable():
    gateway, _ = _gateway([RuntimeError("connection reset")])

    with pytest.raises(GatewayError) as error:
        gateway.run_turn([], lambda name, arguments: ({}, "unused", (), "ok"))

    assert error.value.error_code == "API_UNAVAILABLE"


def test_constructor_rejects_automatic_retries():
    client, _ = _client([])
    client.max_retries = 2

    with pytest.raises(ValueError):
        DeepSeekChatGateway(client=client, model="deepseek-test")


def test_build_chat_gateway_without_a_key_returns_none():
    config = {"base_url": "https://api.deepseek.com", "model": "deepseek-test"}

    assert build_chat_gateway(config, environment={}) is None


def test_build_chat_gateway_passes_the_key_and_disables_retries():
    seen = {}

    def factory(**kwargs):
        seen.update(kwargs)
        client, _ = _client([])
        return client

    gateway = build_chat_gateway(
        {
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-test",
            "request_timeout_seconds": 12,
        },
        client_factory=factory,
        environment={"DEEPSEEK_API_KEY": "sk-test"},
    )

    assert gateway is not None
    assert gateway.model == "deepseek-test"
    assert gateway.request_timeout_seconds == 12
    assert gateway.max_output_tokens == 1200
    assert seen == {
        "api_key": "sk-test",
        "base_url": "https://api.deepseek.com",
        "max_retries": 0,
    }


class _ProviderError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__("provider failure")
        self.status_code = status_code


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, "MODEL_REQUEST_REJECTED"),
        (401, "KEY_REJECTED"),
        (403, "KEY_REJECTED"),
        (429, "MODEL_RATE_LIMITED"),
        (500, "API_UNAVAILABLE"),
    ],
)
def test_provider_status_is_mapped_to_a_safe_error_code(status, code):
    gateway, _completions = _gateway([_ProviderError(status)])

    with pytest.raises(GatewayError) as error:
        gateway.run_turn([], lambda name, arguments: ({}, "unused"))

    assert error.value.error_code == code
    assert "provider failure" not in str(error.value)


def test_build_chat_gateway_honours_a_model_override():
    def factory(**kwargs):
        client, _ = _client([])
        return client

    gateway = build_chat_gateway(
        {"base_url": "https://api.deepseek.com", "model": "deepseek-test"},
        client_factory=factory,
        environment={"DEEPSEEK_API_KEY": "sk-test"},
        model="deepseek-other",
    )

    assert gateway is not None
    assert gateway.model == "deepseek-other"


def test_build_chat_gateway_falls_back_when_the_override_is_blank():
    def factory(**kwargs):
        client, _ = _client([])
        return client

    gateway = build_chat_gateway(
        {"base_url": "https://api.deepseek.com", "model": "deepseek-test"},
        client_factory=factory,
        environment={"DEEPSEEK_API_KEY": "sk-test"},
        model="   ",
    )

    assert gateway is not None
    assert gateway.model == "deepseek-test"
