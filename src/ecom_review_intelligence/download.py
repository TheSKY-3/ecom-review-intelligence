"""Resumable, verified local downloads for approved public source files."""

from __future__ import annotations

import ctypes
import gzip
import hashlib
import json
import os
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from uuid import uuid4

DEFAULT_CHUNK_SIZE = 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 30.0
USER_AGENT = "EComReviewIntelligence/0.1 download"
SCRIPT_DOWNLOAD = "script_download"
MANUAL_DIRECT_DOWNLOAD = "manual_direct_download"
_SNAPSHOT_SOURCE_ORDER = ("reviews", "metadata")
_CONTENT_RANGE_PATTERN = re.compile(r"bytes\s+(\d+)-(\d+)/(\d+)")
_IDENTITY_FIELD_LABELS = {
    "url": "URL",
    "total_bytes": "total byte count",
    "etag": "ETag",
    "last_modified": "Last-Modified",
}


class DownloadError(RuntimeError):
    """Raised when a local or remote download identity cannot be trusted."""


@dataclass(frozen=True)
class RemoteObject:
    """Observed identity of an HTTP object that supports byte ranges."""

    url: str
    total_bytes: int
    content_type: str | None
    accept_ranges: bool
    etag: str | None
    last_modified: str | None


@dataclass(frozen=True)
class DownloadResult:
    """Verified local registration for one completed raw source file."""

    source_name: str
    url: str
    final_path: Path
    total_bytes: int
    sha256: str
    etag: str | None
    last_modified: str | None
    resumed_from_bytes: int
    acquisition_method: str = SCRIPT_DOWNLOAD


@dataclass(frozen=True)
class TransferLock:
    """Inspectable safe metadata for one exclusive raw-data target lock."""

    token: str
    source_name: str
    final_filename: str
    process_id: int
    acquired_at: str


Opener = Callable[..., Any]


def probe_remote(
    url: str,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    opener: Opener = urlopen,
) -> RemoteObject:
    """Read one byte to establish a range-capable remote object's identity."""

    request = Request(
        url,
        headers={"Range": "bytes=0-0", "User-Agent": USER_AGENT},
    )
    with opener(request, timeout=timeout_seconds) as response:
        body = response.read(1)
        status = int(response.status)
        headers = response.headers

    content_range = _parse_content_range(headers.get("Content-Range"))
    if status != 206:
        raise DownloadError(f"Expected HTTP 206 for source probe, received {status}.")
    if content_range is None or content_range[0] != 0 or content_range[1] != 0:
        raise DownloadError("Source probe did not return a one-byte Content-Range.")
    if not body:
        raise DownloadError("Source probe returned an empty body.")

    start, end, total_bytes = content_range
    if start != 0 or end != 0 or total_bytes <= 0:
        raise DownloadError("Source probe did not expose a positive complete object size.")

    accept_ranges = headers.get("Accept-Ranges", "").lower() == "bytes" or status == 206
    if not accept_ranges:
        raise DownloadError("Source does not support byte ranges.")

    return RemoteObject(
        url=url,
        total_bytes=total_bytes,
        content_type=headers.get("Content-Type"),
        accept_ranges=accept_ranges,
        etag=headers.get("ETag"),
        last_modified=headers.get("Last-Modified"),
    )


def download_remote(
    source_name: str,
    remote: RemoteObject,
    final_path: Path,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    opener: Opener = urlopen,
) -> DownloadResult:
    """Resume or create one verified local source file for a fixed remote identity."""

    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")

    final_path.parent.mkdir(parents=True, exist_ok=True)
    with _exclusive_target_lock(final_path, source_name):
        return _download_remote_locked(
            source_name=source_name,
            remote=remote,
            final_path=final_path,
            timeout_seconds=timeout_seconds,
            chunk_size=chunk_size,
            opener=opener,
        )


