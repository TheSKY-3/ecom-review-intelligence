"""Local HTTP boundary for the P7 synthetic operations workbench."""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Mapping, Sequence
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ecom_review_intelligence.model_gateway import normalize_request_key, normalize_request_model
from ecom_review_intelligence.workbench_contracts import (
    ChatRequest,
    ChatTurnView,
    EvidenceDetail,
    ExportRequest,
    ExportView,
    InsightRequest,
    InsightView,
    VerdictList,
    VerdictRecord,
    VerdictRequest,
    WorkbenchResumeRequest,
    WorkbenchRun,
    WorkbenchRunRequest,
)
from ecom_review_intelligence.workbench_service import (
    SyntheticWorkbenchService,
    WorkbenchChatUnavailable,
    WorkbenchEvidenceUnavailable,
    WorkbenchExportExists,
    WorkbenchExportFailed,
    WorkbenchExportUnavailable,
    WorkbenchInputError,
    WorkbenchInsightUnavailable,
    WorkbenchRunConflict,
    WorkbenchRunNotFound,
    WorkbenchService,
)


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}})


INSIGHT_KEY_HEADER = "x-insight-key"
INSIGHT_MODEL_HEADER = "x-insight-model"


def _request_model(http_request: Request, backend: WorkbenchService) -> str | None:
    """Accept only a model id this deployment advertises."""

    settings = backend.model_settings() if hasattr(backend, "model_settings") else {}
    options = [item["id"] for item in settings.get("options", ())]
    try:
        return normalize_request_model(
            http_request.headers.get(INSIGHT_MODEL_HEADER),
            options=options or None,
        )
    except ValueError as error:
        raise WorkbenchInputError("The supplied model is not offered.") from error


class InsightRateLimiter:
    """Bounds abuse of the one endpoint that spends money.

    It is deliberately small and in-process: this app has no accounts and no
    shared store, so a deployment that needs real quotas should rate-limit at its
    proxy instead. The limit protects a public demo from a hot loop, nothing more.
    """

    def __init__(
        self,
        *,
        max_calls: int = 10,
        window_seconds: float = 600.0,
        clock: Any = perf_counter,
    ) -> None:
        self._max_calls = max(1, int(max_calls))
        self._window = float(window_seconds)
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] > self._window:
                hits.popleft()
            if len(hits) >= self._max_calls:
                return False
            hits.append(now)
            return True


