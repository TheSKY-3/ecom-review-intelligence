import hashlib
import io
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver

import ecom_review_intelligence.agent_cli as agent_cli_module
from ecom_review_intelligence.agent_cli import (
    EXIT_INVALID,
    EXIT_PREREQUISITE,
    EXIT_SUCCESS,
    _GraphRuntime,
    agent_main,
    live_main,
    offline_main,
)
from ecom_review_intelligence.agent_contracts import AgentDraft, AgentPlan
from ecom_review_intelligence.agent_graph import AgentGraphDependencies, build_agent_graph
from ecom_review_intelligence.agent_tools import EvidenceToolService, ProductResolver
from ecom_review_intelligence.engine import compute_evidence_id, initialize_store, verify_citation
from ecom_review_intelligence.model_gateway import (
    DeepSeekModelGateway,
    FakeModelGateway,
    ReplayModelGateway,
)

SNAPSHOT = "a" * 64
CONTRACT = "b" * 64
THREAD_ID = "12345678-1234-4234-9234-123456789abc"


class Runtime:
    def __init__(self, result=None):
        self.result = result or {
            "status": "VERIFIED_RESULT",
            "workflow": "diagnosis",
            "error_code": None,
            "model_call_count": 2,
            "tool_execution_count": 1,
            "clarification_count": 0,
            "trace_written": True,
            "verification": {"passed": True},
        }
        self.calls = []

    def invoke(self, request, *, thread_id=None, resume_product_ids=()):
        self.calls.append((request, thread_id, resume_product_ids))
        return self.result