def _download_remote_locked(
    *,
    source_name: str,
    remote: RemoteObject,
    final_path: Path,
    timeout_seconds: float,
    chunk_size: int,
    opener: Opener,
) -> DownloadResult:
    part_path = _part_path(final_path)
    identity_path = _identity_path(final_path)
    receipt_path = _receipt_path(final_path)
    expected_identity = _remote_identity(remote)

    if final_path.exists():
        return _verify_completed_file(
            source_name=source_name,
            remote=remote,
            final_path=final_path,
            part_path=part_path,
            identity_path=identity_path,
            receipt_path=receipt_path,
        )

    if receipt_path.exists():
        raise DownloadError("Completion receipt exists but the completed data file is missing.")

    resumed_from_bytes = _prepare_partial_transfer(
        remote=remote,
        part_path=part_path,
        identity_path=identity_path,
        expected_identity=expected_identity,
    )
    _stream_range_response(
        remote=remote,
        part_path=part_path,
        start=resumed_from_bytes,
        timeout_seconds=timeout_seconds,
        chunk_size=chunk_size,
        opener=opener,
    )

    completed_size = part_path.stat().st_size
    if completed_size != remote.total_bytes:
        raise DownloadError(
            f"Downloaded byte count {completed_size} does not match expected {remote.total_bytes}."
        )

    sha256 = _hash_and_validate_gzip(part_path)
    _promote_completed_file(
        source_name=source_name,
        remote=remote,
        part_path=part_path,
        identity_path=identity_path,
        receipt_path=receipt_path,
        final_path=final_path,
        sha256=sha256,
    )
    return DownloadResult(
        source_name=source_name,
        url=remote.url,
        final_path=final_path,
        total_bytes=remote.total_bytes,
        sha256=sha256,
        etag=remote.etag,
        last_modified=remote.last_modified,
        resumed_from_bytes=resumed_from_bytes,
        acquisition_method=SCRIPT_DOWNLOAD,
    )


def register_existing(
    source_name: str,
    remote: RemoteObject,
    final_path: Path,
) -> DownloadResult:
    """Register a user-provided final gzip without touching partial-transfer state."""

    final_path.parent.mkdir(parents=True, exist_ok=True)
    with _exclusive_target_lock(final_path, source_name):
        return _register_existing_locked(source_name=source_name, remote=remote, final_path=final_path)


def _register_existing_locked(
    *,
    source_name: str,
    remote: RemoteObject,
    final_path: Path,
) -> DownloadResult:
    receipt_path = _receipt_path(final_path)
    if not final_path.exists():
        raise DownloadError("Manual registration requires an existing completed data file.")
    if final_path.stat().st_size != remote.total_bytes:
        raise DownloadError("Completed data file byte count does not match the remote object.")

    sha256 = _hash_and_validate_gzip(final_path)
    if receipt_path.exists():
        receipt = _read_json(receipt_path)
        _require_matching_identity(receipt.get("remote"), _remote_identity(remote), "completion receipt")
        if receipt.get("source_name") != source_name:
            raise DownloadError("Completion receipt source name does not match the requested source.")
        if receipt.get("sha256") != sha256:
            raise DownloadError("Completed data file SHA-256 does not match its receipt.")
        if receipt.get("acquisition_method") != MANUAL_DIRECT_DOWNLOAD:
            raise DownloadError("Completion receipt does not record manual direct download.")
    else:
        _write_json_atomic(
            receipt_path,
            {
                "source_name": source_name,
                "remote": _remote_identity(remote),
                "sha256": sha256,
                "acquisition_method": MANUAL_DIRECT_DOWNLOAD,
                "validation": {"gzip_stream": "passed"},
            },
        )

    return DownloadResult(
        source_name=source_name,
        url=remote.url,
        final_path=final_path,
        total_bytes=remote.total_bytes,
        sha256=sha256,
        etag=remote.etag,
        last_modified=remote.last_modified,
        resumed_from_bytes=remote.total_bytes,
        acquisition_method=MANUAL_DIRECT_DOWNLOAD,
    )


def write_download_outputs(
    project_root: str | Path,
    results: tuple[DownloadResult, ...],
    *,
    observed_at: str,
) -> tuple[Path, Path]:
    """Atomically write a safe P1 manifest and Markdown provenance report."""

    if not results:
        raise ValueError("At least one completed source registration is required.")

    root = Path(project_root)
    records = [_safe_registration_record(result) for result in results]
    snapshot_id = _dataset_snapshot_id(results)
    manifest_path = root / "artifacts" / "manifests" / "phase-1-download.json"
    report_path = root / "docs" / "reports" / "phase-1-download.md"
    _write_json_atomic(
        manifest_path,
        {
            "state": "PASSED",
            "observed_at": observed_at,
            "dataset_snapshot_id": snapshot_id,
            "sources": records,
        },
    )
    _write_text_atomic(report_path, _render_download_markdown(observed_at, snapshot_id, records))
    return manifest_path, report_path