def assert_response_keys_safe(value: Any) -> None:
    """Reject prohibited field names and secret-like values in any response."""

    def scan(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                normalized = re.sub(r"[^a-z0-9]+", "_", str(key).casefold()).strip("_")
                if normalized in _PROHIBITED_KEYS or normalized.endswith(
                    ("_api_key", "_local_path", "_password", "_secret")
                ):
                    raise ValueError("Public workbench response contains a prohibited field.")
                scan(child)
            return
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            for child in item:
                scan(child)
            return
        if not isinstance(item, str):
            return
        if re.search(r"(?i)(?:DEEPSEEK_API_KEY|sk-[A-Za-z0-9_-]+|Bearer\s+\S+)", item):
            raise ValueError("Public workbench response contains a secret-like value.")

    scan(value)


def assert_public_workbench_safe(value: Any) -> None:
    """Full aggregate-surface scan: also bans identifiers, paths, and leak shapes."""

    def scan(item: Any) -> None:
        if isinstance(item, Mapping):
            for child in item.values():
                scan(child)
            return
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            for child in item:
                scan(child)
            return
        if not isinstance(item, str):
            return
        if re.search(
            r"(?<![A-Za-z0-9])B(?=[A-Za-z0-9]{9}(?![A-Za-z0-9]))(?=[A-Za-z0-9]*[0-9])[A-Za-z0-9]{9}",
            item,
            re.IGNORECASE,
        ):
            raise ValueError("Public workbench response contains a product identifier.")
        if re.search(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", item, re.IGNORECASE):
            raise ValueError("Public workbench response contains an evidence-like identifier.")
        if re.search(
            r"(?:[A-Za-z]:[\\/]|(?:\\\\|//)[^\\/]+[\\/]|(?:^|[\s('\"=])/(?!/)[^\s'\"<>]+)",
            item,
        ):
            raise ValueError("Public workbench response contains an absolute path.")

    assert_response_keys_safe(value)
    scan(value)


def _safe_run(run: WorkbenchRun) -> WorkbenchRun:
    assert_public_workbench_safe(run.model_dump(mode="json"))
    return run


_PROHIBITED_KEYS = {
    "api_key",
    "authorization",
    "evidence_id",
    "local_path",
    "password",
    "product_id",
    "raw_text",
    "review_text",
    "secret",
}


def create_workbench_app(
    service: WorkbenchService | None = None,
    *,
    extra_allowed_hosts: Sequence[str] = (),
    insight_rate_limit: tuple[int, float] = (10, 600.0),
) -> FastAPI:
    app = FastAPI(title="ECom Review Intelligence Workbench", docs_url=None, redoc_url=None)
    backend = service or SyntheticWorkbenchService()
    package_root = Path(__file__).resolve().parent
    templates = Jinja2Templates(directory=package_root / "templates")
    app.mount("/static", StaticFiles(directory=package_root / "static"), name="static")
    limiter = InsightRateLimiter(
        max_calls=insight_rate_limit[0], window_seconds=insight_rate_limit[1]
    )
    allowed_hosts = {
        "127.0.0.1",
        "localhost",
        "testserver",
    } | {
        name.strip().casefold() for name in extra_allowed_hosts if name.strip()
    }
    allowed_suffixes = tuple(
        name[1:].casefold() for name in allowed_hosts if name.startswith("*")
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        hostname = request.headers.get("host", "").rsplit(":", 1)[0].casefold()
        permitted = hostname in allowed_hosts or any(
            hostname.endswith(suffix) and hostname != suffix.lstrip(".")
            for suffix in allowed_suffixes
        )
        if not permitted:
            response = _error(400, "INVALID_HOST", "Host is not allowed.")
        else:
            response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        del request, error
        return _error(422, "INVALID_INPUT", "Request validation failed.")

    @app.exception_handler(WorkbenchInputError)
    async def input_error(request: Request, error: WorkbenchInputError) -> JSONResponse:
        del request, error
        return _error(422, "INVALID_INPUT", "Request validation failed.")

    @app.exception_handler(WorkbenchRunNotFound)
    async def not_found(request: Request, error: WorkbenchRunNotFound) -> JSONResponse:
        del request, error
        return _error(404, "RUN_NOT_FOUND", "Run was not found.")

    @app.exception_handler(WorkbenchRunConflict)
    async def conflict(request: Request, error: WorkbenchRunConflict) -> JSONResponse:
        del request, error
        return _error(409, "STATE_ERROR", "Run cannot be resumed.")

    @app.exception_handler(WorkbenchEvidenceUnavailable)
    async def evidence_unavailable(
        request: Request, error: WorkbenchEvidenceUnavailable
    ) -> JSONResponse:
        del request, error
        return _error(
            404,
            "EVIDENCE_DETAIL_UNAVAILABLE",
            "Evidence detail is only available for completed controlled local runs.",
        )

    @app.exception_handler(WorkbenchInsightUnavailable)
    async def insight_unavailable(
        request: Request, error: WorkbenchInsightUnavailable
    ) -> JSONResponse:
        del request, error
        return _error(
            404,
            "INSIGHT_UNAVAILABLE",
            "The optional AI reading step is unavailable for this run.",
        )

    @app.exception_handler(WorkbenchChatUnavailable)
    async def chat_unavailable(
        request: Request, error: WorkbenchChatUnavailable
    ) -> JSONResponse:
        del request, error
        return _error(
            404,
            "CHAT_UNAVAILABLE",
            "The chat assistant is unavailable for this run or deployment.",
        )

    @app.exception_handler(WorkbenchExportUnavailable)
    async def export_unavailable(
        request: Request, error: WorkbenchExportUnavailable
    ) -> JSONResponse:
        del request, error
        return _error(
            404,
            "EXPORT_UNAVAILABLE",
            "The review export is only available for completed controlled local runs.",
        )

    @app.exception_handler(WorkbenchExportExists)
    async def export_exists(request: Request, error: WorkbenchExportExists) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "EXPORT_ALREADY_DONE",
                    "message": "This run already exported once.",
                },
                "folder": error.folder,
                "exported": error.exported,
            },
        )

    @app.exception_handler(WorkbenchExportFailed)
    async def export_failed(request: Request, error: WorkbenchExportFailed) -> JSONResponse:
        del request, error
        return _error(500, "EXPORT_FAILED", "The review export could not be written.")

    @app.exception_handler(Exception)
    async def unknown_error(request: Request, error: Exception) -> JSONResponse:
        del request, error
        return _error(500, "INTERNAL_ERROR", "Request could not be completed.")

    @app.get("/health")
    def health() -> dict[str, str | bool]:
        return {
            "schema_version": "p7-workbench-health-v1",
            "status": "ok",
            "mode": backend.mode,
            "live_enabled": False,
        }

    @app.get("/", include_in_schema=False)
    def workbench(request: Request):
        product_options = backend.product_options() if hasattr(backend, "product_options") else ()
        insight_available = (
            bool(backend.insight_available())
            if hasattr(backend, "insight_available")
            else False
        )
        insight_byo = (
            bool(backend.insight_byo_available())
            if hasattr(backend, "insight_byo_available")
            else False
        )
        model_settings = (
            backend.model_settings()
            if hasattr(backend, "model_settings")
            else {"default": "", "options": []}
        )
        return templates.TemplateResponse(
            request=request,
            name="workbench.html",
            context={
                "mode": backend.mode,
                "products": product_options,
                "insight_available": insight_available,
                "insight_byo": insight_byo,
                "model_settings": model_settings,
            },
        )

    @app.get("/favicon.ico", include_in_schema=False, status_code=204)
    def favicon() -> Response:
        return Response(status_code=204)

    @app.post("/api/v1/runs", status_code=201, response_model=WorkbenchRun)
    def create_run(request: WorkbenchRunRequest) -> WorkbenchRun:
        return _safe_run(backend.create_run(request))

    @app.get("/api/v1/runs/{run_token}", response_model=WorkbenchRun)
    def get_run(run_token: str) -> WorkbenchRun:
        return _safe_run(backend.get_run(run_token))

    @app.post("/api/v1/runs/{run_token}/resume", response_model=WorkbenchRun)
    def resume_run(run_token: str, request: WorkbenchResumeRequest) -> WorkbenchRun:
        return _safe_run(backend.resume_run(run_token, request))

    @app.get(
        "/api/v1/runs/{run_token}/evidence/{evidence_alias}",
        response_model=EvidenceDetail,
    )
    def evidence_detail(run_token: str, evidence_alias: str) -> EvidenceDetail:
        if backend.mode != "local" or not hasattr(backend, "get_evidence_rows"):
            raise WorkbenchEvidenceUnavailable()
        detail = backend.get_evidence_rows(run_token, evidence_alias)
        # Reviewer text is untrusted local data: this owner-facing detail surface
        # checks keys and secrets, and intentionally skips the value-shape rules
        # that exist for the aggregate public surface.
        assert_response_keys_safe(detail.model_dump(mode="json"))
        return detail

    @app.post("/api/v1/runs/{run_token}/exports", status_code=201, response_model=ExportView)
    def export_reviews(run_token: str, request: ExportRequest) -> ExportView:
        """Write this run's matched reviews for one product; never calls a model.

        Deliberately key-free: the export reads only the local evidence store, so
        the result page can offer it before any model key is configured.
        """

        if backend.mode != "local" or not hasattr(backend, "export_run_reviews"):
            raise WorkbenchExportUnavailable()
        view = backend.export_run_reviews(run_token, request.product_code)
        assert_public_workbench_safe(view.model_dump(mode="json"))
        return view

    @app.post("/api/v1/insights", response_model=InsightView)
    def create_insight(request: InsightRequest, http_request: Request) -> InsightView:
        if not hasattr(backend, "run_insight"):
            raise WorkbenchInsightUnavailable()
        server_key = bool(getattr(backend, "insight_available", lambda: False)())
        byo = bool(getattr(backend, "insight_byo_available", lambda: False)())
        try:
            user_key = normalize_request_key(http_request.headers.get(INSIGHT_KEY_HEADER))
        except ValueError as error:
            raise WorkbenchInputError("The supplied API key is not usable.") from error
        model = _request_model(http_request, backend)
        if not server_key and user_key is None and not byo:
            raise WorkbenchInsightUnavailable()
        client = http_request.client.host if http_request.client else "unknown"
        if not limiter.allow(client):
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "RATE_LIMITED",
                        "message": "Too many analysis requests; try again later.",
                    }
                },
            )
        view = backend.run_insight(
            request.run_token,
            request.card_alias,
            user_key=user_key,
            question=request.question,
            model=model,
        )
        assert_public_workbench_safe(view.model_dump(mode="json"))
        return view

    @app.post("/api/v1/chat", response_model=ChatTurnView)
    def create_chat_turn(request: ChatRequest, http_request: Request) -> ChatTurnView:
        if not hasattr(backend, "send_chat"):
            raise WorkbenchChatUnavailable()
        try:
            user_key = normalize_request_key(http_request.headers.get(INSIGHT_KEY_HEADER))
        except ValueError as error:
            raise WorkbenchInputError("The supplied API key is not usable.") from error
        model = _request_model(http_request, backend)
        server_key = bool(getattr(backend, "insight_available", lambda: False)())
        byo = bool(getattr(backend, "insight_byo_available", lambda: False)())
        if not server_key and user_key is None and not byo:
            raise WorkbenchChatUnavailable()
        client = http_request.client.host if http_request.client else "unknown"
        if not limiter.allow(client):
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "RATE_LIMITED",
                        "message": "Too many analysis requests; try again later.",
                    }
                },
            )
        view = backend.send_chat(
            request.run_token, request.message, user_key=user_key, model=model
        )
        assert_public_workbench_safe(view.model_dump(mode="json"))
        return view

    @app.post("/api/v1/verdicts", status_code=201, response_model=VerdictRecord)
    def record_verdict(request: VerdictRequest) -> VerdictRecord:
        record = backend.record_verdict(request)
        assert_public_workbench_safe(record.model_dump(mode="json"))
        return record

    @app.get("/api/v1/runs/{run_token}/verdicts", response_model=VerdictList)
    def list_verdicts(run_token: str) -> VerdictList:
        view = backend.list_verdicts(run_token)
        assert_public_workbench_safe(view.model_dump(mode="json"))
        return view

    return app
