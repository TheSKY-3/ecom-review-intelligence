"""Separate local-sensitive P6 traces from aggregate-safe public traces."""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from ecom_review_intelligence.agent_contracts import ErrorCode

DEFAULT_LOCAL_TRACE_PATH = Path("data/processed/p6_agent_traces.jsonl")
_SCHEMA_VERSION = "p6-agent-trace-v1"
_PUBLIC_FIELDS = {
    "graph_path",
    "node_outcomes",
    "safe_error_codes",
    "counts",
    "latency_ms",
    "model_id",
    "config_id",
    "verifier_result",
    "snapshot_hash",
    "contract_hash",
    "aggregate_metrics",
}
_COUNT_FIELDS = {
    "model_calls",
    "tool_executions",
    "clarifications",
    "input_tokens",
    "output_tokens",
}
_VERIFIER_FIELDS = {"passed", "error_code"}
_LOCAL_FIELDS = {
    "run_id",
    "thread_id",
    "graph_path",
    "node_outcomes",
    "safe_error_codes",
    "local_product_map",
    "local_evidence_ids",
    "candidate_titles",
}
PUBLIC_GRAPH_NODES = frozenset(
    {
        "validate_input",
        "parse_request",
        "resolve_products",
        "clarify_once",
        "route_workflow",
        "execute_evidence_tool",
        "build_safe_evidence",
        "draft_answer",
        "verify_answer",
        "repair_once",
        "deterministic_fallback",
        "render_local",
        "write_redacted_trace",
        "END",
    }
)
PUBLIC_NODE_OUTCOMES = frozenset(
    {
        "accepted",
        "parsed",
        "resolved",
        "clarification_required",
        "clarified",
        "routed",
        "executed",
        "built",
        "drafted",
        "verified",
        "rejected",
        "repaired",
        "fallback",
        "rendered",
        "written",
        "interrupted",
        "failed",
        "skipped",
    }
)
_PROHIBITED_PUBLIC_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "authorization_header",
    "candidate_title",
    "candidate_titles",
    "checkpoint",
    "checkpoint_value",
    "checkpoint_values",
    "evidence_id",
    "evidence_ids",
    "excerpt",
    "excerpts",
    "local_evidence_id",
    "local_evidence_ids",
    "local_product_map",
    "parent_asin",
    "product_id",
    "product_ids",
    "raw_review",
    "review_text",
    "secret",
}
_PROHIBITED_LOCAL_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "authorization_header",
    "environment",
    "env",
    "exception_payload",
    "headers",
    "original_exception",
    "original_headers",
}
_ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9:/])(?:"
    r"\\\\[^\\/\s\"'<>|]+[\\/][^\s\"'<>|]+"
    r"|/(?:[^/\s\"'<>|]+/)*[^/\s\"'<>|]*)"
    r"|(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s\"'<>|]*"
)
_LABELED_ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9_])path\s*:\s*(?:[A-Za-z]:[\\/]|/)", re.IGNORECASE
)
_AUTHORIZATION_VALUE = re.compile(
    r"(?:authorization[\"']?\s*:\s*[\"']?\s*(?:basic|bearer)?\s*\S+"
    r"|(?:^|[\s,;({\"'])bearer\s+\S+)",
    re.IGNORECASE,
)
_SECRET_VALUE = re.compile(r"(?:^|[^A-Za-z0-9])sk-[A-Za-z0-9_-]{6,}", re.IGNORECASE)
_SECRET_MARKER = re.compile(
    r"(?<![A-Za-z0-9_])(?:api[_-]?key|apikey|secret)\s*[\"']?\s*[:=]\s*[\"']?\s*\S+",
    re.IGNORECASE,
)
_PRODUCT_ID_VALUE = re.compile(r"(?<![A-Z0-9])B0[A-Z0-9]{8}(?![A-Z0-9])", re.IGNORECASE)
_EVIDENCE_ID_VALUE = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", re.IGNORECASE)
_SENSITIVE_TEXT_MARKER = re.compile(
    r"(?:candidate[_ -]?title|raw[_ -]?review|review[_ -]?text|excerpt)\s*[:=]",
    re.IGNORECASE,
)
_SAFE_ERROR_CODES = {member.value for member in ErrorCode}
_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[Path, threading.Lock] = {}


class PublicationError(ValueError):
    """Raised before an unsafe or malformed trace can be published."""


@dataclass(frozen=True)
class TraceIdentifiers:
    run_id: str
    thread_id: str


def new_trace_ids() -> TraceIdentifiers:
    """Return opaque UUID4 identifiers with no user-derived input."""

    return TraceIdentifiers(run_id=str(uuid4()), thread_id=str(uuid4()))