def _verify_completed_file(
    *,
    source_name: str,
    remote: RemoteObject,
    final_path: Path,
    part_path: Path,
    identity_path: Path,
    receipt_path: Path,
) -> DownloadResult:
    if part_path.exists():
        raise DownloadError("Completed data file and partial transfer file both exist.")
    if not receipt_path.exists():
        raise DownloadError("Completed data file has no completion receipt.")

    receipt = _read_json(receipt_path)
    _require_matching_identity(receipt.get("remote"), _remote_identity(remote), "completion receipt")
    if receipt.get("source_name") != source_name:
        raise DownloadError("Completion receipt source name does not match the requested source.")
    if final_path.stat().st_size != remote.total_bytes:
        raise DownloadError("Completed data file byte count does not match the remote object.")

    sha256 = _hash_and_validate_gzip(final_path)
    if receipt.get("sha256") != sha256:
        raise DownloadError("Completed data file SHA-256 does not match its receipt.")

    if identity_path.exists():
        _require_matching_identity(
            _read_json(identity_path),
            _remote_identity(remote),
            "partial identity sidecar",
        )
        identity_path.unlink()

    return DownloadResult(
        source_name=source_name,
        url=remote.url,
        final_path=final_path,
        total_bytes=remote.total_bytes,
        sha256=sha256,
        etag=remote.etag,
        last_modified=remote.last_modified,
        resumed_from_bytes=remote.total_bytes,
        acquisition_method=_receipt_acquisition_method(receipt),
    )


def _prepare_partial_transfer(
    *,
    remote: RemoteObject,
    part_path: Path,
    identity_path: Path,
    expected_identity: dict[str, str | int | None],
) -> int:
    if part_path.exists():
        if not identity_path.exists():
            raise DownloadError("Partial transfer file has no identity sidecar.")
        _require_matching_identity(
            _read_json(identity_path),
            expected_identity,
            "partial identity sidecar",
        )
        existing_size = part_path.stat().st_size
        if existing_size >= remote.total_bytes:
            raise DownloadError("Partial transfer size is not smaller than the remote object.")
        return existing_size

    if identity_path.exists():
        _require_matching_identity(
            _read_json(identity_path),
            expected_identity,
            "partial identity sidecar",
        )
    else:
        _write_json_atomic(identity_path, expected_identity)
    return 0


def _stream_range_response(
    *,
    remote: RemoteObject,
    part_path: Path,
    start: int,
    timeout_seconds: float,
    chunk_size: int,
    opener: Opener,
) -> None:
    headers = {
        "Range": f"bytes={start}-",
        "User-Agent": USER_AGENT,
    }
    if start > 0 and remote.etag is not None:
        headers["If-Range"] = remote.etag

    request = Request(remote.url, headers=headers)
    with opener(request, timeout=timeout_seconds) as response:
        _validate_range_response(response, remote, start)
        with part_path.open("ab") as destination:
            for chunk in iter(lambda: response.read(chunk_size), b""):
                destination.write(chunk)
            destination.flush()
            os.fsync(destination.fileno())


def _validate_range_response(response: Any, remote: RemoteObject, start: int) -> None:
    status = int(response.status)
    if status != 206:
        raise DownloadError(f"Expected HTTP 206 for range transfer, received {status}.")

    content_range = _parse_content_range(response.headers.get("Content-Range"))
    if content_range is None:
        raise DownloadError("Range transfer did not include a valid Content-Range.")
    range_start, range_end, range_total = content_range
    if (
        range_start != start
        or range_end != remote.total_bytes - 1
        or range_total != remote.total_bytes
    ):
        raise DownloadError("Range transfer Content-Range does not match the probed remote object.")

    if remote.etag is not None and response.headers.get("ETag") != remote.etag:
        raise DownloadError("Range transfer ETag does not match the probed remote object.")


def _promote_completed_file(
    *,
    source_name: str,
    remote: RemoteObject,
    part_path: Path,
    identity_path: Path,
    receipt_path: Path,
    final_path: Path,
    sha256: str,
) -> None:
    temporary_receipt_path = receipt_path.with_suffix(f"{receipt_path.suffix}.tmp")
    receipt = {
        "source_name": source_name,
        "remote": _remote_identity(remote),
        "sha256": sha256,
        "acquisition_method": SCRIPT_DOWNLOAD,
        "validation": {"gzip_stream": "passed"},
    }
    _write_json_file(temporary_receipt_path, receipt)
    part_path.replace(final_path)
    temporary_receipt_path.replace(receipt_path)
    identity_path.unlink()


def _hash_and_validate_gzip(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(DEFAULT_CHUNK_SIZE), b""):
            digest.update(chunk)
    with gzip.open(path, "rb") as gzip_source:
        for _ in iter(lambda: gzip_source.read(DEFAULT_CHUNK_SIZE), b""):
            pass
    return digest.hexdigest()


