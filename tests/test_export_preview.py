import csv
import json
from pathlib import Path

import pytest

from ecom_review_intelligence.export_preview import PreviewError, latest_export, render
from ecom_review_intelligence.review_export import COLUMNS


def _export(directory: Path, rows: list[list[str]]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "reviews.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        writer.writerows(rows)
    (directory / "meta.json").write_text(
        json.dumps(
            {
                "product_code": "frostline",
                "product_label": "Frostline",
                "aspect": "noise",
                "matched_total": 900,
                "dataset_snapshot_id": "f" * 64,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return directory


def _row(bucket: str, text: str, judged: str = "否", evidence: str = "e1") -> list[str]:
    row = [""] * len(COLUMNS)
    row[0] = evidence
    row[1] = "frostline"
    row[4] = bucket
    row[6] = "5"
    row[11] = judged
    row[12] = text
    return row


def test_review_text_is_escaped_not_executed(tmp_path: Path) -> None:
    directory = _export(
        tmp_path / "export",
        [_row("反对", '<script>alert("x")</script> & done', evidence="evil")],
    )

    page = render(directory).read_text(encoding="utf-8")

    assert "<script>alert" not in page
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; done" in page
    # Only the one inline behaviour script exists; no injected or remote script.
    assert page.count("<script>") == 1
    assert "src=" not in page


def test_every_row_and_bucket_is_present(tmp_path: Path) -> None:
    directory = _export(
        tmp_path / "export",
        [
            _row("支持", "quiet", evidence="a"),
            _row("冲突", "quiet but loud", evidence="b"),
            _row("证据不足", "makes ice", evidence="c"),
        ],
    )

    page = render(directory).read_text(encoding="utf-8")

    assert 'data-bucket="支持"' in page
    assert 'data-bucket="冲突"' in page
    assert 'data-bucket="证据不足"' in page
    assert "全部 3" in page
    assert page.count("<tr data-bucket=") == 3
    assert "全库匹配 900 条 · 导出 3 条" in page


def test_judged_rows_are_marked_for_the_only_judged_filter(tmp_path: Path) -> None:
    directory = _export(
        tmp_path / "export",
        [_row("支持", "quiet", judged="是", evidence="a"), _row("支持", "quiet", evidence="b")],
    )

    page = render(directory).read_text(encoding="utf-8")

    assert page.count('data-judged="1"') == 1
    assert page.count('data-judged="0"') == 1
    assert "参与判断 1 条" in page


def test_missing_csv_is_refused(tmp_path: Path) -> None:
    with pytest.raises(PreviewError, match="Missing"):
        render(tmp_path / "nothing")


def test_latest_export_picks_the_newest_directory(tmp_path: Path) -> None:
    _export(tmp_path / "tmp" / "exports" / "frostline-noise-20260101-000000", [])
    newest = _export(tmp_path / "tmp" / "exports" / "frostline-noise-20260102-000000", [])

    assert latest_export(tmp_path) == newest


def test_latest_export_without_any_export_is_refused(tmp_path: Path) -> None:
    with pytest.raises(PreviewError, match="No export"):
        latest_export(tmp_path)
