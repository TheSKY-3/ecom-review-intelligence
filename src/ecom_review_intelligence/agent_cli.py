"""Explicit, fail-closed command boundary for the P6 local Agent."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TextIO
from uuid import UUID, uuid4

from langgraph.types import Command
from openai import OpenAI
from pydantic import ValidationError

from ecom_review_intelligence.agent_contracts import AgentDraft, AgentPlan, AgentRequest
from ecom_review_intelligence.agent_graph import (
    AgentGraphDependencies,
    open_sqlite_agent_graph,
)
from ecom_review_intelligence.agent_tools import (
    EvidenceToolService,
    ProductResolver,
)
from ecom_review_intelligence.agent_trace import (
    DEFAULT_LOCAL_TRACE_PATH,
    append_local_trace,
    serialize_public_trace,
)
from ecom_review_intelligence.engine import verify_citation
from ecom_review_intelligence.model_gateway import (
    DeepSeekModelGateway,
    FakeModelGateway,
    ReplayModelGateway,
    RequestBudget,
)

EXIT_SUCCESS = 0
EXIT_INVALID = 2
EXIT_PREREQUISITE = 3
EXIT_CLARIFICATION = 4
EXIT_RUN_FAILED = 5
_PUBLIC_STATUSES = {"VERIFIED_RESULT", "DEGRADED_SAFE_RESULT", "FAILED"}
_PUBLIC_WORKFLOWS = {"diagnosis", "comparison", "triage"}
_PUBLIC_ERROR_CODES = {
    "INVALID_INPUT",
    "INSUFFICIENT_INPUT",
    "PRODUCT_NOT_FOUND",
    "PRODUCT_AMBIGUOUS",
    "INSUFFICIENT_EVIDENCE",
    "POLICY_REJECTED",
    "MODEL_OUTPUT_INVALID",
    "MODEL_BUDGET_EXCEEDED",
    "API_UNAVAILABLE",
    "STATE_ERROR",
    "VERIFICATION_FAILED",
}


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise ValueError("Invalid command arguments.")


@dataclass(frozen=True)
class CliSettings:
    project_root: Path
    mode: str
    config_path: Path
    store_path: Path
    replay_path: Path
    checkpoint_path: Path
    config: dict[str, Any]


class AgentRuntime(Protocol):
    def invoke(
        self,
        request: AgentRequest | None,
        *,
        thread_id: str | None = None,
        resume_product_ids: tuple[str, ...] = (),
    ) -> Mapping[str, Any]: ...


RuntimeFactory = Callable[
    [CliSettings, Callable[[RequestBudget], Any]],
    Iterator[AgentRuntime],
]


class _GraphRuntime:
    def __init__(self, graph: Any, dependencies: AgentGraphDependencies) -> None:
        self.graph = graph
        self.dependencies = dependencies

    def invoke(
        self,
        request: AgentRequest | None,
        *,
        thread_id: str | None = None,
        resume_product_ids: tuple[str, ...] = (),
    ) -> Mapping[str, Any]:
        if resume_product_ids:
            if thread_id is None:
                raise ValueError("A resume requires a thread identifier.")
            config = {"configurable": {"thread_id": thread_id}}
            return self.graph.invoke(
                Command(resume={"product_ids": list(resume_product_ids)}), config
            )
        if request is None:
            raise ValueError("A new run requires a request.")
        state, config = self.dependencies.new_run(request, thread_id=thread_id)
        return self.graph.invoke(state, config)


def _parser(*, live_entry: bool = False) -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--mode", choices=("fake", "replay", "live"), default="replay")
    parser.add_argument("--question")
    parser.add_argument("--product-id", action="append", default=[])
    parser.add_argument("--product-keyword", action="append", default=[])
    parser.add_argument("--aspect", action="append", default=[])
    parser.add_argument("--workflow", choices=("diagnosis", "comparison", "triage"))
    parser.add_argument("--resume-thread-id")
    parser.add_argument("--resume-product-id", action="append", default=[])
    parser.add_argument("--acknowledge-live-api", action="store_true")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--config", type=Path, default=Path("artifacts/manifests/phase-6-agent-config.json")
    )
    parser.add_argument(
        "--store", type=Path, default=Path("data/processed/ice_makers_evidence.sqlite")
    )
    parser.add_argument(
        "--replay-fixture", type=Path, default=Path("tests/fixtures/p6_model_replay.json")
    )
    parser.add_argument(
        "--checkpoint", type=Path, default=Path("data/processed/p6_agent_checkpoints.sqlite")
    )
    if live_entry:
        parser.add_argument(
            "--case-corpus",
            type=Path,
            default=Path("artifacts/evaluation/phase-6-live-cases.json"),
        )
    return parser


def agent_main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    runtime_factory: RuntimeFactory | None = None,
    client_factory: Callable[..., Any] = OpenAI,
    uuid_factory: Callable[[], UUID | str] = uuid4,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    return _main(
        argv,
        environ=environ,
        runtime_factory=runtime_factory,
        client_factory=client_factory,
        uuid_factory=uuid_factory,
        stdout=stdout,
        stderr=stderr,
        live_entry=False,
    )


def offline_main(
    argv: Sequence[str] | None = None,
    **kwargs: Any,
) -> int:
    arguments = list(argv if argv is not None else sys.argv[1:])
    try:
        mode = _unique_option_value(arguments, "--mode")
    except ValueError:
        return _emit_error("INVALID_ARGUMENT", EXIT_INVALID, kwargs.get("stderr") or sys.stderr)
    if mode == "live":
        return _emit_error(
            "INVALID_ARGUMENT",
            EXIT_INVALID,
            kwargs.get("stderr") or sys.stderr,
        )
    if mode is None:
        arguments[0:0] = ["--mode", "replay"]
    return agent_main(arguments, **kwargs)


def live_main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    runtime_factory: RuntimeFactory | None = None,
    client_factory: Callable[..., Any] = OpenAI,
    uuid_factory: Callable[[], UUID | str] = uuid4,
    corpus_clean_checker: Callable[[Path], bool] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    arguments = list(argv if argv is not None else sys.argv[1:])
    try:
        mode = _unique_option_value(arguments, "--mode")
    except ValueError:
        return _emit_error("INVALID_ARGUMENT", EXIT_INVALID, stderr or sys.stderr)
    if mode is None:
        arguments[0:0] = ["--mode", "live"]
    elif mode != "live":
        return _emit_error("INVALID_ARGUMENT", EXIT_INVALID, stderr or sys.stderr)
    return _main(
        arguments,
        environ=environ,
        runtime_factory=runtime_factory,
        client_factory=client_factory,
        uuid_factory=uuid_factory,
        corpus_clean_checker=corpus_clean_checker,
        stdout=stdout,
        stderr=stderr,
        live_entry=True,
    )


def _main(
    argv: Sequence[str] | None,
    *,
    environ: Mapping[str, str] | None,
    runtime_factory: RuntimeFactory | None,
    client_factory: Callable[..., Any],
    uuid_factory: Callable[[], UUID | str],
    stdout: TextIO | None,
    stderr: TextIO | None,
    live_entry: bool,
    corpus_clean_checker: Callable[[Path], bool] | None = None,
) -> int:
    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    try:
        _unique_option_value(list(argv if argv is not None else sys.argv[1:]), "--mode")
    except ValueError:
        return _emit_error("INVALID_ARGUMENT", EXIT_INVALID, errors)
    try:
        args = _parser(live_entry=live_entry).parse_args(argv)
    except (SystemExit, ValueError):
        return _emit_error("INVALID_ARGUMENT", EXIT_INVALID, errors)
    environment = os.environ if environ is None else environ
    try:
        is_resume, thread_id = _prepare_invocation(args, uuid_factory)
    except (TypeError, ValueError):
        return _emit_error("INVALID_ARGUMENT", EXIT_INVALID, errors)

    try:
        settings = _load_settings(args)
        if args.mode == "live":
            _validate_live_config(settings.config)
            key = _require_live_authorization(args, environment)
        else:
            key = None
        if live_entry:
            _validate_live_corpus(
                _resolve_path(args.project_root, args.case_corpus),
                settings,
                corpus_clean_checker or _tracked_corpus_is_clean,
            )
        request = None
        if not is_resume:
            request = AgentRequest(
                question=args.question,
                exact_product_ids=tuple(args.product_id),
                product_title_keywords=tuple(args.product_keyword),
                aspects=tuple(args.aspect),
                workflow_hint=args.workflow,
            )
        gateway_factory = _gateway_factory(
            settings,
            api_key=key,
            client_factory=client_factory,
        )
        factory = runtime_factory or _production_runtime
        with factory(settings, gateway_factory) as runtime:
            state = runtime.invoke(
                request,
                thread_id=thread_id,
                resume_product_ids=tuple(args.resume_product_id),
            )
    except ValidationError:
        return _emit_error("INVALID_INPUT", EXIT_INVALID, errors)
    except (OSError, sqlite3.Error, TypeError, ValueError, json.JSONDecodeError):
        return _emit_error("PREREQUISITE_FAILED", EXIT_PREREQUISITE, errors)
    except Exception:  # noqa: BLE001 - top-level CLI must never expose adapter exceptions
        return _emit_error("RUN_FAILED", EXIT_RUN_FAILED, errors)
    public = _serialize_public_result(state, resume_token=thread_id)
    output.write(json.dumps(public, ensure_ascii=False, sort_keys=True) + "\n")
    if "__interrupt__" in state:
        return EXIT_CLARIFICATION
    return EXIT_SUCCESS if public["status"] != "FAILED" else EXIT_RUN_FAILED


def _load_settings(args: argparse.Namespace) -> CliSettings:
    root = args.project_root.resolve()
    config_path = _resolve_path(root, args.config)
    store_path = _resolve_path(root, args.store)
    replay_path = _resolve_path(root, args.replay_fixture)
    checkpoint_path = _resolve_path(root, args.checkpoint)
    config = _read_config(config_path)
    _validate_store_lineage(store_path, config)
    if args.mode in {"fake", "replay"} and not replay_path.is_file():
        raise ValueError("Replay fixture is unavailable.")
    return CliSettings(
        project_root=root,
        mode=args.mode,
        config_path=config_path,
        store_path=store_path,
        replay_path=replay_path,
        checkpoint_path=checkpoint_path,
        config=config,
    )


def _read_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError("Configuration is unavailable.")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("Configuration is invalid.")
    expected = value.get("config_sha256")
    content = dict(value)
    content.pop("config_sha256", None)
    content.pop("config_sha256_rule", None)
    actual = hashlib.sha256(
        json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    required = {
        "model",
        "base_url",
        "max_output_tokens",
        "request_timeout_seconds",
        "retrieval_limit_per_aspect",
        "dataset_snapshot_id",
        "contract_sha256",
    }
    if expected != actual or not required.issubset(value):
        raise ValueError("Configuration integrity check failed.")
    return value


def _validate_live_config(config: Mapping[str, Any]) -> None:
    approved = {
        "schema_version": "phase-6-agent-config-v1",
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "base_url": "https://api.deepseek.com",
    }
    if any(config.get(key) != value for key, value in approved.items()):
        raise ValueError("Live provider configuration is not approved.")


def _prepare_invocation(
    args: argparse.Namespace, uuid_factory: Callable[[], UUID | str]
) -> tuple[bool, str]:
    is_resume = bool(args.resume_product_id)
    if is_resume != bool(args.resume_thread_id):
        raise ValueError("Resume token and selection must be provided together.")
    thread_id = args.resume_thread_id if is_resume else str(uuid_factory())
    if not _is_uuid4(thread_id):
        raise ValueError("Thread identifier is invalid.")
    return is_resume, thread_id


def _validate_store_lineage(path: Path, config: Mapping[str, Any]) -> None:
    if not path.is_file():
        raise ValueError("Evidence store is unavailable.")
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        metadata = dict(connection.execute("SELECT key, value FROM store_meta"))
    finally:
        connection.close()
    if (
        metadata.get("dataset_snapshot_id") != config["dataset_snapshot_id"]
        or metadata.get("contract_sha256") != config["contract_sha256"]
    ):
        raise ValueError("Evidence-store lineage mismatch.")


def _require_live_authorization(args: argparse.Namespace, environment: Mapping[str, str]) -> str:
    if not args.acknowledge_live_api:
        raise ValueError("Live API acknowledgement is required.")
    key = environment.get("DEEPSEEK_API_KEY")
    if not isinstance(key, str) or not key.strip():
        raise ValueError("Live API key is unavailable.")
    return key


def _gateway_factory(
    settings: CliSettings,
    *,
    api_key: str | None,
    client_factory: Callable[..., Any],
) -> Callable[[RequestBudget], Any]:
    if settings.mode == "live":

        def live(budget: RequestBudget) -> DeepSeekModelGateway:
            client = client_factory(
                api_key=api_key,
                base_url=settings.config["base_url"],
                max_retries=0,
            )
            return DeepSeekModelGateway(
                client=client,
                budget=budget,
                model=settings.config["model"],
                max_output_tokens=settings.config["max_output_tokens"],
                request_timeout_seconds=settings.config["request_timeout_seconds"],
            )

        return live
    if settings.mode == "replay":
        return lambda budget: ReplayModelGateway.from_path(settings.replay_path)

    plans, drafts = _load_fake_script(settings.replay_path)
    return lambda budget: FakeModelGateway(plans=list(plans), drafts=list(drafts))


def _load_fake_script(path: Path) -> tuple[tuple[AgentPlan, ...], tuple[AgentDraft, ...]]:
    # The replay adapter performs hash and prohibited-content validation first.
    ReplayModelGateway.from_path(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    plans = tuple(
        AgentPlan.model_validate(item["value"])
        for item in document["interactions"]
        if item.get("kind") == "plan"
    )
    drafts = tuple(
        AgentDraft.model_validate(item["value"])
        for item in document["interactions"]
        if item.get("kind") == "draft"
    )
    return plans, drafts


@contextmanager
def _production_runtime(
    settings: CliSettings,
    gateway_factory: Callable[[RequestBudget], Any],
) -> Iterator[_GraphRuntime]:
    store = settings.store_path
    config = settings.config
    dependencies = AgentGraphDependencies(
        resolver=ProductResolver(store),
        gateway_factory=gateway_factory,
        tool_factory=lambda ledger: EvidenceToolService(
            store,
            expected_snapshot_id=config["dataset_snapshot_id"],
            expected_contract_sha256=config["contract_sha256"],
            retrieval_limit=config["retrieval_limit_per_aspect"],
            ledger=ledger,
        ),
        trace_writer=lambda local, public: (
            append_local_trace(local, settings.project_root / DEFAULT_LOCAL_TRACE_PATH),
            serialize_public_trace(public),
        ),
        citation_verifier=lambda evidence_id: verify_citation(store, evidence_id),
        snapshot_hash=config["dataset_snapshot_id"],
        contract_hash=config["contract_sha256"],
        model_id=config["model"],
        config_id=config["config_sha256"],
    )
    with open_sqlite_agent_graph(
        dependencies,
        project_root=settings.project_root,
        checkpoint_path=settings.checkpoint_path,
    ) as graph:
        yield _GraphRuntime(graph, dependencies)


def _serialize_public_result(
    state: Mapping[str, Any], *, resume_token: str | None = None
) -> dict[str, Any]:
    interrupted = "__interrupt__" in state
    verification = state.get("verification")
    verifier_passed = bool(isinstance(verification, Mapping) and verification.get("passed") is True)
    status = "NEEDS_CLARIFICATION" if interrupted else state.get("status")
    workflow = state.get("workflow")
    error_code = state.get("error_code")
    malformed = (
        not interrupted
        and status not in _PUBLIC_STATUSES
        or workflow is not None
        and workflow not in _PUBLIC_WORKFLOWS
        or error_code is not None
        and error_code not in _PUBLIC_ERROR_CODES
    )
    if malformed:
        status, workflow, error_code = "FAILED", None, "STATE_ERROR"
    result = {
        "schema_version": "p6-agent-cli-v1",
        "status": status,
        "workflow": workflow,
        "error_code": error_code,
        "counts": {
            "model_calls": _safe_count(state.get("model_call_count")),
            "tool_executions": _safe_count(state.get("tool_execution_count")),
            "clarifications": _safe_count(state.get("clarification_count")),
        },
        "verifier_passed": verifier_passed,
        "trace_written": state.get("trace_written") is True,
    }
    if interrupted and resume_token is not None:
        result["resume_token"] = resume_token
    return result


def _safe_count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _validate_live_corpus(
    path: Path,
    settings: CliSettings,
    clean_checker: Callable[[Path], bool],
) -> None:
    if not path.is_file() or not clean_checker(path):
        raise ValueError("Frozen live corpus is unavailable or dirty.")
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError("Frozen live corpus is invalid.")
    expected = document.get("corpus_sha256")
    content = dict(document)
    content.pop("corpus_sha256", None)
    actual = hashlib.sha256(
        json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if (
        expected != actual
        or document.get("dataset_snapshot_id") != settings.config["dataset_snapshot_id"]
        or document.get("contract_sha256") != settings.config["contract_sha256"]
        or document.get("config_sha256") != settings.config["config_sha256"]
    ):
        raise ValueError("Frozen live corpus integrity or lineage mismatch.")


def _tracked_corpus_is_clean(path: Path) -> bool:
    root = Path(__file__).resolve().parents[2]
    try:
        relative = path.resolve().relative_to(root)
    except ValueError:
        return False
    tracked = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", str(relative)],
        capture_output=True,
        check=False,
        text=True,
    )
    dirty = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "--", str(relative)],
        capture_output=True,
        check=False,
        text=True,
    )
    return tracked.returncode == 0 and dirty.returncode == 0 and not dirty.stdout.strip()


def _resolve_path(root: Path, path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _unique_option_value(arguments: Sequence[str], name: str) -> str | None:
    values: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == name:
            if index + 1 >= len(arguments):
                raise ValueError("Option value is missing.")
            values.append(arguments[index + 1])
            index += 2
            continue
        if argument.startswith(name + "="):
            values.append(argument.split("=", 1)[1])
        index += 1
    if len(values) > 1:
        raise ValueError("Option must not be repeated.")
    return values[0] if values else None


def _is_uuid4(value: str) -> bool:
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        return False
    return parsed.version == 4 and str(parsed) == value.lower()


def _emit_error(code: str, exit_code: int, stream: TextIO) -> int:
    stream.write(json.dumps({"schema_version": "p6-agent-cli-error-v1", "error_code": code}) + "\n")
    return exit_code
