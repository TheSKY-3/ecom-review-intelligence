from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.request import urlopen

import pytest

from ecom_review_intelligence import desktop_app


class _FakeWebview:
    def __init__(self) -> None:
        self.window_args = None
        self.start_args = None

    def create_window(self, *args, **kwargs):
        self.window_args = (args, kwargs)
        with urlopen(kwargs["url"] + "/health", timeout=2) as response:
            assert json.loads(response.read())["mode"] == "local"
        return object()

    def start(self, **kwargs):
        self.start_args = kwargs


class _LocalService:
    mode = "local"


def test_desktop_launcher_wraps_the_existing_local_workbench(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    fake = _FakeWebview()
    monkeypatch.setattr(desktop_app, "resolve_local_project_root", lambda _: tmp_path)
    monkeypatch.setattr(desktop_app, "_load_local_service", lambda _: _LocalService())
    desktop_app.launch_desktop(
        webview_module=fake,
        project_root=tmp_path,
        width=1200,
        height=720,
    )

    assert fake.window_args is not None
    args, kwargs = fake.window_args
    assert args == ("ECom Review Intelligence",)
    assert kwargs["url"].startswith("http://127.0.0.1:")
    assert kwargs["width"] == 1200
    assert kwargs["height"] == 720
    assert "http_server" not in kwargs
    assert fake.start_args == {"private_mode": True}


def test_desktop_main_rejects_an_unsupported_window_size():
    with pytest.raises(SystemExit):
        desktop_app.main(["--width", "959"])


def test_desktop_resolves_an_explicit_local_project_root(tmp_path):
    config = tmp_path / "artifacts/manifests/phase-6-agent-config.json"
    store = tmp_path / "data/processed/ice_makers_evidence.sqlite"
    config.parent.mkdir(parents=True)
    store.parent.mkdir(parents=True)
    config.write_text("{}", encoding="utf-8")
    store.touch()

    assert desktop_app.resolve_local_project_root(tmp_path) == tmp_path


def test_desktop_main_smoke_mode_checks_the_server_without_opening_webview(monkeypatch):
    calls = []
    monkeypatch.setattr(desktop_app, "smoke_test", lambda **_: calls.append("smoke"))
    monkeypatch.setattr(desktop_app, "launch_desktop", lambda **_: calls.append("window"))

    assert desktop_app.main(["--smoke"]) == 0
    assert calls == ["smoke"]


def test_bundled_resources_are_absent_from_an_empty_directory(tmp_path) -> None:
    from ecom_review_intelligence import desktop_app

    assert desktop_app.load_bundled_service(tmp_path) is None


def test_bundled_service_uses_the_shipped_subset(tmp_path, monkeypatch) -> None:
    from ecom_review_intelligence import desktop_app

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    service = desktop_app.load_bundled_service(desktop_app.resource_root())

    assert service is not None
    assert service.mode == "local"
    store = getattr(service, "_store_path", None)
    assert store is not None and store.name == "ice_makers_public.sqlite"
    state = tmp_path / "localappdata" / "EComReviewIntelligence"
    assert state.is_dir()
    # A packaged app must not scatter exports next to the exe or wherever it was
    # launched from, so both the folder and the path prefix point at user state.
    assert service._export_dir == state / "exports"
    assert service._project_root == state


def test_state_directory_is_created_under_localappdata(tmp_path, monkeypatch) -> None:
    from ecom_review_intelligence import desktop_app

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    path = desktop_app.state_dir()

    assert path == tmp_path / "EComReviewIntelligence"
    assert path.is_dir()


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = json.dumps({"status": "ok", "mode": "local"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return


def test_health_probe_accepts_the_local_server_and_reports_a_closed_port() -> None:
    from ecom_review_intelligence import desktop_app

    server = HTTPServer(("127.0.0.1", 0), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        payload, error = desktop_app._probe_health(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert error == ""
    assert payload is not None and payload["mode"] == "local"

    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    unused_port = closed.getsockname()[1]
    closed.close()
    payload, error = desktop_app._probe_health(unused_port)

    assert payload is None
    assert error


def test_smoke_failure_reports_a_reason_instead_of_blocking_on_a_dialog(
    tmp_path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from ecom_review_intelligence import desktop_app

    dialogs: list[str] = []

    def _boom(**_kwargs) -> None:
        raise desktop_app.DesktopLaunchError("The local workbench did not become ready in time.")

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(desktop_app, "smoke_test", _boom)
    monkeypatch.setattr(desktop_app, "_show_error", lambda *args: dialogs.append(args))

    assert desktop_app.main(["--smoke"]) == 1

    assert dialogs == []
    payload = json.loads(capsys.readouterr().err.strip())
    assert payload["status"] == "FAILED"
    assert "did not become ready" in payload["message"]
    assert (tmp_path / "EComReviewIntelligence" / "last-startup-error.log").is_file()


def test_windowed_failure_keeps_a_log_and_shows_the_dialog(tmp_path, monkeypatch) -> None:
    from ecom_review_intelligence import desktop_app

    dialogs: list[tuple[str, str]] = []

    def _boom(**_kwargs) -> None:
        raise desktop_app.DesktopLaunchError("The bundle is broken.")

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(desktop_app, "launch_desktop", _boom)
    monkeypatch.setattr(desktop_app, "_show_error", lambda title, message: dialogs.append((title, message)))

    assert desktop_app.main([]) == 1
    assert dialogs and "The bundle is broken." in dialogs[0][1]
    log = tmp_path / "EComReviewIntelligence" / "last-startup-error.log"
    assert "The bundle is broken." in log.read_text(encoding="utf-8")
