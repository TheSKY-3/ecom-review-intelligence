"""Deterministic environment and source checks for the no-download preflight."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import sys
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

GIB = 1024**3
UNKNOWN_SIZE_MINIMUM_FREE_BYTES = 20 * GIB
KNOWN_SIZE_MULTIPLIER = 2.5
KNOWN_SIZE_OVERHEAD_BYTES = 2 * GIB
DEFAULT_TIMEOUT_SECONDS = 30.0
USER_AGENT = "EComReviewIntelligence/0.1 preflight"
REVIEW_SOURCE_URL = (
    "https://mcauleylab.ucsd.edu/public_datasets/data/amazon_2023/raw/"
    "review_categories/Appliances.jsonl.gz"
)
METADATA_SOURCE_URL = (
    "https://mcauleylab.ucsd.edu/public_datasets/data/amazon_2023/raw/"
    "meta_categories/meta_Appliances.jsonl.gz"
)
OFFICIAL_SOURCES = (REVIEW_SOURCE_URL, METADATA_SOURCE_URL)


@dataclass(frozen=True)
class SourceProbe:
    """Metadata collected from a bounded HTTP request."""

    url: str
    status: int | None
    total_bytes: int | None
    content_type: str | None
    accept_ranges: bool
    etag: str | None
    last_modified: str | None
    error: str | None


@dataclass(frozen=True)
class PreflightResult:
    """Facts and gate decision collected during P0."""

    observed_at: str
    python_version: str
    python_supported: bool
    operating_system: str
    free_bytes: int
    required_free_bytes: int
    sources: tuple[SourceProbe, ...]
    state: str
    blockers: tuple[str, ...]


Opener = Callable[..., Any]
SourceProber = Callable[[str, float], SourceProbe]


def parse_content_range_total(value: str | None) -> int | None:
    """Return the complete object size from a valid byte Content-Range header."""

    match = re.fullmatch(r"bytes\s+\d+-\d+/(\d+)", value or "")
    return int(match.group(1)) if match else None


def minimum_free_bytes(sizes: Iterable[int | None]) -> int:
    """Calculate the P0 disk gate from compressed source sizes."""

    values = list(sizes)
    if any(size is None for size in values):
        return UNKNOWN_SIZE_MINIMUM_FREE_BYTES
    return int(sum(size for size in values if size is not None) * KNOWN_SIZE_MULTIPLIER) + (
        KNOWN_SIZE_OVERHEAD_BYTES
    )


def probe_source(
    url: str,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    opener: Opener = urlopen,
) -> SourceProbe:
    """Probe a source with a one-byte range request and never read its full body."""

    request = Request(
        url,
        headers={
            "Range": "bytes=0-0",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with opener(request, timeout=timeout_seconds) as response:
            response.read(1)
            headers = response.headers
            status = int(response.status)
            accept_ranges = headers.get("Accept-Ranges", "").lower() == "bytes" or status == 206
            return SourceProbe(
                url=url,
                status=status,
                total_bytes=parse_content_range_total(headers.get("Content-Range")),
                content_type=headers.get("Content-Type"),
                accept_ranges=accept_ranges,
                etag=headers.get("ETag"),
                last_modified=headers.get("Last-Modified"),
                error=None,
            )
    except HTTPError as error:
        return _error_probe(url, status=error.code, error=error)
    except (URLError, TimeoutError, OSError) as error:
        return _error_probe(url, status=None, error=error)


def _error_probe(url: str, *, status: int | None, error: BaseException) -> SourceProbe:
    return SourceProbe(
        url=url,
        status=status,
        total_bytes=None,
        content_type=None,
        accept_ranges=False,
        etag=None,
        last_modified=None,
        error=f"{type(error).__name__}: {error}",
    )


def collect_preflight(
    project_root: str | Path,
    sources: tuple[str, ...] = OFFICIAL_SOURCES,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    source_prober: SourceProber | None = None,
    free_bytes: int | None = None,
) -> PreflightResult:
    """Collect deterministic P0 facts without downloading either source."""

    root = Path(project_root)
    prober = source_prober or _default_source_prober
    probes = tuple(prober(url, timeout_seconds) for url in sources)
    available_bytes = free_bytes if free_bytes is not None else shutil.disk_usage(root).free
    required_bytes = minimum_free_bytes(probe.total_bytes for probe in probes)
    python_supported = sys.version_info >= (3, 11)
    blockers = _collect_blockers(
        python_supported=python_supported,
        sources=probes,
        free_bytes=available_bytes,
        required_free_bytes=required_bytes,
    )
    return PreflightResult(
        observed_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
        python_version=platform.python_version(),
        python_supported=python_supported,
        operating_system=platform.system(),
        free_bytes=available_bytes,
        required_free_bytes=required_bytes,
        sources=probes,
        state="PASSED" if not blockers else "BLOCKED",
        blockers=blockers,
    )


def render_preflight_markdown(result: PreflightResult) -> str:
    """Render a human-readable report while preserving epistemic labels."""

    lines = [
        "# P0 preflight report",
        "",
        f"- State: {result.state}",
        f"- Observed at (UTC): {result.observed_at}",
        "",
        "## OFFICIAL_FACT",
        "",
        "- The source set is the Amazon Reviews'23 Appliances review and metadata files.",
        "- Review validation expects parent_asin, text, and rating fields.",
        "- Metadata validation expects parent_asin and title fields.",
        "",
        "## OBSERVED_DATA",
        "",
        f"- Python: {result.python_version}; supported: {result.python_supported}",
        f"- Operating system: {result.operating_system}",
        f"- Free bytes: {result.free_bytes}",
        f"- Required bytes: {result.required_free_bytes}",
        "",
        "| URL | Status | Bytes | Range | Content type | ETag | Last modified | Error |",
        "|---|---:|---:|---|---|---|---|---|",
    ]
    for source in result.sources:
        lines.append(
            "| {url} | {status} | {size} | {ranges} | {content_type} | {etag} | "
            "{last_modified} | {error} |".format(
                url=source.url,
                status=source.status if source.status is not None else "-",
                size=source.total_bytes if source.total_bytes is not None else "-",
                ranges=source.accept_ranges,
                content_type=source.content_type or "-",
                etag=source.etag or "-",
                last_modified=source.last_modified or "-",
                error=source.error or "-",
            )
        )
    lines.extend(
        [
            "",
            "## DESIGN_DECISION",
            "",
            "- This preflight uses only one-byte range probes; it downloads no raw dataset.",
            "- Raw data, bulk raw review text, secrets, indexes, and model caches remain untracked.",
            "",
            "## OPEN_QUESTION",
            "",
            "- Any future public display of review-derived text requires a separate data-use review.",
            "",
            "## BLOCKERS",
            "",
        ]
    )
    lines.extend(f"- {blocker}" for blocker in result.blockers) or lines.append("- None")
    return "\n".join(lines) + "\n"


def write_preflight_outputs(project_root: str | Path, result: PreflightResult) -> tuple[Path, Path]:
    """Atomically write the machine-readable manifest and Markdown report."""

    root = Path(project_root)
    manifest_path = root / "artifacts" / "manifests" / "phase-0-preflight.json"
    report_path = root / "docs" / "reports" / "phase-0-preflight.md"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(
        manifest_path,
        json.dumps(asdict(result), ensure_ascii=False, indent=2) + "\n",
    )
    _atomic_write_text(report_path, render_preflight_markdown(result))
    return manifest_path, report_path


def _atomic_write_text(path: Path, text: str) -> None:
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    try:
        with temporary_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.replace(path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _default_source_prober(url: str, timeout_seconds: float) -> SourceProbe:
    return probe_source(url, timeout_seconds=timeout_seconds)


def _collect_blockers(
    *,
    python_supported: bool,
    sources: tuple[SourceProbe, ...],
    free_bytes: int,
    required_free_bytes: int,
) -> tuple[str, ...]:
    blockers: list[str] = []
    if not python_supported:
        blockers.append("Python version is lower than 3.11.")
    for source in sources:
        if source.status is None or not 200 <= source.status < 300:
            blockers.append(f"Source is unreachable: {source.url}")
        elif source.total_bytes is None or source.total_bytes <= 0:
            blockers.append(f"Source did not provide a positive total size: {source.url}")
    if free_bytes < required_free_bytes:
        blockers.append(
            f"Insufficient disk space: {free_bytes} free bytes; {required_free_bytes} bytes required."
        )
    return tuple(blockers)
