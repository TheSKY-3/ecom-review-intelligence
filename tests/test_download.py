from __future__ import annotations

import gzip
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Self

import pytest

from ecom_review_intelligence.download import (
    DownloadError,
    DownloadResult,
    RemoteObject,
    download_remote,
    inspect_transfer_lock,
    probe_remote,
    recover_transfer_lock,
    register_existing,
    write_download_outputs,
)

REVIEW_URL = "https://example.test/Appliances.jsonl.gz"
PAYLOAD = gzip.compress(b"valid gzip test payload", mtime=0)
SPLIT_AT = len(PAYLOAD) // 2


@dataclass
class FakeResponse:
    status: int
    headers: dict[str, str]
    body: bytes
    fail_after: int | None = None
    position: int = 0

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        return None

    def read(self, amount: int) -> bytes:
        if self.fail_after is not None and self.position >= self.fail_after:
            raise OSError("simulated interruption")
        end = min(self.position + amount, len(self.body))
        if self.fail_after is not None:
            end = min(end, self.fail_after)
        chunk = self.body[self.position : end]
        self.position = end
        return chunk


def make_remote(*, etag: str = '"stable"') -> RemoteObject:
    return RemoteObject(
        url=REVIEW_URL,
        total_bytes=len(PAYLOAD),
        content_type="application/gzip",
        accept_ranges=True,
        etag=etag,
        last_modified="Thu, 16 Jan 2025 22:41:53 GMT",
    )


def make_range_opener(
    remote: RemoteObject,
    *,
    fail_after: int | None = None,
):
    def opener(request: object, timeout: float) -> FakeResponse:
        range_header = request.get_header("Range")
        assert range_header is not None
        start = int(range_header.removeprefix("bytes=").removesuffix("-"))
        assert request.full_url == remote.url
        assert timeout == 30.0
        return FakeResponse(
            status=206,
            headers={
                "Content-Range": f"bytes {start}-{len(PAYLOAD) - 1}/{len(PAYLOAD)}",
                "Content-Type": "application/gzip",
                "Accept-Ranges": "bytes",
                "ETag": remote.etag or "",
                "Last-Modified": remote.last_modified or "",
            },
            body=PAYLOAD[start:],
            fail_after=fail_after,
        )

    return opener


def test_probe_remote_records_identity_from_one_byte_range_response() -> None:
    seen: dict[str, object] = {}

    def opener(request: object, timeout: float) -> FakeResponse:
        seen["range"] = request.get_header("Range")
        seen["timeout"] = timeout
        return FakeResponse(
            status=206,
            headers={
                "Content-Range": f"bytes 0-0/{len(PAYLOAD)}",
                "Content-Type": "application/gzip",
                "Accept-Ranges": "bytes",
                "ETag": '"stable"',
                "Last-Modified": "Thu, 16 Jan 2025 22:41:53 GMT",
            },
            body=PAYLOAD[:1],
        )

    remote = probe_remote(REVIEW_URL, opener=opener)

    assert seen == {"range": "bytes=0-0", "timeout": 30.0}
    assert remote.total_bytes == len(PAYLOAD)
    assert remote.etag == '"stable"'
    assert remote.accept_ranges is True


def test_download_remote_resumes_matching_partial_file(tmp_path: Path) -> None:
    target = tmp_path / "reviews.jsonl.gz"
    remote = make_remote()

    with pytest.raises(OSError, match="simulated interruption"):
        download_remote(
            "reviews",
            remote,
            target,
            opener=make_range_opener(remote, fail_after=SPLIT_AT),
        )

    result = download_remote("reviews", remote, target, opener=make_range_opener(remote))

    assert target.read_bytes() == PAYLOAD
    assert result.resumed_from_bytes == SPLIT_AT
    assert not target.with_suffix(".gz.part").exists()
    assert target.with_suffix(".gz.receipt.json").exists()


def test_download_remote_rejects_changed_remote_before_append(tmp_path: Path) -> None:
    target = tmp_path / "reviews.jsonl.gz"
    stable_remote = make_remote()

    with pytest.raises(OSError, match="simulated interruption"):
        download_remote(
            "reviews",
            stable_remote,
            target,
            opener=make_range_opener(stable_remote, fail_after=SPLIT_AT),
        )

    changed_remote = make_remote(etag='"changed"')

    def no_network_opener(request: object, timeout: float) -> FakeResponse:
        pytest.fail("changed remote must be rejected before opening the network response")

    with pytest.raises(DownloadError, match="ETag"):
        download_remote("reviews", changed_remote, target, opener=no_network_opener)

    assert target.with_suffix(".gz.part").read_bytes() == PAYLOAD[:SPLIT_AT]


