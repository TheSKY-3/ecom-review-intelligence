import json
import multiprocessing
import subprocess
from pathlib import Path
from uuid import UUID

import pytest

from ecom_review_intelligence.agent_trace import (
    DEFAULT_LOCAL_TRACE_PATH,
    PublicationError,
    append_local_trace,
    assert_publication_safe,
    new_trace_ids,
    serialize_local_trace,
    serialize_public_trace,
    write_public_trace,
)


def _local_trace() -> dict[str, object]:
    identifiers = new_trace_ids()
    return {
        "run_id": identifiers.run_id,
        "thread_id": identifiers.thread_id,
        "graph_path": ["validate_input", "resolve_products", "render_local"],
        "node_outcomes": {
            "validate_input": "accepted",
            "resolve_products": "resolved",
            "render_local": "verified",
        },
        "safe_error_codes": [],
        "local_product_map": {"PRODUCT_1": "B0LOCAL123"},
        "local_evidence_ids": ["EVIDENCE_LOCAL_1"],
        "candidate_titles": ["Local diagnostic title"],
    }


def _public_trace() -> dict[str, object]:
    return {
        "graph_path": ["validate_input", "resolve_products", "render_local"],
        "node_outcomes": {
            "validate_input": "accepted",
            "resolve_products": "resolved",
            "render_local": "verified",
        },
        "safe_error_codes": [],
        "counts": {
            "model_calls": 2,
            "tool_executions": 1,
            "clarifications": 0,
            "input_tokens": 120,
            "output_tokens": 40,
        },
        "latency_ms": 18.25,
        "model_id": "deepseek-v4-flash",
        "config_id": "p6-agent-config-v1",
        "verifier_result": {"passed": True, "error_code": None},
        "snapshot_hash": "a" * 64,
        "contract_hash": "b" * 64,
        "aggregate_metrics": {
            "citation_integrity": 1.0,
            "public_leak_count": 0,
            "budget_exceeded": False,
        },
    }


def _append_after_barrier(
    target: str, trace: dict[str, object], barrier: object
) -> None:
    barrier.wait()
    append_local_trace(trace, Path(target))


def test_trace_ids_are_independent_random_uuid4_values() -> None:
    first = new_trace_ids()
    second = new_trace_ids()

    values = (first.run_id, first.thread_id, second.run_id, second.thread_id)
    assert len(set(values)) == 4
    assert all(UUID(value).version == 4 for value in values)


def test_explicit_serializers_keep_local_mappings_out_of_public_trace() -> None:
    local = serialize_local_trace(_local_trace())
    public = serialize_public_trace(_public_trace())

    assert local["local_product_map"] == {"PRODUCT_1": "B0LOCAL123"}
    assert local["local_evidence_ids"] == ["EVIDENCE_LOCAL_1"]
    assert "candidate_titles" in local
    assert set(public) == {
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
    encoded = json.dumps(public)
    assert "B0LOCAL123" not in encoded
    assert "EVIDENCE_LOCAL_1" not in encoded
    assert "Local diagnostic title" not in encoded


def test_public_trace_allows_semantic_config_sha_but_rejects_same_hash_elsewhere() -> None:
    config_hash = "c" * 64
    payload = _public_trace()
    payload["config_id"] = config_hash

    assert serialize_public_trace(payload)["config_id"] == config_hash

    payload = _public_trace()
    payload["model_id"] = config_hash
    with pytest.raises(PublicationError, match="identifier"):
        serialize_public_trace(payload)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("api_key",), "sk-secret"),
        (("authorization",), "Bearer secret"),
        (("nested", "review_text"), "raw review"),
        (("nested", "excerpt"), "raw excerpt"),
        (("nested", "product_id"), "B0LOCAL123"),
        (("nested", "evidence_id"), "EVIDENCE_LOCAL_1"),
        (("candidate_titles",), ["private title"]),
        (("checkpoint", "values"), {"state": "private"}),
        (("aggregate_metrics", "debug_path"), r"C:\\private\\trace.json"),
        (("aggregate_metrics", "debug_path"), "/home/user/trace.json"),
    ],
)
def test_recursive_publication_scanner_rejects_prohibited_content(
    path: tuple[str, ...], value: object
) -> None:
    payload: dict[str, object] = _public_trace()
    cursor = payload
    for part in path[:-1]:
        child = cursor.setdefault(part, {})
        assert isinstance(child, dict)
        cursor = child
    cursor[path[-1]] = value

    with pytest.raises(PublicationError):
        assert_publication_safe(payload)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("graph_path", ["validate_input", "B012345678"], "product identifier"),
        ("graph_path", ["validate_input", "e" * 64], "evidence-like"),
        (
            "node_outcomes",
            {"render_local": "CANDIDATE_TITLE: private local title"},
            "sensitive text marker",
        ),
        (
            "node_outcomes",
            {"render_local": "RAW_REVIEW=private review body"},
            "sensitive text marker",
        ),
        (
            "node_outcomes",
            {"render_local": "result (EXCERPT: private excerpt)"},
            "sensitive text marker",
        ),
        ("model_id", "provider metadata at C:\\private\\model.json", "absolute path"),
        ("config_id", "config loaded from /var/tmp/private.json", "absolute path"),
        ("config_id", r"config loaded from \\server\private\config.json", "absolute path"),
        (
            "node_outcomes",
            {"provider": '{"Authorization":"Basic local-credential"}'},
            "authorization",
        ),
        (
            "node_outcomes",
            {"provider": "failure payload, Bearer local-token"},
            "authorization",
        ),
    ],
)
def test_scanner_rejects_sensitive_values_inside_allowed_string_fields(
    field: str, value: object, message: str
) -> None:
    payload = _public_trace()
    payload[field] = value

    with pytest.raises(PublicationError, match=message):
        assert_publication_safe(payload)


