"""Render an exported reviews.csv as a read-only local HTML table.

The CSV is fine for Excel but unreadable in a browser. This writes a
self-contained ``preview.html`` next to it so the same file can be inspected
visually: search, filter by bucket, and isolate the few rows that actually fed
the conclusion.

Review text is untrusted data, so every cell is HTML-escaped and the page loads
no external assets. Nothing here fetches, uploads, or rewrites the export.
"""

from __future__ import annotations

import csv
import html
import json
from pathlib import Path

BUCKET_CLASS = {
    "支持": "supporting",
    "反对": "opposing",
    "冲突": "conflicting",
    "证据不足": "insufficient",
}


class PreviewError(RuntimeError):
    """Raised when there is no export to render."""

STYLE = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { margin: 0; font: 14px/1.5 "Segoe UI", "Microsoft YaHei", sans-serif; }
header { padding: 14px 18px; border-bottom: 1px solid #8884; position: sticky; top: 0;
  background: Canvas; z-index: 2; }
h1 { margin: 0 0 6px; font-size: 17px; }
.meta { color: #888; font-size: 12.5px; }
.controls { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-top: 10px; }
input[type=search] { padding: 6px 9px; width: 260px; border: 1px solid #8886;
  border-radius: 6px; background: Canvas; color: inherit; font: inherit; }
button { padding: 6px 11px; border: 1px solid #8886; border-radius: 6px;
  background: Canvas; color: inherit; font: inherit; cursor: pointer; }
button[aria-pressed=true] { background: #2f6fed; border-color: #2f6fed; color: #fff; }
.count { margin-left: auto; color: #888; font-size: 12.5px; }
table { border-collapse: collapse; width: 100%; }
th, td { border-bottom: 1px solid #8883; padding: 6px 9px; text-align: left;
  vertical-align: top; }
th { position: sticky; top: 118px; background: Canvas; font-weight: 600;
  font-size: 12.5px; white-space: nowrap; z-index: 1; }
td.num { white-space: nowrap; font-variant-numeric: tabular-nums; }
td.text { min-width: 420px; }
tr.participated { background: #2f6fed14; }
.pill { display: inline-block; padding: 1px 8px; border-radius: 999px;
  font-size: 12px; white-space: nowrap; }
.supporting { background: #1a7f371f; color: #1a7f37; }
.opposing { background: #d1242f1f; color: #d1242f; }
.conflicting { background: #bf87001f; color: #997404; }
.insufficient { background: #6e77811f; color: #6e7781; }
.when { color: #888; }
"""

SCRIPT = """
const rows = [...document.querySelectorAll('tbody tr')];
const search = document.querySelector('#q');
const onlyJudged = document.querySelector('#judged');
const chips = [...document.querySelectorAll('.bucket')];
let active = 'all';
function apply() {
  const term = search.value.trim().toLowerCase();
  let shown = 0;
  for (const row of rows) {
    const okBucket = active === 'all' || row.dataset.bucket === active;
    const okJudged = !onlyJudged.checked || row.dataset.judged === '1';
    const okText = !term || row.dataset.search.includes(term);
    const show = okBucket && okJudged && okText;
    row.hidden = !show;
    if (show) shown += 1;
  }
  document.querySelector('#count').textContent = `显示 ${shown} / ${rows.length} 行`;
}
search.addEventListener('input', apply);
onlyJudged.addEventListener('change', apply);
for (const chip of chips) {
  chip.addEventListener('click', () => {
    active = chip.dataset.bucket;
    for (const other of chips) other.setAttribute('aria-pressed', String(other === chip));
    apply();
  });
}
apply();
"""


def _load(directory: Path) -> tuple[list[str], list[list[str]], dict[str, object]]:
    csv_path = directory / "reviews.csv"
    if not csv_path.exists():
        raise PreviewError(f"Missing {csv_path}.")
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        table = list(csv.reader(handle))
    meta_path = directory / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    return table[0], table[1:], meta


def render(directory: Path) -> Path:
    header, rows, meta = _load(directory)
    index = {name: position for position, name in enumerate(header)}
    bucket_at = index["极性桶"]
    judged_at = index["参与本次判断"]

    counts: dict[str, int] = {}
    for row in rows:
        counts[row[bucket_at]] = counts.get(row[bucket_at], 0) + 1
    judged = sum(1 for row in rows if row[judged_at] == "是")

    chips = (
        f'<button class="bucket" data-bucket="all" aria-pressed="true">'
        f"全部 {len(rows)}</button>"
    ) + "".join(
        f'<button class="bucket" data-bucket="{html.escape(name)}" aria-pressed="false">'
        f"{html.escape(name)} {count}</button>"
        for name, count in counts.items()
    )
    body: list[str] = []
    for row in rows:
        bucket = row[bucket_at]
        css = BUCKET_CLASS.get(bucket, "insufficient")
        cells: list[str] = []
        for position, name in enumerate(header):
            value = row[position]
            if name == "极性桶":
                cells.append(f'<td><span class="pill {css}">{html.escape(value)}</span></td>')
            elif name == "评论原文":
                cells.append(f'<td class="text">{html.escape(value)}</td>')
            elif name.startswith("【人工】"):
                cells.append('<td class="when">—</td>')
            elif name == "评论时间(UTC)":
                cells.append(f'<td class="num when">{html.escape(value)}</td>')
            else:
                cells.append(f'<td class="num">{html.escape(value)}</td>')
        search_blob = html.escape(" ".join(row).lower(), quote=True)
        body.append(
            f'<tr data-bucket="{html.escape(bucket)}" '
            f'data-judged="{"1" if row[judged_at] == "是" else "0"}" '
            f'data-search="{search_blob}"'
            f'{" class=participated" if row[judged_at] == "是" else ""}>'
            + "".join(cells)
            + "</tr>"
        )

    title = html.escape(f"{meta.get('product_label') or meta.get('product_code', '')} × "
                        f"{meta.get('aspect', '')}")
    summary = html.escape(
        f"全库匹配 {meta.get('matched_total', len(rows))} 条 · 导出 {len(rows)} 条 · "
        f"参与判断 {judged} 条 · 快照 {str(meta.get('dataset_snapshot_id', ''))[:12]}"
    )
    page = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} 评论导出预览</title>
<style>{STYLE}</style></head>
<body>
<header>
  <h1>{title} · 评论导出</h1>
  <div class="meta">{summary}</div>
  <div class="controls">
    <input id="q" type="search" placeholder="搜索评论原文 / 命中词 / 证据ID"
           aria-label="搜索">
    <label><input id="judged" type="checkbox"> 只看参与判断的行</label>
    {chips}
    <span class="count" id="count"></span>
  </div>
</header>
<table>
  <thead><tr>{"".join(f"<th>{html.escape(name)}</th>" for name in header)}</tr></thead>
  <tbody>{"".join(body)}</tbody>
</table>
<script>{SCRIPT}</script>
</body></html>
"""
    target = directory / "preview.html"
    target.write_text(page, encoding="utf-8")
    return target


def latest_export(root: Path) -> Path:
    """Return the newest export directory under ``root`` or raise PreviewError."""

    found = sorted((root / "tmp" / "exports").glob("*/reviews.csv"))
    if not found:
        raise PreviewError("No export found under tmp/exports.")
    return found[-1].parent