def inspect_transfer_lock(final_path: Path) -> TransferLock | None:
    """Return current target-lock metadata without changing lock state."""

    lock_path = _lock_path(final_path)
    if not lock_path.exists():
        return None
    record = _read_json(lock_path)
    try:
        token = record["token"]
        source_name = record["source_name"]
        final_filename = record["final_filename"]
        process_id = record["process_id"]
        acquired_at = record["acquired_at"]
    except KeyError as error:
        raise DownloadError("Target lock has incomplete owner metadata.") from error
    if (
        not isinstance(token, str)
        or not isinstance(source_name, str)
        or not isinstance(final_filename, str)
        or not isinstance(process_id, int)
        or not isinstance(acquired_at, str)
    ):
        raise DownloadError("Target lock has invalid owner metadata.")
    return TransferLock(
        token=token,
        source_name=source_name,
        final_filename=final_filename,
        process_id=process_id,
        acquired_at=acquired_at,
    )


def recover_transfer_lock(
    final_path: Path,
    *,
    raw_root: Path,
    expected_token: str,
) -> TransferLock:
    """Explicitly remove an inspected lock only for a target beneath raw_root."""

    resolved_raw_root = raw_root.resolve()
    resolved_target = final_path.resolve()
    if not resolved_target.is_relative_to(resolved_raw_root):
        raise DownloadError("Transfer-lock recovery target is outside the declared raw-data root.")

    lock = inspect_transfer_lock(final_path)
    if lock is None:
        raise DownloadError("No transfer lock exists for the requested target.")
    if lock.token != expected_token:
        raise DownloadError("Transfer-lock recovery requires the current inspection token.")
    if _process_is_active(lock.process_id):
        raise DownloadError("Transfer-lock owner process is still active.")

    current_lock = inspect_transfer_lock(final_path)
    if current_lock != lock:
        raise DownloadError("Transfer lock changed after inspection; inspect it again before recovery.")

    _lock_path(final_path).unlink()
    return lock


@contextmanager
def _exclusive_target_lock(final_path: Path, source_name: str) -> Iterator[None]:
    lock_path = _lock_path(final_path)
    token = uuid4().hex
    record = {
        "token": token,
        "source_name": source_name,
        "final_filename": final_path.name,
        "process_id": os.getpid(),
        "acquired_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
    }
    try:
        _write_lock_exclusive(lock_path, record)
    except FileExistsError as error:
        try:
            existing = inspect_transfer_lock(final_path)
        except DownloadError as lock_error:
            raise DownloadError("Target is locked and its owner metadata is unreadable.") from lock_error
        owner = existing.process_id if existing is not None else "unknown"
        raise DownloadError(f"Target is locked by process {owner}.") from error

    try:
        yield
    finally:
        _release_target_lock(final_path, token)