def _config(path: Path) -> Path:
    payload = {
        "schema_version": "phase-6-agent-config-v1",
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "base_url": "https://api.deepseek.com",
        "max_output_tokens": 1200,
        "request_timeout_seconds": 30,
        "retrieval_limit_per_aspect": 5,
        "dataset_snapshot_id": SNAPSHOT,
        "contract_sha256": CONTRACT,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["config_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _store(path: Path) -> Path:
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE store_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.executemany(
            "INSERT INTO store_meta VALUES (?, ?)",
            (("dataset_snapshot_id", SNAPSHOT), ("contract_sha256", CONTRACT)),
        )
        connection.commit()
    finally:
        connection.close()
    return path


def _corpus(path: Path, *, config_hash: str) -> Path:
    payload = {
        "schema_version": "phase-6-live-cases-v1",
        "dataset_snapshot_id": SNAPSHOT,
        "contract_sha256": CONTRACT,
        "config_sha256": config_hash,
        "cases": [],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["corpus_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _factory(captured, runtime):
    @contextmanager
    def factory(settings, gateway_factory):
        captured.append((settings, gateway_factory))
        yield runtime

    return factory


@pytest.mark.parametrize(
    ("mode", "gateway_type"),
    (("fake", FakeModelGateway), ("replay", ReplayModelGateway), ("live", DeepSeekModelGateway)),
)
def test_agent_cli_injects_selected_gateway_without_network(tmp_path, mode, gateway_type):
    config = _config(tmp_path / "config.json")
    store = _store(tmp_path / "store.sqlite")
    replay = Path(__file__).parent / "fixtures" / "p6_model_replay.json"
    captured = []
    runtime = Runtime()
    clients = []
    args = [
        "--mode",
        mode,
        "--question",
        "synthetic question",
        "--aspect",
        "noise",
        "--config",
        str(config),
        "--store",
        str(store),
        "--replay-fixture",
        str(replay),
    ]
    environment = {}
    if mode == "live":
        args.append("--acknowledge-live-api")
        environment["DEEPSEEK_API_KEY"] = "test-only-key"

    code = agent_main(
        args,
        environ=environment,
        runtime_factory=_factory(captured, runtime),
        client_factory=lambda **kwargs: (
            clients.append(kwargs) or type("Client", (), {"max_retries": 0})()
        ),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert code == EXIT_SUCCESS
    gateway = captured[0][1](type("Budget", (), {"consume": lambda self: None})())
    assert isinstance(gateway, gateway_type)
    assert (mode == "live") == bool(clients)


def test_default_agent_mode_is_replay(tmp_path):
    captured = []
    output = io.StringIO()
    code = agent_main(
        [
            "--question",
            "synthetic",
            "--aspect",
            "noise",
            "--config",
            str(_config(tmp_path / "c.json")),
            "--store",
            str(_store(tmp_path / "s.sqlite")),
            "--replay-fixture",
            str(Path(__file__).parent / "fixtures" / "p6_model_replay.json"),
        ],
        environ={},
        runtime_factory=_factory(captured, Runtime()),
        stdout=output,
        stderr=io.StringIO(),
    )
    assert code == EXIT_SUCCESS
    assert captured[0][0].mode == "replay"


@pytest.mark.parametrize("missing", ("config", "store"))
def test_missing_local_prerequisite_has_stable_safe_error(tmp_path, missing):
    config = _config(tmp_path / "config.json")
    store = _store(tmp_path / "store.sqlite")
    if missing == "config":
        config.unlink()
    else:
        store.unlink()
    out, err = io.StringIO(), io.StringIO()
    code = agent_main(
        [
            "--question",
            "synthetic",
            "--aspect",
            "noise",
            "--config",
            str(config),
            "--store",
            str(store),
        ],
        environ={},
        runtime_factory=_factory([], Runtime()),
        stdout=out,
        stderr=err,
    )
    assert code == EXIT_PREREQUISITE
    combined = out.getvalue() + err.getvalue()
    assert str(tmp_path) not in combined
    assert "PREREQUISITE_FAILED" in combined


def test_live_requires_explicit_ack_and_key_before_client_construction(tmp_path):
    config = _config(tmp_path / "config.json")
    store = _store(tmp_path / "store.sqlite")
    clients = []
    base = [
        "--mode",
        "live",
        "--question",
        "safe",
        "--aspect",
        "noise",
        "--config",
        str(config),
        "--store",
        str(store),
    ]
    assert (
        agent_main(
            base,
            environ={"DEEPSEEK_API_KEY": "secret"},
            client_factory=lambda **kw: clients.append(kw),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )
        == EXIT_PREREQUISITE
    )
    assert (
        agent_main(
            base + ["--acknowledge-live-api"],
            environ={},
            client_factory=lambda **kw: clients.append(kw),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )
        == EXIT_PREREQUISITE
    )
    assert clients == []


def test_clarification_resume_passes_only_thread_and_selected_products(tmp_path):
    runtime = Runtime()
    captured = []
    code = agent_main(
        [
            "--resume-thread-id",
            THREAD_ID,
            "--resume-product-id",
            "LOCAL_1",
            "--config",
            str(_config(tmp_path / "c.json")),
            "--store",
            str(_store(tmp_path / "s.sqlite")),
        ],
        environ={},
        runtime_factory=_factory(captured, runtime),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    assert code == EXIT_SUCCESS
    assert runtime.calls[0] == (None, THREAD_ID, ("LOCAL_1",))


def test_real_sqlite_cli_interrupt_resumes_same_thread_without_replanning(tmp_path):
    store = tmp_path / "store.sqlite"
    initialize_store(
        store,
        snapshot_id=SNAPSHOT,
        contract_sha256=CONTRACT,
        aspect_terms={"noise": ("noise",)},
        polarity_terms={"supporting": ("quiet",), "opposing": ("noise",)},
    )
    connection = sqlite3.connect(store)
    try:
        connection.executemany(
            "INSERT INTO products(parent_asin, title) VALUES (?, ?)",
            (("LOCAL_1", "Shared Alpha"), ("LOCAL_2", "Shared Beta")),
        )
        evidence_id = compute_evidence_id(SNAPSHOT, 1)
        cursor = connection.execute(
            "INSERT INTO reviews VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (evidence_id, 1, "LOCAL_1", "noise", 1.0, 1, 1, 0),
        )
        connection.execute(
            "INSERT INTO reviews_fts(rowid, text) VALUES (?, ?)", (cursor.lastrowid, "noise")
        )
        connection.commit()
    finally:
        connection.close()
    gateway = FakeModelGateway(
        plans=[AgentPlan(workflow="diagnosis", product_terms=("Shared",), aspects=("noise",))],
        drafts=[
            AgentDraft(
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
        ],
    )
    checkpoint = tmp_path / "checkpoint.sqlite"
    traces = []

    @contextmanager
    def real_runtime(settings, gateway_factory):
        del settings, gateway_factory
        dependencies = AgentGraphDependencies(
            resolver=ProductResolver(store),
            gateway_factory=lambda budget: gateway,
            tool_factory=lambda ledger: EvidenceToolService(
                store,
                expected_snapshot_id=SNAPSHOT,
                expected_contract_sha256=CONTRACT,
                ledger=ledger,
            ),
            trace_writer=lambda local, public: traces.append((local, public)),
            citation_verifier=lambda item: verify_citation(store, item),
            snapshot_hash=SNAPSHOT,
            contract_hash=CONTRACT,
        )
        with SqliteSaver.from_conn_string(str(checkpoint)) as saver:
            yield _GraphRuntime(build_agent_graph(dependencies, checkpointer=saver), dependencies)

    config = _config(tmp_path / "config.json")
    common = ["--config", str(config), "--store", str(store)]
    first_out = io.StringIO()
    first = agent_main(
        [*common, "--question", "choose", "--product-keyword", "Shared", "--aspect", "noise"],
        environ={},
        runtime_factory=real_runtime,
        uuid_factory=lambda: THREAD_ID,
        stdout=first_out,
        stderr=io.StringIO(),
    )
    resume_token = json.loads(first_out.getvalue())["resume_token"]
    second = agent_main(
        [*common, "--resume-thread-id", resume_token, "--resume-product-id", "LOCAL_1"],
        environ={},
        runtime_factory=real_runtime,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert first == 4
    assert resume_token == THREAD_ID
    assert second == EXIT_SUCCESS
    assert gateway.call_kinds == ("plan", "draft")
    assert traces[0][1]["counts"]["tool_executions"] == 1


def test_runtime_exception_is_fixed_safe_json_without_traceback(tmp_path):
    class ExplodingRuntime(Runtime):
        def invoke(self, request, *, thread_id=None, resume_product_ids=()):
            raise RuntimeError("C:\\private\\raw.txt sk-secret-value")

    error = io.StringIO()
    code = agent_main(
        [
            "--question",
            "safe",
            "--aspect",
            "noise",
            "--config",
            str(_config(tmp_path / "c.json")),
            "--store",
            str(_store(tmp_path / "s.sqlite")),
        ],
        environ={},
        runtime_factory=_factory([], ExplodingRuntime()),
        stdout=io.StringIO(),
        stderr=error,
    )
    assert code != EXIT_SUCCESS
    assert json.loads(error.getvalue())["error_code"] == "RUN_FAILED"
    assert "private" not in error.getvalue() and "secret" not in error.getvalue()


def test_public_resume_token_must_be_uuid4_not_product_like(tmp_path):
    error = io.StringIO()
    code = agent_main(
        [
            "--resume-thread-id",
            "B012345678",
            "--resume-product-id",
            "LOCAL_1",
            "--config",
            str(_config(tmp_path / "c.json")),
            "--store",
            str(_store(tmp_path / "s.sqlite")),
        ],
        environ={},
        runtime_factory=_factory([], Runtime()),
        stdout=io.StringIO(),
        stderr=error,
    )
    assert code == EXIT_INVALID
    assert "B012345678" not in error.getvalue()


def test_machine_json_is_aggregate_safe_and_rejects_invalid_input(tmp_path):
    unsafe = Runtime(
        {
            "status": "C:\\private\\raw.txt",
            "workflow": "sk-secret-value",
            "error_code": "C:\\private\\raw.txt",
            "model_call_count": 0,
            "tool_execution_count": 0,
            "clarification_count": 0,
            "trace_written": True,
            "verification": {"passed": False},
            "response": {
                "rendered_text": "C:\\private\\raw.txt sk-secret-value",
                "citations": ["LOCAL"],
            },
        }
    )
    output = io.StringIO()
    code = agent_main(
        [
            "--question",
            "safe",
            "--aspect",
            "noise",
            "--config",
            str(_config(tmp_path / "c.json")),
            "--store",
            str(_store(tmp_path / "s.sqlite")),
        ],
        environ={},
        runtime_factory=_factory([], unsafe),
        stdout=output,
        stderr=io.StringIO(),
    )
    document = json.loads(output.getvalue())
    assert code != EXIT_SUCCESS
    assert set(document) == {
        "schema_version",
        "status",
        "workflow",
        "error_code",
        "counts",
        "verifier_passed",
        "trace_written",
    }
    assert "private" not in output.getvalue() and "sk-secret" not in output.getvalue()

    parser_error = io.StringIO()
    assert (
        agent_main(
            ["--unknown", "C:\\private\\raw.txt"],
            environ={},
            stdout=io.StringIO(),
            stderr=parser_error,
        )
        == EXIT_INVALID
    )
    assert "private" not in parser_error.getvalue()


def test_live_entry_rejects_dirty_or_hash_mismatched_corpus_before_client(tmp_path):
    config = _config(tmp_path / "config.json")
    config_doc = json.loads(config.read_text(encoding="utf-8"))
    store = _store(tmp_path / "store.sqlite")
    corpus = _corpus(tmp_path / "cases.json", config_hash=config_doc["config_sha256"])
    clients = []
    common = [
        "--acknowledge-live-api",
        "--question",
        "safe",
        "--aspect",
        "noise",
        "--config",
        str(config),
        "--store",
        str(store),
        "--case-corpus",
        str(corpus),
    ]
    corpus.write_text(
        corpus.read_text(encoding="utf-8").replace('"cases": []', '"cases": [1]'), encoding="utf-8"
    )
    assert (
        live_main(
            common,
            environ={"DEEPSEEK_API_KEY": "secret"},
            corpus_clean_checker=lambda path: True,
            client_factory=lambda **kw: clients.append(kw),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )
        == EXIT_PREREQUISITE
    )
    _corpus(corpus, config_hash=config_doc["config_sha256"])
    assert (
        live_main(
            common,
            environ={"DEEPSEEK_API_KEY": "secret"},
            corpus_clean_checker=lambda path: False,
            client_factory=lambda **kw: clients.append(kw),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )
        == EXIT_PREREQUISITE
    )
    assert clients == []


def test_live_entry_rejects_lineage_mismatch_before_client(tmp_path):
    config = _config(tmp_path / "config.json")
    config_doc = json.loads(config.read_text(encoding="utf-8"))
    store = _store(tmp_path / "store.sqlite")
    connection = sqlite3.connect(store)
    try:
        connection.execute(
            "UPDATE store_meta SET value = ? WHERE key = 'dataset_snapshot_id'", ("c" * 64,)
        )
        connection.commit()
    finally:
        connection.close()
    corpus = _corpus(tmp_path / "cases.json", config_hash=config_doc["config_sha256"])
    clients = []
    code = live_main(
        [
            "--acknowledge-live-api",
            "--question",
            "safe",
            "--aspect",
            "noise",
            "--config",
            str(config),
            "--store",
            str(store),
            "--case-corpus",
            str(corpus),
        ],
        environ={"DEEPSEEK_API_KEY": "secret"},
        corpus_clean_checker=lambda path: True,
        client_factory=lambda **kw: clients.append(kw),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    assert code == EXIT_PREREQUISITE
    assert clients == []


def test_offline_entry_forces_non_live_mode():
    err = io.StringIO()
    code = offline_main(["--mode", "live"], environ={}, stdout=io.StringIO(), stderr=err)
    assert code == EXIT_INVALID
    assert "INVALID_ARGUMENT" in err.getvalue()


@pytest.mark.parametrize(
    ("entry", "arguments"),
    (
        (offline_main, ["--mode=live"]),
        (offline_main, ["--mode", "replay", "--mode", "live"]),
        (offline_main, ["--mod", "live"]),
        (offline_main, ["--m", "live"]),
        (agent_main, ["--mod", "live"]),
        (live_main, ["--mod", "replay"]),
        (live_main, ["--m", "replay"]),
        (live_main, ["--mode", "live", "--mode", "replay"]),
        (live_main, ["--mode=replay"]),
    ),
)
def test_entry_mode_isolation_rejects_equals_and_duplicate_bypasses(entry, arguments):
    errors = io.StringIO()
    code = entry(arguments, environ={}, stdout=io.StringIO(), stderr=errors)
    assert code == EXIT_INVALID
    assert json.loads(errors.getvalue())["error_code"] == "INVALID_ARGUMENT"


def test_live_rejects_unapproved_endpoint_before_reading_client(tmp_path):
    config = _config(tmp_path / "config.json")
    payload = json.loads(config.read_text(encoding="utf-8"))
    payload["base_url"] = "https://attacker.invalid/v1"
    payload.pop("config_sha256")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["config_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    config.write_text(json.dumps(payload), encoding="utf-8")
    store = _store(tmp_path / "store.sqlite")
    clients = []

    code = agent_main(
        [
            "--mode",
            "live",
            "--acknowledge-live-api",
            "--question",
            "safe",
            "--aspect",
            "noise",
            "--config",
            str(config),
            "--store",
            str(store),
        ],
        environ={"DEEPSEEK_API_KEY": "TOPSECRET"},
        runtime_factory=_factory([], Runtime()),
        client_factory=lambda **kwargs: clients.append(kwargs),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert code == EXIT_PREREQUISITE
    assert clients == []


def test_production_trace_path_is_bound_to_project_root(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    project_root.mkdir()
    config_path = _config(project_root / "config.json")
    store_path = _store(project_root / "store.sqlite")
    settings = agent_cli_module.CliSettings(
        project_root=project_root,
        mode="replay",
        config_path=config_path,
        store_path=store_path,
        replay_path=Path(__file__).parent / "fixtures" / "p6_model_replay.json",
        checkpoint_path=project_root / "checkpoints.sqlite",
        config=json.loads(config_path.read_text(encoding="utf-8")),
    )
    captured = []
    monkeypatch.setattr(
        agent_cli_module,
        "append_local_trace",
        lambda trace, path: captured.append(path),
    )

    with (
        agent_cli_module._production_runtime(
            settings, lambda budget: ReplayModelGateway.from_path(settings.replay_path)
        ) as runtime,
        pytest.raises(ValueError),
    ):
        runtime.dependencies.trace_writer({}, {})

    assert captured == [project_root / agent_cli_module.DEFAULT_LOCAL_TRACE_PATH]


def test_invalid_resume_arguments_fail_before_missing_config():
    errors = io.StringIO()
    code = agent_main(
        ["--resume-thread-id", THREAD_ID],
        environ={},
        stdout=io.StringIO(),
        stderr=errors,
    )
    assert code == EXIT_INVALID
    assert json.loads(errors.getvalue())["error_code"] == "INVALID_ARGUMENT"
