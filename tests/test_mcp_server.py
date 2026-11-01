"""MCP server: protocol handshake, evidence tools, and the no-text boundary."""

from __future__ import annotations

import io
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from ecom_review_intelligence.engine import compute_evidence_id, initialize_store
from ecom_review_intelligence.mcp_server import McpEvidenceServer

SNAPSHOT = "a" * 64
CONTRACT = "b" * 64

_INIT_REQUEST = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "protocol-test", "version": "0"},
    },
}


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
    products = (
        ("B001", "Alpha Ice Quiet"),
        ("B002", "Alpha Ice Pro"),
        ("B003", "Beta Countertop"),
    )
    reviews = (
        (1, "B001", "quiet good", 5.0),
        (2, "B001", "noise loud", 1.0),
        (3, "B001", "broke failed", 1.0),
        (4, "B002", "loud bad", 1.0),
        (5, "B002", "reliable good", 5.0),
        (6, "B003", "quiet loud", 3.0),
    )
    with sqlite3.connect(path) as connection:
        connection.executemany("INSERT INTO products(parent_asin, title) VALUES (?, ?)", products)
        connection.executemany(
            """
            INSERT INTO reviews(
                evidence_id, source_line, parent_asin, text, rating, timestamp,
                verified_purchase, helpful_vote
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    compute_evidence_id(SNAPSHOT, line),
                    line,
                    product,
                    text,
                    rating,
                    1,
                    1,
                    0,
                )
                for line, product, text, rating in reviews
            ],
        )
    return path


@pytest.fixture
def server(store_path: Path) -> McpEvidenceServer:
    return McpEvidenceServer(store_path=store_path, snapshot_id=SNAPSHOT, contract_sha256=CONTRACT)


def _request(message_id: int, name: str, arguments: dict[str, object]) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": message_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def _result_text(server: McpEvidenceServer, request: dict[str, object]) -> tuple[object, bool]:
    response = server.handle_message(request)
    assert response is not None and "result" in response
    result = response["result"]
    text = result["content"][0]["text"]
    try:
        return json.loads(text), bool(result["isError"])
    except ValueError:
        return text, bool(result["isError"])


def test_initialize_echoes_supported_version_and_advertises_tools(server) -> None:
    response = server.handle_message(_INIT_REQUEST)

    assert response is not None and "result" in response
    result = response["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["serverInfo"]["name"] == "ecom-review-intelligence"

    downgrade = server.handle_message({**_INIT_REQUEST, "params": {"protocolVersion": "2024-11-05"}})
    assert downgrade is not None and downgrade["result"]["protocolVersion"] == "2024-11-05"

    unknown = server.handle_message({**_INIT_REQUEST, "params": {"protocolVersion": "2099-01-01"}})
    assert unknown is not None and unknown["result"]["protocolVersion"] == "2025-06-18"


def test_notifications_silence_unknown_methods_and_ping(server) -> None:
    assert server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert server.handle_message({"jsonrpc": "2.0", "method": "notifications/cancelled"}) is None
    assert server.handle_message({"jsonrpc": "2.0", "method": "resources/list", "id": 9})[
        "error"
    ]["code"] == -32601
    assert server.handle_message({"jsonrpc": "2.0", "id": 10, "method": "ping"})["result"] == {}
    assert server.handle_message({"jsonrpc": "1.0", "id": 11, "method": "ping"})["error"][
        "code"
    ] == -32600


def test_tools_list_exposes_five_documented_tools(server) -> None:
    response = server.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})

    tools = response["result"]["tools"]  # type: ignore[index]
    assert [tool["name"] for tool in tools] == [
        "list_products",
        "diagnose_product",
        "compare_products",
        "triage_issues",
        "category_baseline",
    ]
    for tool in tools:
        assert tool["description"]
        assert tool["inputSchema"]["type"] == "object"


def test_list_products_never_leaks_asins(server) -> None:
    payload, is_error = _result_text(
        server, _request(3, "list_products", {"query": "alpha"})
    )

    assert is_error is False
    assert payload["returned"] == 2
    titles = json.dumps(payload, ensure_ascii=False)
    assert "Alpha Ice Quiet" in titles
    assert "B001" not in titles, "parent ASIN must never leave the process"


def test_diagnose_reports_counts_without_review_text(server) -> None:
    payload, is_error = _result_text(
        server, _request(4, "diagnose_product", {"product_code": "frostline", "aspect": "noise"})
    )

    assert is_error is False
    assert payload["matched_count"] == 2
    assert payload["sample"]["size"] == 2
    assert payload["sample"]["directions"] == {
        "supporting": 1,
        "opposing": 1,
        "conflicting": 0,
        "insufficient": 0,
    }
    assert payload["full_lexicon_screen"]["opposing"] == 1
    assert payload["blind_spot"]["insufficient"] == 0
    body = json.dumps(payload, ensure_ascii=False)
    assert "quiet good" not in body, "review text must never leave the process"
    assert "B001" not in body


def test_compare_and_triage_cover_the_two_workflows(server) -> None:
    compare, compare_error = _result_text(
        server,
        _request(
            5,
            "compare_products",
            {"product_codes": ["frostline", "northstar"], "aspect": "noise"},
        ),
    )
    assert compare_error is False
    assert [item["product_code"] for item in compare["products"]] == ["frostline", "northstar"]

    triage, triage_error = _result_text(
        server,
        _request(
            6,
            "triage_issues",
            {"product_codes": ["frostline", "northstar"], "aspects": ["noise", "reliability"]},
        ),
    )
    assert triage_error is False
    assert triage["items"], "at least one negative-or-conflicting pair must be retained"
    for item in triage["items"]:
        assert item["negative_or_conflicting_count"] > 0
        assert set(item) == {
            "product_code",
            "aspect",
            "strength",
            "negative_or_conflicting_count",
            "sample_size",
        }


def test_tool_errors_come_back_as_is_error_results(server) -> None:
    unknown_tool, unknown_is_error = _result_text(server, _request(7, "no_such_tool", {}))
    assert unknown_is_error is True and "Unknown tool" in str(unknown_tool)

    unknown_code, code_is_error = _result_text(
        server, _request(8, "diagnose_product", {"product_code": "p9999", "aspect": "noise"})
    )
    assert code_is_error is True and "Unknown product code" in str(unknown_code)

    _bad_aspect, aspect_is_error = _result_text(
        server, _request(9, "diagnose_product", {"product_code": "frostline", "aspect": "price"})
    )
    assert aspect_is_error is True


def test_stdio_round_trip_is_framing_tolerant(server) -> None:
    lines = [
        json.dumps(_INIT_REQUEST),
        "not json at all",
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        json.dumps(_request(3, "list_products", {})),
        json.dumps({"jsonrpc": "2.0", "id": 4, "method": "ping"}),
        json.dumps({"jsonrpc": "2.0", "id": 5, "method": "no_such_method"}),
    ]
    stdout = io.StringIO()
    server.serve(iter(lines), stdout)

    responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [response.get("id") for response in responses] == [1, None, 2, 3, 4, 5]
    assert responses[1]["error"]["code"] == -32700
    assert responses[-1]["error"]["code"] == -32601
    assert responses[3]["result"]["isError"] is False


def test_script_fails_closed_without_a_local_store(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_mcp_server.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--project-root", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert proc.returncode == 2
    payload = json.loads(proc.stderr.strip().splitlines()[-1])
    assert payload["status"] == "FAILED"
    assert "unavailable" in payload["reason"]


def test_real_store_smoke_matches_frozen_catalog() -> None:
    root = Path(__file__).resolve().parents[1]
    store = root / "data" / "processed" / "ice_makers_evidence.sqlite"
    if not store.exists():
        pytest.skip("local evidence store is not present on this machine")

    server = McpEvidenceServer(
        store_path=store,
        snapshot_id=SNAPSHOT,
        contract_sha256=CONTRACT,
    )
    assert len(server.aspect_names) == 8
    payload, is_error = _result_text(server, _request(1, "category_baseline", {}))
    assert is_error is False
    assert payload["review_total"] == 136604
    assert payload["tiers"] == {"A": 244, "B": 566, "C": 2468}