def _write_lock_exclusive(path: Path, record: dict[str, str | int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as destination:
        json.dump(record, destination, ensure_ascii=False, indent=2)
        destination.write("\n")
        destination.flush()
        os.fsync(destination.fileno())


def _release_target_lock(final_path: Path, token: str) -> None:
    try:
        current_lock = inspect_transfer_lock(final_path)
    except DownloadError:
        return
    if current_lock is not None and current_lock.token == token:
        _lock_path(final_path).unlink()


def _process_is_active(process_id: int) -> bool:
    if process_id <= 0:
        return False
    if os.name == "nt":
        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information,
            False,
            process_id,
        )
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return ctypes.get_last_error() == 5
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _part_path(final_path: Path) -> Path:
    return final_path.with_suffix(f"{final_path.suffix}.part")


def _identity_path(final_path: Path) -> Path:
    return final_path.with_suffix(f"{final_path.suffix}.part.identity.json")


def _lock_path(final_path: Path) -> Path:
    return final_path.with_suffix(f"{final_path.suffix}.lock.json")


def _receipt_path(final_path: Path) -> Path:
    return final_path.with_suffix(f"{final_path.suffix}.receipt.json")


def _remote_identity(remote: RemoteObject) -> dict[str, str | int | None]:
    return {
        "url": remote.url,
        "total_bytes": remote.total_bytes,
        "etag": remote.etag,
        "last_modified": remote.last_modified,
    }


def _safe_registration_record(result: DownloadResult) -> dict[str, str | int | None]:
    return {
        "source_name": result.source_name,
        "url": result.url,
        "filename": result.final_path.name,
        "total_bytes": result.total_bytes,
        "sha256": result.sha256,
        "etag": result.etag,
        "last_modified": result.last_modified,
        "resumed_from_bytes": result.resumed_from_bytes,
        "acquisition_method": result.acquisition_method,
    }


def _render_download_markdown(
    observed_at: str,
    dataset_snapshot_id: str,
    records: list[dict[str, str | int | None]],
) -> str:
    lines = [
        "# P1 download and raw-data registration report",
        "",
        "- State: PASSED",
        f"- Observed at (UTC): {observed_at}",
        f"- Dataset snapshot ID: {dataset_snapshot_id}",
        "",
        "## OFFICIAL_FACT",
        "",
        "- Each record identifies an official source URL and the downloaded object version.",
        "",
        "## OBSERVED_DATA",
        "",
        "| Source | Filename | Bytes | SHA-256 | ETag | Last modified | Resumed bytes | Acquisition |",
        "|---|---|---:|---|---|---|---:|---|",
    ]
    for record in records:
        lines.append(
            "| {source_name} | {filename} | {total_bytes} | {sha256} | {etag} | "
            "{last_modified} | {resumed_from_bytes} | {acquisition_method} |".format(
                **{key: _markdown_cell(value) for key, value in record.items()}
            )
        )
    lines.extend(
        [
            "",
            "## DESIGN_DECISION",
            "",
            "- Raw files remain local-only. This report omits local absolute paths and review text.",
            "",
            "## PROVENANCE_LIMITATION",
            "",
            (
                "- manual_direct_download means local size, SHA-256, and gzip validation passed "
                "after a user-confirmed direct download. It does not claim a browser HTTP receipt "
                "or a server-provided checksum."
            ),
            "",
            "## OPEN_QUESTION",
            "",
            "- Public display or redistribution of review-derived text requires a separate data-use review.",
            "",
            "## BLOCKERS",
            "",
            "- None",
        ]
    )
    return "\n".join(lines) + "\n"


def _dataset_snapshot_id(results: tuple[DownloadResult, ...]) -> str:
    results_by_source = {result.source_name: result for result in results}
    if set(results_by_source) != set(_SNAPSHOT_SOURCE_ORDER) or len(results_by_source) != len(results):
        raise ValueError("P1 snapshot requires exactly one reviews and one metadata registration.")
    payload = "".join(
        f"{source_name}:{results_by_source[source_name].sha256}\n"
        for source_name in _SNAPSHOT_SOURCE_ORDER
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _receipt_acquisition_method(receipt: dict[str, Any]) -> str:
    acquisition_method = receipt.get("acquisition_method", SCRIPT_DOWNLOAD)
    if acquisition_method not in {SCRIPT_DOWNLOAD, MANUAL_DIRECT_DOWNLOAD}:
        raise DownloadError("Completion receipt has an unsupported acquisition method.")
    return str(acquisition_method)


def _markdown_cell(value: object) -> str:
    return str(value if value is not None else "-").replace("|", "\\|").replace("\n", " ")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DownloadError(f"Cannot read local registration sidecar: {path.name}") from error
    if not isinstance(value, dict):
        raise DownloadError(f"Local registration sidecar is not a JSON object: {path.name}")
    return value


def _require_matching_identity(
    observed: object,
    expected: dict[str, str | int | None],
    location: str,
) -> None:
    if not isinstance(observed, dict):
        raise DownloadError(f"{location} has no valid remote identity.")
    for field, expected_value in expected.items():
        if observed.get(field) != expected_value:
            label = _IDENTITY_FIELD_LABELS[field]
            raise DownloadError(f"{location} {label} does not match the probed remote object.")


def _write_json_atomic(path: Path, value: Any) -> None:
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    _write_json_file(temporary_path, value)
    temporary_path.replace(path)


def _write_text_atomic(path: Path, text: str) -> None:
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with temporary_path.open("w", encoding="utf-8", newline="\n") as destination:
        destination.write(text)
        destination.flush()
        os.fsync(destination.fileno())
    temporary_path.replace(path)


def _write_json_file(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as destination:
        json.dump(value, destination, ensure_ascii=False, indent=2)
        destination.write("\n")
        destination.flush()
        os.fsync(destination.fileno())


def _parse_content_range(value: str | None) -> tuple[int, int, int] | None:
    match = _CONTENT_RANGE_PATTERN.fullmatch(value or "")
    if match is None:
        return None
    start, end, total = (int(part) for part in match.groups())
    return start, end, total