def test_download_remote_rejects_complete_file_without_receipt(tmp_path: Path) -> None:
    target = tmp_path / "reviews.jsonl.gz"
    target.write_bytes(PAYLOAD)

    with pytest.raises(DownloadError, match="receipt"):
        download_remote("reviews", make_remote(), target, opener=make_range_opener(make_remote()))


def test_write_download_outputs_exposes_registration_without_raw_content(tmp_path: Path) -> None:
    results = (
        DownloadResult(
            source_name="reviews",
            url=REVIEW_URL,
            final_path=Path("data/raw/Appliances.jsonl.gz"),
            total_bytes=len(PAYLOAD),
            sha256="a" * 64,
            etag='"stable"',
            last_modified="Thu, 16 Jan 2025 22:41:53 GMT",
            resumed_from_bytes=0,
        ),
        DownloadResult(
            source_name="metadata",
            url="https://example.test/meta_Appliances.jsonl.gz",
            final_path=Path("data/raw/meta_Appliances.jsonl.gz"),
            total_bytes=10,
            sha256="b" * 64,
            etag='"stable-meta"',
            last_modified="Thu, 16 Jan 2025 22:01:05 GMT",
            resumed_from_bytes=10,
        ),
    )

    manifest_path, report_path = write_download_outputs(
        tmp_path,
        results,
        observed_at="2026-08-02T00:00:00+00:00",
    )

    assert manifest_path.name == "phase-1-download.json"
    assert report_path.name == "phase-1-download.md"
    assert "sha256" in manifest_path.read_text(encoding="utf-8")
    assert "valid gzip test payload" not in report_path.read_text(encoding="utf-8")
    assert list(tmp_path.rglob("*.tmp")) == []


def test_register_existing_records_manual_download_without_touching_stale_partial(
    tmp_path: Path,
) -> None:
    target = tmp_path / "reviews.jsonl.gz"
    target.write_bytes(PAYLOAD)
    stale_partial = target.with_suffix(".gz.part")
    stale_partial.write_bytes(b"stale partial data")

    result = register_existing("reviews", make_remote(), target)

    receipt = json.loads(target.with_suffix(".gz.receipt.json").read_text(encoding="utf-8"))
    assert result.acquisition_method == "manual_direct_download"
    assert result.resumed_from_bytes == len(PAYLOAD)
    assert receipt["sha256"] == result.sha256
    assert receipt["acquisition_method"] == "manual_direct_download"
    assert stale_partial.read_bytes() == b"stale partial data"


def test_register_existing_rejects_wrong_byte_count_without_receipt(tmp_path: Path) -> None:
    target = tmp_path / "reviews.jsonl.gz"
    target.write_bytes(PAYLOAD)
    wrong_size_remote = RemoteObject(
        url=REVIEW_URL,
        total_bytes=len(PAYLOAD) + 1,
        content_type="application/gzip",
        accept_ranges=True,
        etag='"stable"',
        last_modified="Thu, 16 Jan 2025 22:41:53 GMT",
    )

    with pytest.raises(DownloadError, match="byte count"):
        register_existing("reviews", wrong_size_remote, target)

    assert not target.with_suffix(".gz.receipt.json").exists()


