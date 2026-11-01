"""Aggregate human card verdicts into a reviewed label set with divergence stats.

The verdict log records one click per line; this module collapses it into the
formal "reviewed" tier the master plan defines (human-checked, below gold):
one row per reviewed card across runs, the final verdict, and how the verdict
moved between runs. It reads only the verdict log, so it needs no server and
no access to the review store, and the output never contains review text.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from ecom_review_intelligence.verdict_export import read_verdicts
from ecom_review_intelligence.workbench_contracts import VerdictRecord

SCHEMA_VERSION = "reviewed-verdicts-v1"

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


def _card_key(record: VerdictRecord) -> tuple[str, str, str, tuple[str, ...]]:
    """Identity of a card across runs: the sampled cards are deterministic."""

    return (
        record.workflow.value,
        record.aspect.value,
        record.card_alias,
        tuple(record.product_labels),
    )


def build_summary(records: list[VerdictRecord], *, generated_at: str) -> dict[str, object]:
    """Collapse deduplicated verdicts into one reviewed row per card."""

    grouped: dict[tuple[str, str, str, tuple[str, ...]], list[VerdictRecord]] = {}
    for record in records:
        grouped.setdefault(_card_key(record), []).append(record)

    cards: list[dict[str, object]] = []
    verdict_totals: Counter[str] = Counter()
    flipped = 0
    for key, items in grouped.items():
        ordered = sorted(enumerate(items), key=lambda pair: (pair[1].recorded_at, pair[0]))
        history = [item.verdict for _, item in ordered]
        final = ordered[-1][1]
        if len(set(history)) > 1:
            flipped += 1
        verdict_totals[final.verdict] += 1
        workflow, aspect, card_alias, product_labels = key
        cards.append(
            {
                "workflow": workflow,
                "aspect": aspect,
                "card_alias": card_alias,
                "product_labels": list(product_labels),
                "polarity": final.polarity,
                "strength": final.strength,
                "matched_count": final.matched_count,
                "evidence_count": final.evidence_count,
                "final_verdict": final.verdict,
                "times_reviewed": len(history),
                "verdict_history": history,
                "final_run_alias": final.run_alias,
                "final_recorded_at": final.recorded_at,
            }
        )
    cards.sort(key=lambda card: (str(card["aspect"]), str(card["product_labels"]), str(card["card_alias"])))

    total = len(cards)
    aspect_totals: dict[str, Counter[str]] = {}
    for card in cards:
        counter = aspect_totals.setdefault(str(card["aspect"]), Counter())
        counter[str(card["final_verdict"])] += 1

    confirmed = verdict_totals["confirmed"]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "cards_total": total,
        "runs_total": len({record.run_token for record in records}),
        "verdict_totals": {
            "confirmed": confirmed,
            "doubtful": verdict_totals["doubtful"],
            "rejected": verdict_totals["rejected"],
        },
        "confirmation_rate": round(confirmed / total, 4) if total else 0.0,
        "divergence_rate": round((total - confirmed) / total, 4) if total else 0.0,
        "flipped_cards": flipped,
        "aspect_totals": {
            aspect: dict(counter) for aspect, counter in sorted(aspect_totals.items())
        },
        "cards": cards,
    }


def render_markdown(summary: dict[str, object]) -> str:
    """Human-readable report; safe to share because it never holds review text."""

    total = int(summary["cards_total"])  # type: ignore[arg-type]
    lines = ["# 人工核对评估集（reviewed 档）", ""]
    if not total:
        lines.append("还没有任何人工核对记录；在证据卡上点「确认 / 存疑 / 驳回」后重跑本脚本。")
        lines.append("")
        return "\n".join(lines)

    totals = summary["verdict_totals"]
    lines.append(f"- 核对卡片：{total} 张（来自 {summary['runs_total']} 次运行）")
    lines.append(
        "- 最终判定："
        f"确认 {totals['confirmed']} · 存疑 {totals['doubtful']} · 驳回 {totals['rejected']}"  # type: ignore[index]
    )
    lines.append(
        f"- 接受率（确认占比）：{summary['confirmation_rate']} · "
        f"分歧率（存疑+驳回占比）：{summary['divergence_rate']}"
    )
    if int(summary["flipped_cards"] or 0):  # type: ignore[arg-type]
        lines.append(
            f"- 跨运行改判：{summary['flipped_cards']} 张卡在不同运行里给出过不同判定"  # type: ignore[str-bytes-safe]
        )
    lines.append("")
    lines.append("| 维度 | 商品 | 证据卡 | 机器方向 | 机器强度 | 最终判定 | 核对次数 | 判定轨迹 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for card in summary["cards"]:  # type: ignore[union-attr]
        polarity = _POLARITY_LABELS.get(str(card["polarity"]), str(card["polarity"] or "-"))
        history = " → ".join(
            _VERDICT_LABELS.get(verdict, verdict) for verdict in card["verdict_history"]  # type: ignore[arg-type]
        )
        lines.append(
            f"| {card['aspect']} | {'、'.join(card['product_labels']) or '（未记录）'} "  # type: ignore[str-bytes-safe]
            f"| {card['card_alias']} | {polarity} | {card['strength'] or '-'} "
            f"| {_VERDICT_LABELS[str(card['final_verdict'])]} | {card['times_reviewed']} | {history} |"
        )
    lines.append("")
    lines.append(
        "> 本文件只含证据卡编号、方向、强度与人工判定，不含评论原文、"
        "不含本地路径；「机器方向/强度」是证据卡上的判定，人工判定是对它的核对结论。"
    )
    lines.append("")
    return "\n".join(lines)


def summarize_log(path: Path) -> dict[str, object]:
    """Read a verdict log and return the reviewed summary for it."""

    records = read_verdicts(Path(path))
    generated_at = datetime.now(UTC).isoformat()
    return build_summary(records, generated_at=generated_at)


def write_reports(
    summary: dict[str, object], output_dir: Path
) -> tuple[Path, Path]:
    """Write the JSON summary and the Markdown report, returning both paths."""

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "reviewed-verdict-labels.json"
    markdown_path = output_dir / "reviewed-verdict-labels.md"
    json_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    markdown_path.write_text(render_markdown(summary), encoding="utf-8", newline="\n")
    return json_path, markdown_path
