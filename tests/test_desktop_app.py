from __future__ import annotations

import json
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
    assert (tmp_path / "localappdata" / "EComReviewIntelligence").is_dir()


def test_state_directory_is_created_under_localappdata(tmp_path, monkeypatch) -> None:
    from ecom_review_intelligence import desktop_app

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    path = desktop_app.state_dir()

    assert path == tmp_path / "EComReviewIntelligence"
    assert path.is_dir()