def test_register_existing_rejects_receipt_with_different_hash(tmp_path: Path) -> None:
    target = tmp_path / "reviews.jsonl.gz"
    target.write_bytes(PAYLOAD)
    target.with_suffix(".gz.receipt.json").write_text(
        json.dumps(
            {
                "source_name": "reviews",
                "remote": {
                    "url": REVIEW_URL,
                    "total_bytes": len(PAYLOAD),
                    "etag": '"stable"',
                    "last_modified": "Thu, 16 Jan 2025 22:41:53 GMT",
                },
                "sha256": "0" * 64,
                "acquisition_method": "manual_direct_download",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(DownloadError, match="SHA-256"):
        register_existing("reviews", make_remote(), target)


def test_write_download_outputs_records_snapshot_and_manual_provenance(tmp_path: Path) -> None:
    results = (
        DownloadResult(
            source_name="reviews",
            url=REVIEW_URL,
            final_path=Path("data/raw/Appliances.jsonl.gz"),
            total_bytes=len(PAYLOAD),
            sha256="a" * 64,
            etag='"stable"',
            last_modified="Thu, 16 Jan 2025 22:41:53 GMT",
            resumed_from_bytes=len(PAYLOAD),
            acquisition_method="manual_direct_download",
        ),
        DownloadResult(
            source_name="metadata",
            url="https://example.test/meta_Appliances.jsonl.gz",
            final_path=Path("data/raw/meta_Appliances.jsonl.gz"),
            total_bytes=10,
            sha256="b" * 64,
            etag='"stable-meta"',
            last_modified="Thu, 16 Jan 2025 22:01:05 GMT",
            resumed_from_bytes=0,
        ),
    )

    manifest_path, report_path = write_download_outputs(
        tmp_path,
        results,
        observed_at="2026-08-03T00:00:00+00:00",
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_snapshot = __import__("hashlib").sha256(
        b"reviews:" + b"a" * 64 + b"\nmetadata:" + b"b" * 64 + b"\n"
    ).hexdigest()
    report = report_path.read_text(encoding="utf-8")
    assert manifest["dataset_snapshot_id"] == expected_snapshot
    assert manifest["sources"][0]["acquisition_method"] == "manual_direct_download"
    assert "manual_direct_download" in report
    assert "browser HTTP receipt" in report


def test_download_remote_rejects_existing_target_lock_before_opening_partial(
    tmp_path: Path,
) -> None:
    target = tmp_path / "reviews.jsonl.gz"
    lock_path = target.with_suffix(".gz.lock.json")
    lock_path.write_text(
        json.dumps(
            {
                "token": "existing-lock",
                "source_name": "reviews",
                "final_filename": target.name,
                "process_id": 42,
                "acquired_at": "2026-08-03T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    def no_network_opener(request: object, timeout: float) -> FakeResponse:
        pytest.fail("locked targets must be rejected before opening the network")

    with pytest.raises(DownloadError, match="locked"):
        download_remote("reviews", make_remote(), target, opener=no_network_opener)

    assert not target.with_suffix(".gz.part").exists()
    assert inspect_transfer_lock(target).token == "existing-lock"


def test_download_remote_rejects_oversized_partial_without_mutating_it(tmp_path: Path) -> None:
    target = tmp_path / "reviews.jsonl.gz"
    oversized_partial = target.with_suffix(".gz.part")
    oversized_partial.write_bytes(PAYLOAD + b"extra")
    target.with_suffix(".gz.part.identity.json").write_text(
        json.dumps(
            {
                "url": REVIEW_URL,
                "total_bytes": len(PAYLOAD),
                "etag": '"stable"',
                "last_modified": "Thu, 16 Jan 2025 22:41:53 GMT",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(DownloadError, match="not smaller"):
        download_remote("reviews", make_remote(), target, opener=make_range_opener(make_remote()))

    assert oversized_partial.read_bytes() == PAYLOAD + b"extra"


def test_recover_transfer_lock_requires_explicit_matching_inspection_token(tmp_path: Path) -> None:
    raw_root = tmp_path / "data" / "raw"
    target = raw_root / "reviews.jsonl.gz"
    target.parent.mkdir(parents=True)
    lock_path = target.with_suffix(".gz.lock.json")
    lock_path.write_text(
        json.dumps(
            {
                "token": "recover-me",
                "source_name": "reviews",
                "final_filename": target.name,
                "process_id": 42,
                "acquired_at": "2026-08-03T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    lock = inspect_transfer_lock(target)

    with pytest.raises(DownloadError, match="inspection token"):
        recover_transfer_lock(target, raw_root=raw_root, expected_token="wrong-token")

    recovered = recover_transfer_lock(target, raw_root=raw_root, expected_token=lock.token)

    assert recovered == lock
    assert not lock_path.exists()


def test_recover_transfer_lock_rejects_a_live_owner_process(tmp_path: Path) -> None:
    raw_root = tmp_path / "data" / "raw"
    target = raw_root / "reviews.jsonl.gz"
    target.parent.mkdir(parents=True)
    lock_path = target.with_suffix(".gz.lock.json")
    lock_path.write_text(
        json.dumps(
            {
                "token": "live-owner",
                "source_name": "reviews",
                "final_filename": target.name,
                "process_id": os.getpid(),
                "acquired_at": "2026-08-03T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(DownloadError, match="still active"):
        recover_transfer_lock(target, raw_root=raw_root, expected_token="live-owner")

    assert lock_path.exists()


def test_transfer_lock_releases_after_error_and_does_not_block_other_target(tmp_path: Path) -> None:
    first_target = tmp_path / "reviews.jsonl.gz"
    second_target = tmp_path / "metadata.jsonl.gz"
    remote = make_remote()

    with pytest.raises(OSError, match="simulated interruption"):
        download_remote(
            "reviews",
            remote,
            first_target,
            opener=make_range_opener(remote, fail_after=SPLIT_AT),
        )

    assert inspect_transfer_lock(first_target) is None
    result = download_remote("metadata", remote, second_target, opener=make_range_opener(remote))

    assert result.final_path == second_target
    assert inspect_transfer_lock(second_target) is None
