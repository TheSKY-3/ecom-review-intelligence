"""Parsing and prompt assembly for the optional local-model bridge.

The bridge itself runs under a different interpreter (the checkpoint's torch
stack), so these tests cover the protocol pieces that must stay correct
without a GPU: turning the checkpoint's tool-call text into OpenAI-shaped calls
and passing tools through the chat template.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "serve_local_model.py"


def _module():
    spec = importlib.util.spec_from_file_location("serve_local_model", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def server():
    return _module()


def test_tool_call_text_becomes_an_openai_tool_call(server):
    text = (
        "我先核对引用。\n"
        "<tool_call>\n<function=submit_answer>\n"
        "<parameter=quote>\nIt's pretty noisy.\n</parameter>\n"
        "<parameter=count>\n3\n</parameter>\n"
        "</function>\n</tool_call>"
    )

    content, calls = server.parse_tool_calls(text)

    assert content == "我先核对引用。"
    assert len(calls) == 1
    call = calls[0]
    assert call["type"] == "function"
    assert call["function"]["name"] == "submit_answer"
    assert json.loads(call["function"]["arguments"]) == {
        "quote": "It's pretty noisy.",
        "count": 3,
    }


def test_two_tool_calls_keep_their_order_and_identifiers(server):
    text = (
        "<tool_call>\n<function=first>\n<parameter=question>\na?\n</parameter>\n"
        "</function>\n</tool_call>\n"
        "<tool_call>\n<function=second>\n<parameter=product_number>\n4\n</parameter>\n"
        "</function>\n</tool_call>"
    )

    content, calls = server.parse_tool_calls(text)

    assert content == ""
    assert [call["function"]["name"] for call in calls] == ["first", "second"]
    assert [call["id"] for call in calls] == ["call_1", "call_2"]
    assert json.loads(calls[1]["function"]["arguments"]) == {"product_number": 4}


def test_unterminated_tool_call_yields_no_call_and_no_content(server):
    # A reply cut off at the token budget must not be passed off as an answer.
    text = "<tool_call>\n<function=submit>\n<parameter=quote>\ntruncated"

    content, calls = server.parse_tool_calls(text)

    assert calls == []
    assert content == ""


def test_build_prompt_passes_tools_and_adds_the_quote_rule(server, monkeypatch):
    captured: dict[str, object] = {}

    class FakeTokenizer:
        def apply_chat_template(self, messages, **kwargs):
            captured["messages"] = messages
            captured["kwargs"] = kwargs
            return "prompt"

    monkeypatch.setattr(server, "_TOKENIZER", FakeTokenizer())
    tools = [{"type": "function", "function": {"name": "submit_insight"}}]

    prompt = server.build_prompt(
        [{"role": "system", "content": "系统"}],
        tools,
        {"type": "function", "function": {"name": "submit_insight"}},
    )

    assert prompt == "prompt"
    assert captured["kwargs"]["tools"] == tools
    assert captured["kwargs"]["enable_thinking"] is False
    system = captured["messages"][0]["content"]
    assert "必须调用函数 submit_insight" in system
    assert "逐字" in system
    assert "JSON Schema" in system


def test_complete_shapes_the_openai_response(server, monkeypatch):
    class FakeTokenizer:
        def apply_chat_template(self, messages, **kwargs):
            return "prompt"

        def __call__(self, prompt, return_tensors):
            return {"input_ids": SimpleNamespace(shape=(1, 5))}

        def decode(self, tokens, skip_special_tokens):
            return "答案<tool_call><function=f><parameter=a>1</parameter></function></tool_call>"

    class FakeTensor:
        shape = (1, 12)

        def __getitem__(self, item):
            if isinstance(item, tuple) and isinstance(item[0], int):
                return FakeTensor()
            return [7] * 12

        def __len__(self):
            return 12

    class FakeModel:
        def generate(self, **kwargs):
            return FakeTensor()

    torch = pytest.importorskip("torch")
    monkeypatch.setattr(server, "_TOKENIZER", FakeTokenizer())
    monkeypatch.setattr(server, "_MODEL", FakeModel())
    monkeypatch.setattr(server, "_MODEL_NAME", "fake")
    monkeypatch.setattr(torch, "inference_mode", lambda: __import__("contextlib").nullcontext())

    result = server.complete(
        {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 64}
    )

    assert result["model"] == "fake"
    message = result["choices"][0]["message"]
    assert result["choices"][0]["finish_reason"] == "tool_calls"
    assert message["content"] == "答案"
    assert message["tool_calls"][0]["function"]["arguments"] == {"a": 1}
