"""Native desktop launcher for the local P7 workbench."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from importlib import import_module
from pathlib import Path
from typing import Any

import uvicorn

from ecom_review_intelligence.agent_cli import _read_config, _validate_store_lineage
from ecom_review_intelligence.workbench_resources import load_local_agent_resources
from ecom_review_intelligence.workbench_service import P6BackedWorkbenchService
from ecom_review_intelligence.workbench_web import create_workbench_app

HOST = "127.0.0.1"
DEFAULT_TITLE = "ECom Review Intelligence"
DEFAULT_WIDTH = 1440
DEFAULT_HEIGHT = 900
STARTUP_TIMEOUT_SECONDS = 12.0
_LOCAL_CONFIG_PATH = Path("artifacts/manifests/phase-6-agent-config.json")
_LOCAL_STORE_PATH = Path("data/processed/ice_makers_evidence.sqlite")


class DesktopLaunchError(RuntimeError):
    """Raised when the desktop shell cannot start its local workbench."""


def _show_error(title: str, message: str) -> None:
    """Show a usable error even when PyInstaller hides the console window."""

    if sys.platform == "win32":
        try:
            ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)
            return
        except Exception as error:  # noqa: BLE001 - fallback must not mask the original error
            del error
    print(json.dumps({"status": "FAILED", "message": message}, ensure_ascii=False), file=sys.stderr)


def _bind_loopback_socket() -> tuple[socket.socket, int]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((HOST, 0))
        sock.listen(socket.SOMAXCONN)
    except OSError:
        sock.close()
        raise
    return sock, int(sock.getsockname()[1])


BUNDLED_STORE = Path("data/public/ice_makers_public.sqlite")
BUNDLED_CONFIG = Path("artifacts/manifests/phase-6-agent-config.json")
_BUNDLED_CODES = ("frostline", "northstar", "harbor")


def resource_root() -> Path:
    """Where the packaged resources live: the exe bundle when frozen, else the repo."""

    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parents[2]


def state_dir() -> Path:
    """Writable per-user state; a packaged app must not write next to the exe."""

    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    path = root / "EComReviewIntelligence"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _bundled_product_ids(store: Path) -> dict[str, str]:
    import sqlite3

    with sqlite3.connect(f"file:{store.as_posix()}?mode=ro", uri=True) as connection:
        rows = connection.execute(
            """SELECT p.parent_asin FROM products p
               JOIN reviews r ON r.parent_asin = p.parent_asin
               GROUP BY p.parent_asin
               ORDER BY count(r.evidence_id) DESC, p.parent_asin
               LIMIT 3"""
        ).fetchall()
    if len(rows) < 3:
        raise DesktopLaunchError("The bundled store does not hold three products.")
    return {code: row[0] for code, row in zip(_BUNDLED_CODES, rows, strict=True)}


def load_bundled_service(root: Path | None = None) -> P6BackedWorkbenchService | None:
    """Build the service over the three-product subset that ships with the app."""

    base = Path(root) if root is not None else resource_root()
    store = base / BUNDLED_STORE
    config_path = base / BUNDLED_CONFIG
    if not (store.is_file() and config_path.is_file()):
        return None
    config = _read_config(config_path)
    _validate_store_lineage(store, config)
    state = state_dir()
    return P6BackedWorkbenchService(
        store_path=store,
        checkpoint_path=state / "checkpoints.sqlite",
        config=config,
        product_ids_by_code=_bundled_product_ids(store),
        verdict_path=state / "verdicts.jsonl",
    )


def _select_service(
    project_root: Path | None, *, prefer_bundled: bool
) -> P6BackedWorkbenchService:
    """Prefer the bundled subset; fall back to a checked-out project with the full store."""

    if prefer_bundled:
        bundled = load_bundled_service()
        if bundled is not None:
            return bundled
    return _load_local_service(resolve_local_project_root(project_root))


def _is_local_project_root(path: Path) -> bool:
    return (path / _LOCAL_CONFIG_PATH).is_file() and (path / _LOCAL_STORE_PATH).is_file()


def _candidate_project_roots(project_root: Path | None) -> tuple[Path, ...]:
    if project_root is not None:
        return (Path(project_root),)

    starts = [Path.cwd(), Path(sys.executable).resolve().parent, Path(__file__).resolve().parent]
    configured_root = os.environ.get("ECOM_REVIEW_INTELLIGENCE_PROJECT_ROOT")
    if configured_root:
        starts.insert(0, Path(configured_root))

    candidates: list[Path] = []
    for start in starts:
        try:
            resolved = start.resolve()
        except OSError:
            continue
        candidates.extend((resolved, *resolved.parents))
    return tuple(dict.fromkeys(candidates))


def resolve_local_project_root(project_root: Path | None = None) -> Path:
    """Locate the checked local P3-P6 resources without embedding them in the app."""

    for candidate in _candidate_project_roots(project_root):
        if _is_local_project_root(candidate):
            return candidate
    if project_root is not None:
        raise DesktopLaunchError("The selected project root does not contain the local evidence resources.")
    raise DesktopLaunchError(
        "Local evidence resources were not found. Keep this app under the project output folder "
        "or set ECOM_REVIEW_INTELLIGENCE_PROJECT_ROOT."
    )


def _load_local_service(project_root: Path) -> P6BackedWorkbenchService:
    try:
        store_path, checkpoint_path, config, product_ids_by_code = load_local_agent_resources(
            project_root
        )
    except Exception as error:
        raise DesktopLaunchError("The controlled local evidence store is not ready.") from error
    return P6BackedWorkbenchService(
        store_path=store_path,
        checkpoint_path=checkpoint_path,
        config=config,
        product_ids_by_code=product_ids_by_code,
    )


def _wait_for_health(
    port: int, *, expected_mode: str, timeout: float = STARTUP_TIMEOUT_SECONDS
) -> None:
    deadline = time.monotonic() + timeout
    url = f"http://{HOST}:{port}/health"
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:
                payload = json.loads(response.read())
                if response.status == 200 and payload.get("mode") == expected_mode:
                    return
        except (OSError, urllib.error.URLError) as error:
            last_error = error
        time.sleep(0.05)
    detail = f" ({last_error})" if last_error else ""
    raise DesktopLaunchError(f"The local workbench did not become ready in time{detail}.")


def _start_server(service: Any) -> tuple[uvicorn.Server, threading.Thread, socket.socket, int]:
    sock, port = _bind_loopback_socket()
    config = uvicorn.Config(
        create_workbench_app(service=service),
        host=HOST,
        port=port,
        reload=False,
        access_log=False,
        log_config=None,
        log_level="error",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [sock]},
        name="ecom-workbench-server",
        daemon=True,
    )
    thread.start()
    try:
        _wait_for_health(port, expected_mode=str(service.mode))
    except Exception:
        server.should_exit = True
        thread.join(timeout=3)
        raise
    return server, thread, sock, port


def launch_desktop(
    *,
    webview_module: Any | None = None,
    project_root: Path | None = None,
    title: str = DEFAULT_TITLE,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    prefer_bundled: bool = True,
) -> None:
    """Start the local workbench and keep it inside a native application window."""

    webview = webview_module or import_module("webview")
    service = _select_service(project_root, prefer_bundled=prefer_bundled)
    server, thread, sock, port = _start_server(service)
    try:
        webview.create_window(
            title,
            url=f"http://{HOST}:{port}",
            width=width,
            height=height,
            min_size=(960, 640),
            resizable=True,
            text_select=True,
        )
        webview.start(private_mode=True)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        if thread.is_alive():
            sock.close()


def smoke_test(*, project_root: Path | None = None, prefer_bundled: bool = True) -> None:
    """Validate the bundled server and package resources without opening a window."""

    service = _select_service(project_root, prefer_bundled=prefer_bundled)
    server, thread, sock, _port = _start_server(service)
    try:
        return
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        if thread.is_alive():
            sock.close()


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch the local ECom Review Intelligence desktop app.")
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--smoke", action="store_true", help="Check the bundled local server and exit.")
    parser.add_argument(
        "--bundled",
        choices=("auto", "bundled", "project"),
        default="auto",
        help="auto: the shipped subset when frozen, else the checked-out project.",
    )
    args = parser.parse_args(argv)
    if args.width < 960 or args.height < 640:
        parser.error("The desktop window is smaller than the supported minimum.")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parse_args(argv)
        prefer_bundled = args.bundled == "bundled" or (
            args.bundled == "auto" and bool(getattr(sys, "frozen", False))
        )
        if args.smoke:
            smoke_test(project_root=args.project_root, prefer_bundled=prefer_bundled)
        else:
            launch_desktop(
                project_root=args.project_root,
                width=args.width,
                height=args.height,
                prefer_bundled=prefer_bundled,
            )
    except SystemExit:
        raise
    except Exception as error:  # noqa: BLE001 - desktop boundary shows a fixed user-facing error
        _show_error("ECom Review Intelligence", "The desktop app could not start.\n\n" + str(error))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
