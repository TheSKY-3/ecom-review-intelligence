from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import ClassVar, Self

from ecom_review_intelligence.preflight import (
    PreflightResult,
    SourceProbe,
    collect_preflight,
    minimum_free_bytes,
    parse_content_range_total,
    probe_source,
    render_preflight_markdown,
    write_preflight_outputs,
)

REVIEW_URL = (
    "https://mcauleylab.ucsd.edu/public_datasets/data/amazon_2023/raw/"
    "review_categories/Appliances.jsonl.gz"
)


class FakeResponse:
    status = 206
    headers: ClassVar[dict[str, str]] = {
        "Content-Range": "bytes 0-0/270209794",
        "Content-Type": "application/gzip",
        "Accept-Ranges": "bytes",
        "ETag": '"example-etag"',
        "Last-Modified": "Thu, 16 Jan 2025 22:41:53 GMT",
    }

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        return None

    def read(self, amount: int) -> bytes:
        assert amount == 1
        return b"x"


def test_parse_content_range_total_extracts_total_size() -> None:
    assert parse_content_range_total("bytes 0-0/270209794") == 270209794


def test_parse_content_range_total_rejects_invalid_values() -> None:
    assert parse_content_range_total("not a content range") is None


def test_minimum_free_bytes_uses_2_5x_plus_2_gib_when_sizes_known() -> None:
    assert minimum_free_bytes([100, 200]) == int(300 * 2.5) + 2 * 1024**3


def test_minimum_free_bytes_requires_20_gib_when_size_is_unknown() -> None:
    assert minimum_free_bytes([100, None]) == 20 * 1024**3


def test_probe_source_records_a_one_byte_range_response() -> None:
    seen: dict[str, object] = {}

    def fake_urlopen(request: object, timeout: float) -> FakeResponse:
        seen["range"] = request.get_header("Range")
        seen["timeout"] = timeout
        return FakeResponse()

    probe = probe_source(REVIEW_URL, opener=fake_urlopen)

    assert seen == {"range": "bytes=0-0", "timeout": 30.0}
    assert probe.status == 206
    assert probe.total_bytes == 270209794
    assert probe.content_type == "application/gzip"
    assert probe.accept_ranges is True
    assert probe.etag == '"example-etag"'
    assert probe.error is None


def test_collect_preflight_passes_when_sources_and_disk_gate_pass() -> None:
    source_probe = SourceProbe(
        url=REVIEW_URL,
        status=206,
        total_bytes=100,
        content_type="application/gzip",
        accept_ranges=True,
        etag="etag",
        last_modified="date",
        error=None,
    )

    result = collect_preflight(
        project_root=".",
        sources=(REVIEW_URL, "https://example.test/meta.gz"),
        source_prober=lambda url, timeout_seconds: replace(source_probe, url=url),
        free_bytes=3 * 1024**3,
    )

    assert result.state == "PASSED"
    assert result.blockers == ()
    assert result.required_free_bytes == int(200 * 2.5) + 2 * 1024**3


def test_render_preflight_markdown_labels_evidence_types_and_state() -> None:
    result = PreflightResult(
        observed_at="2026-08-02T00:00:00+00:00",
        python_version="3.12.13",
        python_supported=True,
        operating_system="Windows",
        free_bytes=3 * 1024**3,
        required_free_bytes=2 * 1024**3,
        sources=(
            SourceProbe(
                url=REVIEW_URL,
                status=206,
                total_bytes=270209794,
                content_type="application/gzip",
                accept_ranges=True,
                etag="etag",
                last_modified="date",
                error=None,
            ),
        ),
        state="PASSED",
        blockers=(),
    )

    text = render_preflight_markdown(result)

    assert text.startswith("# P0 preflight report")
    assert "OFFICIAL_FACT" in text
    assert "OBSERVED_DATA" in text
    assert "DESIGN_DECISION" in text
    assert "OPEN_QUESTION" in text
    assert "PASSED" in text


def test_write_preflight_outputs_replaces_final_files_without_temp_leftovers(
    tmp_path: Path,
) -> None:
    result = PreflightResult(
        observed_at="2026-08-02T00:00:00+00:00",
        python_version="3.12.13",
        python_supported=True,
        operating_system="Windows",
        free_bytes=3 * 1024**3,
        required_free_bytes=2 * 1024**3,
        sources=(),
        state="PASSED",
        blockers=(),
    )

    manifest_path, report_path = write_preflight_outputs(tmp_path, result)

    assert json.loads(manifest_path.read_text(encoding="utf-8"))["state"] == "PASSED"
    assert "## OBSERVED_DATA" in report_path.read_text(encoding="utf-8")
    assert list(tmp_path.glob("*.tmp")) == []
