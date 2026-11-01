"""Zero-dependency MCP (Model Context Protocol) server over stdio.

Exposes the deterministic evidence layer — product catalog, per-product aspect
statistics, comparison, triage, and the category baseline — to any MCP client
(Claude Desktop, other agents). The model-facing discipline is unchanged: a
tool result carries counts, product titles and local codes, and aspect names
only. Review text, evidence IDs, parent ASINs, and local paths never leave
this process, so every field a client sees is already on the public-safe list.

Only the stdio transport and the three methods a tool server needs are
implemented (initialize, tools/list, tools/call) plus ping; that surface is
small enough to hold in one auditable file, which is why this file does not
depend on the official SDK.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from ecom_review_intelligence.agent_tools import (
    AgentToolError,
    EvidenceToolService,
    ToolInvocationLedger,
)
from ecom_review_intelligence.catalog import CatalogEntry, catalog_tier_totals, load_catalog
from ecom_review_intelligence.engine import (
    EngineError,
    Evidence,
    category_aspect_totals,
    count_aspect_matches,
    query_aspect_evidence,
    query_stratified_aspect,
    read_store_meta,
)

SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
LATEST_PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "ecom-review-intelligence"
SERVER_VERSION = "0.4.0"

_MAX_LIST_LIMIT = 50
_MAX_TRIAGE_ASPECTS = 8

_SERVER_INSTRUCTIONS = (
    "Deterministic e-commerce review evidence tools over a frozen local store. "
    "Results contain counts and product titles only; review text never leaves "
    "the host machine. Resolve products with list_products first."
)

_JSONRPC = "2.0"
_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602


class McpToolError(RuntimeError):
    """A tool call could not produce evidence, e.g. an unknown product code."""


def _direction_counts(items: Iterable[Evidence]) -> dict[str, int]:
    counts = {"supporting": 0, "opposing": 0, "conflicting": 0, "insufficient": 0}
    for item in items:
        counts[item.polarity] += 1
    return counts


class McpEvidenceServer:
    """JSON-RPC 2.0 handler backed by the frozen local evidence store."""

    def __init__(
        self,
        *,
        store_path: Path,
        snapshot_id: str,
        contract_sha256: str,
        catalog_limit: int | None = None,
    ) -> None:
        self.store_path = Path(store_path)
        self.snapshot_id = snapshot_id
        self.contract_sha256 = contract_sha256
        self.entries: tuple[CatalogEntry, ...] = load_catalog(
            self.store_path, limit=catalog_limit or 2**32
        )
        self._aspects: tuple[str, ...] = ()
        metadata = read_store_meta(self.store_path)
        aspects = json.loads(metadata["aspect_terms"])
        self._aspects = tuple(sorted(aspects))

    # ------------------------------------------------------------------ tools

    @property
    def aspect_names(self) -> tuple[str, ...]:
        """The frozen aspect vocabulary this store was built with."""

        return self._aspects

    def tool_definitions(self) -> list[dict[str, object]]:
        aspect_enum = list(self._aspects)
        return [
            {
                "name": "list_products",
                "description": (
                    "List catalog products (local codes, titles, review counts, "
                    "coverage tier). Search by title substring or filter by tier."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Title substring filter."},
                        "tier": {"type": "string", "enum": ["A", "B", "C"]},
                        "limit": {"type": "integer", "minimum": 1, "maximum": _MAX_LIST_LIMIT},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "diagnose_product",
                "description": (
                    "Evidence statistics for one product and one aspect: matched "
                    "count, rating-stratified sample directions, full lexicon "
                    "screen, and the blind-spot count. Counts only, no review text."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "product_code": {"type": "string"},
                        "aspect": {"type": "string", "enum": aspect_enum},
                    },
                    "required": ["product_code", "aspect"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "compare_products",
                "description": (
                    "The same per-product aspect statistics for two to five "
                    "products, for side-by-side comparison."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "product_codes": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 2,
                            "maxItems": 5,
                            "uniqueItems": True,
                        },
                        "aspect": {"type": "string", "enum": aspect_enum},
                    },
                    "required": ["product_codes", "aspect"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "triage_issues",
                "description": (
                    "Rank product/aspect pairs by evidence strength and "
                    "negative-or-conflicting sample counts: what to verify first."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "product_codes": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                            "maxItems": 5,
                            "uniqueItems": True,
                        },
                        "aspects": {
                            "type": "array",
                            "items": {"type": "string", "enum": aspect_enum},
                            "maxItems": _MAX_TRIAGE_ASPECTS,
                            "uniqueItems": True,
                        },
                    },
                    "required": ["product_codes"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "category_baseline",
                "description": (
                    "Whole-category aspect totals over every stored review: "
                    "coverage, direction counts, and catalog tier sizes. The "
                    "background rate a single product must be read against."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
        ]

    def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        if name == "list_products":
            return self._list_products(arguments)
        if name == "diagnose_product":
            return self._diagnose(str(arguments.get("product_code") or ""), arguments)
        if name == "compare_products":
            return self._compare(arguments)
        if name == "triage_issues":
            return self._triage(arguments)
        if name == "category_baseline":
            return self._category_baseline()
        raise McpToolError(f"Unknown tool: {name}")

    def _list_products(self, arguments: dict[str, object]) -> dict[str, object]:
        query = str(arguments.get("query") or "").strip().lower()
        tier = arguments.get("tier")
        limit_value = arguments.get("limit", 20)
        try:
            limit = int(limit_value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise McpToolError("limit must be an integer.") from None
        if not 1 <= limit <= _MAX_LIST_LIMIT:
            raise McpToolError(f"limit must be between 1 and {_MAX_LIST_LIMIT}.")
        if tier is not None and tier not in {"A", "B", "C"}:
            raise McpToolError("tier must be one of A, B, C.")

        matched = []
        for entry in self.entries:
            if tier is not None and entry.tier != tier:
                continue
            if query and query not in entry.title.lower():
                continue
            matched.append(entry)
        matched = matched[:limit]
        return {
            "total_in_catalog": len(self.entries),
            "returned": len(matched),
            "products": [
                {
                    "product_code": entry.code,
                    "title": entry.title,
                    "review_count": entry.review_count,
                    "tier": entry.tier,
                }
                for entry in matched
            ],
        }

    def _resolve_code(self, product_code: str) -> tuple[str, str]:
        for entry in self.entries:
            if entry.code == product_code:
                return entry.parent_asin, entry.title
        raise McpToolError(
            f"Unknown product code: {product_code}. Resolve codes with list_products first."
        )

    def _product_stats(self, product_code: str, aspect: str) -> dict[str, object]:
        parent_asin, title = self._resolve_code(product_code)
        matched_count = count_aspect_matches(self.store_path, (parent_asin,), aspect)
        sample = query_stratified_aspect(self.store_path, (parent_asin,), aspect, limit=5)
        full = query_aspect_evidence(self.store_path, (parent_asin,), aspect)
        full_counts = _direction_counts(full)
        low_star = sum(
            1
            for item in full
            if item.polarity == "insufficient" and item.rating is not None and item.rating <= 2
        )
        return {
            "product_code": product_code,
            "product_title": title,
            "aspect": aspect,
            "matched_count": matched_count,
            "sample": {
                "size": len(sample.evidence),
                "strength": sample.strength,
                "directions": _direction_counts(sample.evidence),
            },
            "full_lexicon_screen": {
                "supporting": full_counts["supporting"],
                "opposing": full_counts["opposing"],
                "conflicting": full_counts["conflicting"],
                "insufficient": full_counts["insufficient"],
            },
            "blind_spot": {
                "insufficient": full_counts["insufficient"],
                "low_star": low_star,
            },
            "notes": (
                "The sample is a rating-stratified reading aid, not a statistical "
                "conclusion; the full-lexicon screen counts word-list directions "
                "over every matched review without verification."
            ),
        }

    def _diagnose(self, product_code: str, arguments: dict[str, object]) -> dict[str, object]:
        aspect = str(arguments.get("aspect") or "")
        self._service().diagnose_product(self._resolve_code(product_code)[0], (aspect,))
        return self._product_stats(product_code, aspect)

    def _compare(self, arguments: dict[str, object]) -> dict[str, object]:
        codes = self._code_list(arguments, "product_codes")
        aspect = str(arguments.get("aspect") or "")
        asins = tuple(self._resolve_code(code)[0] for code in codes)
        self._service().compare_products(asins, (aspect,))
        return {
            "workflow": "comparison",
            "aspect": aspect,
            "products": [self._product_stats(code, aspect) for code in codes],
        }

    def _triage(self, arguments: dict[str, object]) -> dict[str, object]:
        codes = self._code_list(arguments, "product_codes")
        aspects_value = arguments.get("aspects")
        aspects = (
            tuple(str(item) for item in aspects_value)  # type: ignore[union-attr]
            if aspects_value
            else self._aspects
        )
        if not aspects:
            raise McpToolError("No aspects are enabled by the frozen contract.")
        code_by_asin = {self._resolve_code(code)[0]: code for code in codes}
        result = self._service().triage_issues(tuple(code_by_asin), aspects)
        return {
            "workflow": "triage",
            "items": [
                {
                    "product_code": code_by_asin[item.product_id],
                    "aspect": item.aspect,
                    "strength": item.strength,
                    "negative_or_conflicting_count": item.negative_or_conflicting_count,
                    "sample_size": len(item.evidence),
                }
                for item in result.items
            ],
        }

    def _category_baseline(self) -> dict[str, object]:
        totals = category_aspect_totals(self.store_path)
        return {
            "review_total": totals["review_total"],
            "tiers": catalog_tier_totals(self.store_path),
            "aspects": totals["aspects"],
        }

    # ------------------------------------------------------------- validation

    def _service(self) -> EvidenceToolService:
        """A fresh service per call: one business tool execution per request."""

        return EvidenceToolService(
            self.store_path,
            expected_snapshot_id=self.snapshot_id,
            expected_contract_sha256=self.contract_sha256,
            ledger=ToolInvocationLedger(max_executions=1),
        )

    def _code_list(self, arguments: dict[str, object], key: str) -> list[str]:
        value = arguments.get(key)
        if not isinstance(value, list) or not value:
            raise McpToolError(f"{key} must be a non-empty array of product codes.")
        codes = [str(item) for item in value]
        if len(set(codes)) != len(codes):
            raise McpToolError("Product codes must be unique.")
        return codes

    # --------------------------------------------------------------- protocol

    def handle_message(self, message: object) -> dict[str, object] | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != _JSONRPC:
            return self._error(None, _INVALID_REQUEST, "Invalid Request")
        method = message.get("method")
        if not isinstance(method, str):
            return self._error(None, _INVALID_REQUEST, "Invalid Request")
        is_notification = "id" not in message
        message_id = message.get("id")

        if method == "initialize":
            requested = str((message.get("params") or {}).get("protocolVersion") or "")
            version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else LATEST_PROTOCOL_VERSION
            return {
                "jsonrpc": _JSONRPC,
                "id": message_id,
                "result": {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                    "instructions": _SERVER_INSTRUCTIONS,
                },
            }
        if method == "ping":
            return {"jsonrpc": _JSONRPC, "id": message_id, "result": {}}
        if method == "tools/list":
            return {
                "jsonrpc": _JSONRPC,
                "id": message_id,
                "result": {"tools": self.tool_definitions()},
            }
        if method == "tools/call":
            return self._handle_tool_call(message_id, message.get("params") or {})
        if is_notification:
            return None  # unknown notifications (initialized, cancelled, ...) are ignored
        return self._error(message_id, _METHOD_NOT_FOUND, f"Method not found: {method}")

    def _handle_tool_call(
        self, message_id: object, params: dict[str, object]
    ) -> dict[str, object]:
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return self._error(message_id, _INVALID_PARAMS, "tools/call requires name and arguments.")
        try:
            payload = self.call_tool(name, arguments)
        except (McpToolError, AgentToolError, EngineError, ValueError, KeyError) as error:
            return {
                "jsonrpc": _JSONRPC,
                "id": message_id,
                "result": {
                    "content": [{"type": "text", "text": str(error)}],
                    "isError": True,
                },
            }
        return {
            "jsonrpc": _JSONRPC,
            "id": message_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    }
                ],
                "isError": False,
            },
        }

    def _error(self, message_id: object, code: int, message: str) -> dict[str, object]:
        return {
            "jsonrpc": _JSONRPC,
            "id": message_id,
            "error": {"code": code, "message": message},
        }

    def serve(self, stdin, stdout) -> None:
        """Read newline-delimited JSON-RPC requests, write one line per response."""

        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                response = self._error(None, _PARSE_ERROR, "Parse error")
            else:
                response = self.handle_message(message)
            if response is not None:
                stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                stdout.flush()