def serialize_local_trace(trace: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and copy the deliberately small local diagnostic record."""

    document = _copy_mapping(trace)
    _require_exact_fields(document, required=_LOCAL_FIELDS, allowed=_LOCAL_FIELDS, where="local")
    _validate_uuid4(document["run_id"], "run_id")
    _validate_uuid4(document["thread_id"], "thread_id")
    _validate_string_list(document["graph_path"], "graph_path")
    _validate_string_mapping(document["node_outcomes"], "node_outcomes")
    _validate_safe_errors(document["safe_error_codes"])
    _validate_string_mapping(document["local_product_map"], "local_product_map")
    _validate_string_list(document["local_evidence_ids"], "local_evidence_ids")
    _validate_string_list(document["candidate_titles"], "candidate_titles")
    _scan_recursive(document, prohibited_keys=_PROHIBITED_LOCAL_KEYS, public=False)
    return {"schema_version": _SCHEMA_VERSION, **document}


def serialize_public_trace(trace: Mapping[str, Any]) -> dict[str, Any]:
    """Build the strict aggregate-only public form and reject rather than redact."""

    document = _copy_mapping(trace)
    _validate_public_document(document)
    return document


def assert_publication_safe(value: Any) -> None:
    """Recursively reject prohibited keys, values, paths, and schema additions."""

    if not isinstance(value, Mapping):
        raise PublicationError("Public trace must be an object.")
    document = _copy_mapping(value)
    _validate_public_document(document)


def _validate_public_document(document: dict[str, Any]) -> None:
    _require_exact_fields(document, required=_PUBLIC_FIELDS, allowed=_PUBLIC_FIELDS, where="public")
    _scan_recursive(document, prohibited_keys=_PROHIBITED_PUBLIC_KEYS, public=True)
    _validate_public_graph(document["graph_path"], document["node_outcomes"])
    _validate_safe_errors(document["safe_error_codes"])
    _validate_counts(document["counts"])
    _validate_nonnegative_number(document["latency_ms"], "latency_ms")
    _validate_nonempty_string(document["model_id"], "model_id")
    _validate_nonempty_string(document["config_id"], "config_id")
    _validate_verifier(document["verifier_result"])
    _validate_hash(document["snapshot_hash"], "snapshot_hash")
    _validate_hash(document["contract_hash"], "contract_hash")
    _validate_aggregate_metrics(document["aggregate_metrics"])


def append_local_trace(
    trace: Mapping[str, Any], path: Path = DEFAULT_LOCAL_TRACE_PATH
) -> None:
    """Atomically append one complete JSONL record while preserving prior content."""

    document = serialize_local_trace(trace)
    record = _canonical_json(document) + "\n"
    target = Path(path)
    with _path_lock(target), _interprocess_lock(target):
        previous = target.read_text(encoding="utf-8") if target.exists() else ""
        if previous and not previous.endswith("\n"):
            raise PublicationError("Existing local trace is not complete JSONL.")
        _atomic_write_text(target, previous + record)


def write_public_trace(path: Path, trace: Mapping[str, Any]) -> None:
    """Validate fully, then atomically replace a public JSON artifact."""

    document = serialize_public_trace(trace)
    _atomic_write_text(Path(path), _canonical_json(document) + "\n")


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp-{uuid4().hex}")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class _PathLock:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        with _LOCKS_GUARD:
            self.lock = _PATH_LOCKS.setdefault(self.path, threading.Lock())

    def __enter__(self) -> None:
        self.lock.acquire()

    def __exit__(self, *ignored: object) -> None:
        self.lock.release()


def _path_lock(path: Path) -> _PathLock:
    return _PathLock(path)


@contextmanager
def _interprocess_lock(target: Path) -> Any:
    lock_path = target.with_name(f"{target.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _copy_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PublicationError("Trace must be an object.")
    try:
        copied = json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError) as error:
        raise PublicationError("Trace must contain finite JSON values only.") from error
    if not isinstance(copied, dict):
        raise PublicationError("Trace must be an object.")
    return copied


def _require_exact_fields(
    value: Mapping[str, Any], *, required: set[str], allowed: set[str], where: str
) -> None:
    keys = set(value)
    unexpected = keys - allowed
    missing = required - keys
    if unexpected:
        raise PublicationError(f"Unexpected {where} trace fields: {sorted(unexpected)}")
    if missing:
        raise PublicationError(f"Missing {where} trace fields: {sorted(missing)}")


def _as_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PublicationError(f"{name} must be an object.")
    return value


def _validate_uuid4(value: Any, name: str) -> None:
    try:
        parsed = UUID(str(value))
    except (ValueError, TypeError, AttributeError) as error:
        raise PublicationError(f"{name} must be a UUID4.") from error
    if parsed.version != 4 or str(parsed) != value:
        raise PublicationError(f"{name} must be a canonical UUID4.")


def _validate_nonempty_string(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise PublicationError(f"{name} must be a non-empty string.")


def _validate_string_list(value: Any, name: str) -> None:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise PublicationError(f"{name} must be a list of strings.")
    if any(not isinstance(item, str) or not item for item in value):
        raise PublicationError(f"{name} must contain non-empty strings.")


def _validate_string_mapping(value: Any, name: str) -> None:
    mapping = _as_mapping(value, name)
    if any(not isinstance(key, str) or not isinstance(item, str) for key, item in mapping.items()):
        raise PublicationError(f"{name} must map strings to strings.")


def _validate_public_graph(graph_path: Any, node_outcomes: Any) -> None:
    _validate_string_list(graph_path, "graph_path")
    _validate_string_mapping(node_outcomes, "node_outcomes")
    if not graph_path or any(node not in PUBLIC_GRAPH_NODES for node in graph_path):
        raise PublicationError("Public graph path must contain approved P6 graph nodes only.")
    if any(node not in PUBLIC_GRAPH_NODES for node in node_outcomes):
        raise PublicationError("Public node outcomes require approved P6 graph nodes.")
    if any(outcome not in PUBLIC_NODE_OUTCOMES for outcome in node_outcomes.values()):
        raise PublicationError("Public trace contains an unapproved node outcome.")
    if any(node not in graph_path for node in node_outcomes):
        raise PublicationError("Public node outcomes must correspond to the recorded graph path.")


def _validate_safe_errors(value: Any) -> None:
    _validate_string_list(value, "safe_error_codes")
    if any(item not in _SAFE_ERROR_CODES for item in value):
        raise PublicationError("Public and local traces accept approved safe error codes only.")


def _validate_counts(value: Any) -> None:
    counts = _as_mapping(value, "counts")
    _require_exact_fields(counts, required=_COUNT_FIELDS, allowed=_COUNT_FIELDS, where="counts")
    for name, count in counts.items():
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise PublicationError(f"counts.{name} must be a non-negative integer.")


def _validate_nonnegative_number(value: Any, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise PublicationError(f"{name} must be a non-negative number.")


def _validate_verifier(value: Any) -> None:
    verifier = _as_mapping(value, "verifier_result")
    _require_exact_fields(
        verifier,
        required=_VERIFIER_FIELDS,
        allowed=_VERIFIER_FIELDS,
        where="verifier_result",
    )
    if not isinstance(verifier["passed"], bool):
        raise PublicationError("verifier_result.passed must be boolean.")
    error_code = verifier["error_code"]
    if error_code is not None and error_code not in _SAFE_ERROR_CODES:
        raise PublicationError("Verifier error code is not approved.")
    if verifier["passed"] and error_code is not None:
        raise PublicationError("Passing verification cannot contain an error code.")
    if not verifier["passed"] and error_code is None:
        raise PublicationError("Failed verification requires a safe error code.")


def _validate_hash(value: Any, name: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise PublicationError(f"{name} must be a lowercase SHA-256 value.")


def _validate_aggregate_metrics(value: Any) -> None:
    metrics = _as_mapping(value, "aggregate_metrics")
    for name, metric in metrics.items():
        if not isinstance(name, str) or not name:
            raise PublicationError("Aggregate metric names must be non-empty strings.")
        if metric is None or isinstance(metric, (int, float, bool)):
            continue
        raise PublicationError("Aggregate metric values must be numeric, boolean, or null.")


def _scan_recursive(
    value: Any,
    *,
    prohibited_keys: set[str],
    public: bool,
    path: tuple[str, ...] = (),
) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower()
            if _is_prohibited_key(normalized, prohibited_keys):
                raise PublicationError(f"Trace contains prohibited field: {key}")
            if public and normalized.endswith(("_path", "_id")) and normalized not in {
                "config_id",
                "graph_path",
                "model_id",
            }:
                raise PublicationError(f"Trace contains prohibited identifier/path field: {key}")
            _scan_recursive(
                item,
                prohibited_keys=prohibited_keys,
                public=public,
                path=(*path, normalized),
            )
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            _scan_recursive(
                item,
                prohibited_keys=prohibited_keys,
                public=public,
                path=path,
            )
    elif isinstance(value, str):
        if _SECRET_MARKER.search(value):
            raise PublicationError("Trace contains a secret marker.")
        if _SECRET_VALUE.search(value):
            raise PublicationError("Trace contains a secret value.")
        if _AUTHORIZATION_VALUE.search(value):
            raise PublicationError("Trace contains an authorization value.")
        if public and (_ABSOLUTE_PATH.search(value) or _LABELED_ABSOLUTE_PATH.search(value)):
            raise PublicationError("Public trace contains an absolute path.")
        if public and _PRODUCT_ID_VALUE.search(value):
            raise PublicationError("Public trace contains a product identifier.")
        if public and path[-1:] not in {
            ("snapshot_hash",),
            ("contract_hash",),
            ("config_id",),
        } and (
            _EVIDENCE_ID_VALUE.search(value)
        ):
            raise PublicationError("Public trace contains an evidence-like identifier.")
        if public and _SENSITIVE_TEXT_MARKER.search(value):
            raise PublicationError("Public trace contains a sensitive text marker.")


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _is_prohibited_key(key: str, prohibited_keys: set[str]) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", key).strip("_")
    if normalized in {"model_id", "config_id"}:
        return False
    if normalized in prohibited_keys:
        return True
    return normalized.endswith(("api_key", "apikey", "secret", "token"))
