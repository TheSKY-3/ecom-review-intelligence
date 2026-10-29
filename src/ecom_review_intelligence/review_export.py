"""Export one product-and-aspect review set as a table a human can audit.

This is the bounded half of the "hand it to a person" workflow: read every
matching review, label it with the same frozen polarity rule the agent used, and
write a CSV in a local-only directory. It deliberately does not collect answers
back, reconcile labels, or change any rule.

Two honesty constraints shape the file:

* Every row says whether it was one of the few reviews the agent actually
  judged, so a reader cannot mistake the whole sheet for the evidence behind a
  conclusion.
* The sheet carries the match totals and the frozen lineage, so it can still be
  tied to one dataset snapshot months later.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .engine import (
    Evidence,
    count_aspect_matches,
    polarity_hits,
    query_aspect_evidence,
    read_store_meta,
)
from .label_backfill import POLARITIES

EXPORT_SCHEMA_VERSION = "p7-review-export-v1"
MAX_EXPORT_ROWS = 5000

BUCKET_LABELS: dict[str, str] = {
    "supporting": "支持",
    "opposing": "反对",
    "conflicting": "冲突",
    "insufficient": "证据不足",
}

COLUMNS: tuple[str, ...] = (
    "证据ID",
    "商品代码",
    "父ASIN",
    "维度",
    "极性桶",
    "命中词",
    "星级",
    "评论时间(UTC)",
    "已验证购买",
    "有用票",
    "行号",
    "参与本次判断",
    "评论原文",
    "【人工】是否同意",
    "【人工】更合适的桶",
    "【人工】备注",
)


class ExportError(RuntimeError):
    """Raised when an export request cannot be honoured."""


@dataclass(frozen=True)
class ExportRow:
    evidence_id: str
    product_code: str
    product_id: str
    aspect: str
    polarity: str
    hit_terms: tuple[str, ...]
    rating: float | None
    timestamp: int | None
    verified_purchase: bool | None
    helpful_vote: int | None
    source_line: int
    participated: bool
    text: str

    @property
    def bucket_label(self) -> str:
        return BUCKET_LABELS[self.polarity]

    def as_csv_row(self) -> list[str]:
        return [
            self.evidence_id,
            self.product_code,
            self.product_id,
            self.aspect,
            self.bucket_label,
            " ".join(self.hit_terms) or "-",
            "" if self.rating is None else f"{self.rating:g}",
            format_review_timestamp(self.timestamp),
            "" if self.verified_purchase is None else ("是" if self.verified_purchase else "否"),
            "" if self.helpful_vote is None else str(self.helpful_vote),
            str(self.source_line),
            "是" if self.participated else "否",
            self.text,
            "",
            "",
            "",
        ]


@dataclass(frozen=True)
class ExportResult:
    directory: Path
    csv_path: Path
    rows: tuple[ExportRow, ...]
    matched_total: int
    truncated: bool
    bucket_counts: dict[str, int]
    participated_count: int
    split_paths: tuple[Path, ...]

    def summary(self) -> dict[str, Any]:
        return {
            "status": "EXPORTED",
            "schema_version": EXPORT_SCHEMA_VERSION,
            "directory": str(self.directory),
            "csv": str(self.csv_path),
            "exported_rows": len(self.rows),
            "matched_total": self.matched_total,
            "truncated": self.truncated,
            "participated_count": self.participated_count,
            "bucket_counts": self.bucket_counts,
            "split_files": [str(path) for path in self.split_paths],
        }


def format_review_timestamp(value: int | None) -> str:
    """Format an epoch value as UTC minutes, accepting seconds or milliseconds.

    The frozen store keeps epoch milliseconds; other callers pass seconds. A
    value that cannot be formatted returns an empty cell rather than a guess,
    but the unit is detected by magnitude so a valid stamp is never dropped.
    """

    if value is None:
        return ""
    try:
        stamp = int(value)
    except (TypeError, ValueError):
        return ""
    if stamp >= 100_000_000_000:  # 13+ digits: epoch milliseconds
        stamp //= 1000
    try:
        return datetime.fromtimestamp(stamp, tz=UTC).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return ""


def build_export_rows(
    store_path: Path,
    *,
    product_code: str,
    product_id: str,
    aspect: str,
    participated_ids: frozenset[str],
    max_rows: int = MAX_EXPORT_ROWS,
) -> tuple[tuple[ExportRow, ...], int, bool]:
    """Return every matching review as an export row, plus the pre-cap total."""

    if max_rows <= 0:
        raise ExportError("Export row cap must be positive.")
    metadata = read_store_meta(store_path)
    try:
        aspect_terms = json.loads(metadata["aspect_terms"])
        polarity_terms = json.loads(metadata["polarity_terms"])
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ExportError("The store's frozen vocabulary is missing or invalid.") from error
    if aspect not in aspect_terms:
        raise ExportError("The requested aspect is not enabled by the frozen contract.")

    evidence = query_aspect_evidence(store_path, (product_id,), aspect)
    matched_total = count_aspect_matches(store_path, (product_id,), aspect)
    truncated = len(evidence) > max_rows
    if truncated:
        evidence = evidence[:max_rows]
    rows = tuple(_to_row(item, product_code, product_id, aspect, polarity_terms, participated_ids) for item in evidence)
    return rows, matched_total, truncated


def _to_row(
    item: Evidence,
    product_code: str,
    product_id: str,
    aspect: str,
    polarity_terms: dict[str, list[str]],
    participated_ids: frozenset[str],
) -> ExportRow:
    bucket, hits = polarity_hits(item.text, polarity_terms)
    return ExportRow(
        evidence_id=item.evidence_id,
        product_code=product_code,
        product_id=product_id,
        aspect=aspect,
        polarity=bucket,
        hit_terms=hits,
        rating=item.rating,
        timestamp=item.timestamp,
        verified_purchase=item.verified_purchase,
        helpful_vote=item.helpful_vote,
        source_line=item.source_line,
        participated=item.evidence_id in participated_ids,
        text=item.text,
    )


def write_export(
    directory: Path,
    rows: tuple[ExportRow, ...],
    *,
    meta: dict[str, Any],
    split: bool = False,
) -> tuple[Path, tuple[Path, ...]]:
    """Write the sheet, an optional per-bucket split, a sidecar, and a readme."""

    directory.mkdir(parents=True, exist_ok=True)
    csv_path = directory / "reviews.csv"
    _write_csv(csv_path, rows)

    split_paths: list[Path] = []
    if split:
        for bucket in POLARITIES:
            subset = tuple(row for row in rows if row.polarity == bucket)
            if not subset:
                continue
            path = directory / f"reviews-{bucket}.csv"
            _write_csv(path, subset)
            split_paths.append(path)

    (directory / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (directory / "说明.md").write_text(_readme(meta, rows), encoding="utf-8")
    return csv_path, tuple(split_paths)


def _write_csv(path: Path, rows: tuple[ExportRow, ...]) -> None:
    # utf-8-sig writes a BOM so Excel opens the Chinese headers instead of mojibake.
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for row in rows:
            writer.writerow(row.as_csv_row())


def _readme(meta: dict[str, Any], rows: tuple[ExportRow, ...]) -> str:
    counts = Counter(row.bucket_label for row in rows)
    breakdown = "、".join(f"{label} {counts.get(label, 0)} 条" for label in BUCKET_LABELS.values())
    participated = sum(1 for row in rows if row.participated)
    return "\n".join(
        [
            "# 评论导出说明",
            "",
            f"- 商品：{meta.get('product_label', '')}（{meta.get('product_code', '')}）",
            f"- 维度：{meta.get('aspect', '')}",
            f"- 全库匹配：{meta.get('matched_total', 0)} 条；本表导出 {len(rows)} 条",
            f"- 极性分布：{breakdown}",
            f"- 其中参与过本次判断：{participated} 条（该列标「是」的行）",
            "",
            "## 怎么读这张表",
            "",
            "1. 先看「参与本次判断」为「是」的几行——那才是支撑当前结论的证据。",
            "2. 「极性桶」由词表规则判定，不是模型判断；「命中词」是判定的依据。",
            "3. 「冲突」表示同一条评论里支持和反对的词同时出现，最需要人工裁决。",
            "4. 最后三列留空，供人工填写；本工具不收集、不回读这些填写内容。",
            "",
            "## 边界",
            "",
            "- 含原始评论文本，仅限本机使用，不进入版本库。",
            f"- 数据集快照：{meta.get('dataset_snapshot_id', '')}",
            f"- 契约哈希：{meta.get('contract_sha256', '')}",
            f"- 导出时间(UTC)：{meta.get('generated_at', '')}",
            "",
        ]
    )


def export_product_aspect(
    store_path: Path,
    directory: Path,
    *,
    product_code: str,
    product_id: str,
    product_label: str,
    aspect: str,
    participated_ids: frozenset[str],
    judged_limit: int,
    max_rows: int = MAX_EXPORT_ROWS,
    split: bool = False,
    generated_at: str | None = None,
) -> ExportResult:
    """Export one product-and-aspect review set and return what was written."""

    rows, matched_total, truncated = build_export_rows(
        store_path,
        product_code=product_code,
        product_id=product_id,
        aspect=aspect,
        participated_ids=participated_ids,
        max_rows=max_rows,
    )
    metadata = read_store_meta(store_path)
    bucket_counts = {bucket: 0 for bucket in POLARITIES}
    for row in rows:
        bucket_counts[row.polarity] += 1
    meta: dict[str, Any] = {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "generated_at": generated_at or datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "product_code": product_code,
        "product_label": product_label,
        "product_id": product_id,
        "aspect": aspect,
        "judged_limit": judged_limit,
        "matched_total": matched_total,
        "exported_rows": len(rows),
        "truncated": truncated,
        "max_rows": max_rows,
        "bucket_counts": bucket_counts,
        "participated_count": sum(1 for row in rows if row.participated),
        "dataset_snapshot_id": metadata.get("dataset_snapshot_id", ""),
        "contract_sha256": metadata.get("contract_sha256", ""),
        "data_boundary": "Contains raw review text. Local-only; never commit.",
    }
    csv_path, split_paths = write_export(directory, rows, meta=meta, split=split)
    return ExportResult(
        directory=directory,
        csv_path=csv_path,
        rows=rows,
        matched_total=matched_total,
        truncated=truncated,
        bucket_counts=bucket_counts,
        participated_count=meta["participated_count"],
        split_paths=split_paths,
    )
