"""Serve a local Qwen3.5 checkpoint through the OpenAI chat-completions API.

The workbench already speaks the OpenAI protocol and can be pointed at any
endpoint with the ECOM_MODEL_* environment variables, so this file only has to
translate one JSON request into one generation and translate the tool-call
text back. It runs under a separate interpreter because it needs the
checkpoint's own torch/transformers stack (see the soil-model project); the
workbench itself never imports torch.

    <checkpoint-venv>/python scripts/serve_local_model.py \\
        --model-path E:/models/Qwen3.5-2B --port 11435 --name qwen3.5-2b

Then start the workbench with:

    ECOM_MODEL_PROVIDER=ollama
    ECOM_MODEL_BASE_URL=http://127.0.0.1:11435/v1
    ECOM_MODEL_NAME=qwen3.5-2b
    ECOM_MODEL_API_KEY=local

Deliberately not a general-purpose server: no streaming, no batching, greedy
decoding only, one request at a time. That is enough for the workbench's two
short, deterministic model steps.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

# torch/transformers are imported inside main() and complete() so the parsing
# helpers stay importable under the workbench's own environment, which has no
# GPU stack. Only the server process needs the checkpoint runtime.

TOOL_BLOCK = re.compile(
    r"<tool_call>\s*<function=([^>\s]+)>\s*(.*?)</function>\s*</tool_call>", re.DOTALL
)
PARAMETER = re.compile(r"<parameter=([^>\s]+)>\s*(.*?)\s*</parameter>", re.DOTALL)
THINKING = re.compile(r"<think>.*?</think>", re.DOTALL)

_LOCK = threading.Lock()
_DUMP_DIR = os.environ.get("ECOM_LOCAL_DUMP_DIR", "")
_DUMP_SEQUENCE = 0
_MODEL_NAME = "local-model"
_TOKENIZER: Any = None
_MODEL: Any = None
_MAX_NEW_TOKENS = 1200


def _coerce(value: str) -> Any:
    text = value.strip()
    if not text:
        return ""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def parse_tool_calls(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Split the reply into visible content and OpenAI-shaped tool calls."""

    first = text.find("<tool_call>")
    content = (text[:first] if first >= 0 else text).strip()
    calls: list[dict[str, Any]] = []
    for index, match in enumerate(TOOL_BLOCK.finditer(text), start=1):
        arguments = {
            name: _coerce(value) for name, value in PARAMETER.findall(match.group(2))
        }
        calls.append(
            {
                "id": f"call_{index}",
                "type": "function",
                "function": {
                    "name": match.group(1).strip(),
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            }
        )
    return content, calls


def build_prompt(messages: list[dict[str, Any]], tools: Any, tool_choice: Any) -> str:
    prepared = [dict(message) for message in messages]
    forced = (
        isinstance(tool_choice, dict)
        and tool_choice.get("type") == "function"
        and isinstance(tool_choice.get("function"), dict)
    )
    hints: list[str] = []
    if forced:
        name = tool_choice["function"].get("name", "")
        hints.append(f"必须调用函数 {name}；除了这次函数调用不要输出任何其他内容。")
    if tools:
        hints.append(
            "调用函数时：quote 只能是原文中逐字出现的最短片段（不超过 60 个字符），"
            "不要引用整段评论；note 不超过 40 个字。参数必须严格符合该函数 parameters 的 "
            "JSON Schema：只能用 schema 里声明的字段名，数组元素的字段名也必须与 schema 完全一致，"
            "不得新增或改名任何字段。"
        )
    if hints:
        hint = "\n\n".join(hints)
        if prepared and prepared[0].get("role") == "system":
            prepared[0]["content"] = f"{prepared[0]['content']}\n\n{hint}"
        else:
            prepared.insert(0, {"role": "system", "content": hint})
    kwargs: dict[str, Any] = {"tokenize": False, "add_generation_prompt": True}
    if tools:
        kwargs["tools"] = tools
    try:
        prepared_template = _TOKENIZER.apply_chat_template(
            prepared, enable_thinking=False, **kwargs
        )
    except TypeError:
        prepared_template = _TOKENIZER.apply_chat_template(prepared, **kwargs)
    return str(prepared_template)


def complete(payload: dict[str, Any]) -> dict[str, Any]:
    messages = payload.get("messages") or []
    tools = payload.get("tools")
    prompt = build_prompt(messages, tools, payload.get("tool_choice"))
    requested = payload.get("max_tokens") or _MAX_NEW_TOKENS
    budget = max(16, min(int(requested), _MAX_NEW_TOKENS))
    inputs = _TOKENIZER(prompt, return_tensors="pt").to("cuda")
    import torch

    started = time.perf_counter()
    with _LOCK, torch.inference_mode():
        generated = _MODEL.generate(
            **inputs, max_new_tokens=budget, do_sample=False, use_cache=True
        )
    new_tokens = generated[0][inputs["input_ids"].shape[1] :]
    reply = THINKING.sub("", _TOKENIZER.decode(new_tokens, skip_special_tokens=True))
    content, calls = parse_tool_calls(reply)
    if _DUMP_DIR:
        _dump(payload, prompt, reply, content, calls)
    elapsed = time.perf_counter() - started
    print(
        f"[local-model] {len(inputs['input_ids'][0])} in / {len(new_tokens)} out "
        f"/ {elapsed:.1f}s / {len(calls)} tool call(s)",
        file=sys.stderr,
        flush=True,
    )
    message: dict[str, Any] = {"role": "assistant", "content": content or None}
    if calls:
        message["tool_calls"] = calls
    return {
        "id": "chatcmpl-local",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": _MODEL_NAME,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if calls else "stop",
            }
        ],
        "usage": {
            "prompt_tokens": int(inputs["input_ids"].shape[1]),
            "completion_tokens": len(new_tokens),
            "total_tokens": int(inputs["input_ids"].shape[1] + len(new_tokens)),
        },
    }


