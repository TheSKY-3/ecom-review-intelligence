"""Turn stored human checks into a shareable Markdown record.

It reads only the verdict log, so exporting needs no running server and no
access to the review store. Review text is never part of a verdict record, so
the output is safe to hand to someone outside the machine.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path

from ecom_review_intelligence.workbench_contracts import VerdictRecord

_VERDICT_LABELS = {
    "confirmed": "已确认",
    "doubtful": "存疑",
    "rejected": "驳回",
}
_POLARITY_LABELS = {
    "supporting": "支持",
    "opposing": "反对",
    "conflicting": "冲突",
    "insufficient": "信息不足",
}


def read_verdicts(path: Path, run_token: str | None = None) -> list[VerdictRecord]:
    """Read the JSONL log, skipping unreadable lines and keeping the latest per card."""

    if not path.exists():
        return []
    latest: dict[tuple[str, str], VerdictRecord] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                record = VerdictRecord.model_validate_json(line)
            except ValueError:
                continue
            if run_token is not None and record.run_token != run_token:
                continue
            latest[(record.run_token, record.card_alias)] = record
    return list(latest.values())


def _run_groups(records: Iterable[VerdictRecord]) -> dict[str, list[VerdictRecord]]:
    groups: dict[str, list[VerdictRecord]] = {}
    for record in records:
        groups.setdefault(record.run_token, []).append(record)
    for items in groups.values():
        items.sort(key=lambda item: item.card_alias)
    return groups


def render_markdown(records: Sequence[VerdictRecord]) -> str:
    """Build the export document: what was reviewed, by whom, and what was decided."""

    if not records:
        return "# 人工核对记录\n\n这次运行还没有核对记录。\n"
    lines = ["# 人工核对记录", ""]
    for run_token, items in _run_groups(records).items():
        first = items[0]
        counts = Counter(item.verdict for item in items)
        lines.append(f"## {first.run_alias}")
        lines.append("")
        lines.append(f"- 运行号：`{run_token}`")
        lines.append(f"- 流程：{first.workflow.value}")
        lines.append(f"- 维度：{first.aspect.value}")
        labels = "、".join(first.product_labels) or "（未记录）"
        lines.append(f"- 商品：{labels}")
        lines.append(f"- 匹配评论 {first.matched_count} 条 · 证据卡 {first.evidence_count} 张")
        lines.append(
            "- 核对进度："
            f"{len(items)}/{first.evidence_count}"
            f"（确认 {counts['confirmed']} · 存疑 {counts['doubtful']} · 驳回 {counts['rejected']}）"
        )
        lines.append("")
        lines.append("| 证据卡 | 方向 | 强度 | 人工判定 | 记录时间 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for item in items:
            polarity = _POLARITY_LABELS.get(item.polarity or "", item.polarity or "-")
            lines.append(
                f"| {item.card_alias} | {polarity} | {item.strength or '-'} | "
                f"{_VERDICT_LABELS.get(item.verdict, item.verdict)} | {item.recorded_at} |"
            )
        lines.append("")
    lines.append(
        "> 本文件由本地核对记录生成,只含证据卡编号、方向、强度与人工判定,"
        "不含评论原文,可直接分享给不需要看到原文的人。"
    )
    lines.append("")
    return "\n".join(lines)


def dump_records(records: Sequence[VerdictRecord]) -> str:
    """Machine-readable form, useful when the export feeds another tool."""

    return json.dumps(
        [record.model_dump(mode="json") for record in records],
        ensure_ascii=False,
        indent=1,
        sort_keys=True,
    )