def test_public_node_outcomes_reject_free_form_diagnostic_sentences() -> None:
    payload = _public_trace()
    payload["node_outcomes"] = {
        "render_local": "The ice maker stopped working after two weeks"
    }

    with pytest.raises(PublicationError, match="approved node outcome"):
        assert_publication_safe(payload)


@pytest.mark.parametrize(
    "sensitive_key",
    [
        "openai_api_key",
        "deepseek_api_key",
        "provider_apikey",
        "runtime_secret",
        "access_token",
    ],
)
def test_scanner_rejects_normalized_sensitive_key_suffixes(sensitive_key: str) -> None:
    payload = _public_trace()
    outcomes = payload["node_outcomes"]
    assert isinstance(outcomes, dict)
    outcomes[sensitive_key] = "accepted"

    with pytest.raises(PublicationError, match="prohibited field"):
        assert_publication_safe(payload)


def test_assert_publication_safe_applies_complete_semantic_validation() -> None:
    payload = _public_trace()
    counts = payload["counts"]
    assert isinstance(counts, dict)
    counts["model_calls"] = -1

    with pytest.raises(PublicationError, match="non-negative integer"):
        assert_publication_safe(payload)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("path:/home/alice/file", "absolute path"),
        ("path:C:\\Users\\alice\\file", "absolute path"),
        ("root /", "absolute path"),
        ("root C:\\", "absolute path"),
        ("root C:/", "absolute path"),
        ('{"api_key":"local-credential"}', "secret marker"),
        ("apikey=local-credential", "secret marker"),
        ("secret:local-credential", "secret marker"),
    ],
)
def test_scanner_rejects_residual_path_and_secret_markers_in_allowed_fields(
    value: str, message: str
) -> None:
    payload = _public_trace()
    payload["node_outcomes"] = {"provider": value}

    with pytest.raises(PublicationError, match=message):
        assert_publication_safe(payload)


@pytest.mark.parametrize(
    "value",
    [
        "https://api.deepseek.com/v1",
        "deepseek-v4-flash",
        "p6-agent-config-v1",
    ],
)
def test_scanner_keeps_safe_urls_and_identifiers(value: str) -> None:
    payload = _public_trace()
    payload["model_id"] = value

    assert_publication_safe(payload)


def test_public_serializer_rejects_unexpected_fields_before_writing(tmp_path: Path) -> None:
    target = tmp_path / "public.json"
    payload = _public_trace()
    payload["rendered_text"] = "must never be published"

    with pytest.raises(PublicationError):
        write_public_trace(target, payload)

    assert not target.exists()


@pytest.mark.parametrize("leaked_value", ["B012345678", "e" * 64, "see /var/tmp/private"])
def test_aggregate_metrics_reject_identifier_and_path_strings(leaked_value: str) -> None:
    payload = _public_trace()
    metrics = payload["aggregate_metrics"]
    assert isinstance(metrics, dict)
    metrics["diagnostic_value"] = leaked_value

    with pytest.raises(PublicationError):
        serialize_public_trace(payload)


def test_public_write_is_atomic_and_preserves_previous_file_on_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "public.json"
    target.write_text('{"previous":true}\n', encoding="utf-8")

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr("ecom_review_intelligence.agent_trace.os.replace", fail_replace)

    with pytest.raises(OSError, match="replace failure"):
        write_public_trace(target, _public_trace())

    assert target.read_text(encoding="utf-8") == '{"previous":true}\n'
    assert list(tmp_path.glob("*.tmp-*")) == []


def test_local_jsonl_append_is_atomic_and_keeps_complete_records(tmp_path: Path) -> None:
    target = tmp_path / "p6_agent_traces.jsonl"
    first = _local_trace()
    second = _local_trace()

    append_local_trace(first, target)
    append_local_trace(second, target)

    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["run_id"] for line in lines] == [
        first["run_id"],
        second["run_id"],
    ]
    assert list(tmp_path.glob("*.tmp-*")) == []


def test_local_jsonl_append_serializes_independent_processes(tmp_path: Path) -> None:
    target = tmp_path / "p6_agent_traces.jsonl"
    context = multiprocessing.get_context("spawn")
    process_count = 8
    barrier = context.Barrier(process_count)
    traces = [_local_trace() for _ in range(process_count)]
    processes = [
        context.Process(
            target=_append_after_barrier,
            args=(str(target), trace, barrier),
        )
        for trace in traces
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    records = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    assert {record["run_id"] for record in records} == {trace["run_id"] for trace in traces}
    assert len(records) == process_count


def test_local_append_preserves_previous_file_on_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "p6_agent_traces.jsonl"
    append_local_trace(_local_trace(), target)
    previous = target.read_bytes()

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr("ecom_review_intelligence.agent_trace.os.replace", fail_replace)

    with pytest.raises(OSError, match="replace failure"):
        append_local_trace(_local_trace(), target)

    assert target.read_bytes() == previous
    assert list(tmp_path.glob("*.tmp-*")) == []


def test_local_serializer_rejects_secret_exception_header_and_environment_fields() -> None:
    for prohibited in ("api_key", "exception_payload", "headers", "environment"):
        payload = _local_trace()
        payload[prohibited] = {"private": "value"}
        with pytest.raises(PublicationError):
            serialize_local_trace(payload)


def test_default_local_trace_path_is_ignored_by_git() -> None:
    result = subprocess.run(
        ["git", "check-ignore", "--quiet", str(DEFAULT_LOCAL_TRACE_PATH)],
        cwd=Path(__file__).parents[1],
        check=False,
    )
    assert result.returncode == 0