def _dump(
    payload: dict[str, Any],
    prompt: str,
    reply: str,
    content: str,
    calls: list[dict[str, Any]],
) -> None:
    """Write one request/response pair for offline inspection (debug only)."""

    global _DUMP_SEQUENCE
    _DUMP_SEQUENCE += 1
    directory = Path(_DUMP_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    record = {
        "request": payload,
        "prompt": prompt,
        "reply": reply,
        "content": content,
        "tool_calls": calls,
    }
    (directory / f"{int(time.time())}-{_DUMP_SEQUENCE:03d}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8"
    )


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalModel/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[local-model] {format % args}", file=sys.stderr, flush=True)

    def _send(self, status: int, body: dict[str, Any]) -> None:
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        if self.path in ("/health", "/v1/health"):
            self._send(200, {"status": "ok", "model": _MODEL_NAME})
        elif self.path == "/v1/models":
            self._send(200, {"object": "list", "data": [{"id": _MODEL_NAME}]})
        else:
            self._send(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:
        if self.path not in ("/v1/chat/completions", "/chat/completions"):
            self._send(404, {"error": {"message": "not found"}})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            result = complete(payload)
        except Exception as error:  # noqa: BLE001 - the server must answer in one shape
            print(f"[local-model] failed: {type(error).__name__}: {error}", file=sys.stderr)
            self._send(500, {"error": {"message": "generation failed"}})
            return
        self._send(200, result)


def main() -> int:
    global _MODEL, _MODEL_NAME, _TOKENIZER, _MAX_NEW_TOKENS
    import torch
    from transformers import AutoModelForImageTextToText, AutoTokenizer

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--port", type=int, default=11435)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--name", default="local-model")
    parser.add_argument("--max-new-tokens", type=int, default=1200)
    args = parser.parse_args()

    _MODEL_NAME = args.name
    _MAX_NEW_TOKENS = args.max_new_tokens
    started = time.perf_counter()
    _TOKENIZER = AutoTokenizer.from_pretrained(args.model_path)
    _MODEL = AutoModelForImageTextToText.from_pretrained(
        args.model_path, dtype=torch.bfloat16, device_map="cuda:0"
    )
    _MODEL.eval()
    print(
        f"[local-model] {args.name} ready on http://{args.host}:{args.port} "
        f"in {time.perf_counter() - started:.1f}s",
        file=sys.stderr,
        flush=True,
    )
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
