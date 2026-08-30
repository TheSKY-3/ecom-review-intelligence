"""Export the public LaTeX handbook into an Obsidian-friendly Markdown vault.

The LaTeX project remains the source for the printable handbook. This export is
deliberately a separate, retrieval-oriented representation: one note per phase,
an index, and no raw data, private material, or local runtime artefacts.
"""

from __future__ import annotations

import argparse
import re
import shutil
import zipfile
from pathlib import Path

PHASES = [
    ("00-reading-guide.tex", "00A-项目概览与阅读指南.md", "总览", "passed"),
    ("01-origin-and-value.tex", "01-为什么做这个项目.md", "价值", "passed"),
    ("02-system-roadmap.tex", "02-系统路线图.md", "路线图", "passed"),
    ("03-phase-p0.tex", "03-P0-来源可行性.md", "P0", "passed"),
    ("04-phase-p1.tex", "04-P1-数据下载与冻结.md", "P1", "passed"),
    ("05-phase-p2.tex", "05-P2-数据范围与质量审计.md", "P2", "passed"),
    ("06-phase-p3.tex", "06-P3-证据合同.md", "P3", "passed"),
    ("07-phase-p4.tex", "07-P4-本地证据引擎.md", "P4", "passed"),
    ("08-phase-p5.tex", "08-P5-检索基线.md", "P5", "passed"),
    ("09-phase-p6.tex", "09-P6-受约束Agent.md", "P6", "passed"),
    ("10-phase-p7.tex", "10-P7-运营工作台.md", "P7", "in_progress"),
    ("11-lessons-and-failures.tex", "11-问题修复与失败案例.md", "复盘", "passed"),
    ("12-resume-and-interview.tex", "12-简历与面试问答.md", "面试", "passed"),
    ("13-reproduction.tex", "13-本地复现与Overleaf.md", "复现", "passed"),
]

BOX_TITLES = {
    "factbox": "已有证据支持的事实",
    "inferencebox": "基于证据的推断",
    "advicebox": "建议",
    "boundarybox": "尚待验证或适用边界",
    "beginnerbox": "初学者提示",
    "interviewbox": "面试提示",
}


def _brace_value(line: str, command: str) -> str | None:
    match = re.search(rf"\\{command}\{{(.*?)\}}", line)
    return match.group(1) if match else None


def _clean_inline(value: str) -> str:
    value = value.replace("http://127.0.0.1:8765", "本机回环地址的 8765 端口")
    value = re.sub(r"\\shortstack(?:\[[^\]]*\])?\{(.*?)\}", r"\1", value)
    value = re.sub(r"\\texttt\{([^{}]*)\}", r"`\1`", value)
    value = re.sub(r"\\textbf\{([^{}]*)\}", r"**\1**", value)
    value = re.sub(r"\\textit\{([^{}]*)\}", r"*\1*", value)
    value = re.sub(r"\\textcolor\{[^{}]*\}\{([^{}]*)\}", r"\1", value)
    value = value.replace(r"\statuspassed", "**PASSED**")
    value = value.replace(r"\statusprogress", "**IN_PROGRESS**")
    value = re.sub(r"\\filelink\{([^{}]*)\}", r"`\1`", value)
    value = re.sub(r"\\url\{([^{}]*)\}", r"\1", value)
    value = re.sub(r"\\href\{([^{}]*)\}\{([^{}]*)\}", r"[\2](\1)", value)
    value = value.replace(r"\&", "&").replace(r"\%", "%")
    value = value.replace(r"\_", "_").replace(r"\#", "#")
    value = value.replace(r"\textgreater{}", ">")
    value = value.replace(r"\textless{}", "<")
    value = value.replace(r"\rightarrow", "→").replace(r"\leftarrow", "←")
    value = value.replace("--", "–")
    value = re.sub(r"\$([^$]+)\$", r"\1", value)
    value = re.sub(r"\\(?:small|normalsize|noindent|linebreak|allowbreak)\b", "", value)
    value = value.replace(r"\centering", "")
    value = value.replace(r"\\", " ")
    value = re.sub(r"\\[,;:! ]", " ", value)
    return value.strip()


def latex_to_markdown(source: str) -> str:
    output: list[str] = []
    in_table = False
    table_header_written = False
    in_code = False
    box: str | None = None

    for raw in source.splitlines():
        line = raw.strip()
        if not line:
            if not in_table and not in_code:
                output.append("")
            continue

        if re.match(r"^\\begin\{lstlisting\}(?:\[[^]]*\])?$", line):
            output.extend(["", "```text"])
            in_code = True
            continue
        if line.startswith("\\end{lstlisting}"):
            output.extend(["```", ""])
            in_code = False
            continue
        if in_code:
            output.append(raw.rstrip())
            continue

        begin_box = re.fullmatch(r"\\begin\{([^{}]+)\}", line)
        if begin_box and begin_box.group(1) in BOX_TITLES:
            box = begin_box.group(1)
            output.extend([f"> [!note] {BOX_TITLES[box]}", ">"])
            continue
        if line.startswith("\\end{") and box and line == f"\\end{{{box}}}":
            output.append("")
            box = None
            continue

        if re.match(r"\\begin\{(?:longtable|tabularx|tabular)\}", line):
            in_table = True
            table_header_written = False
            output.extend(["", "<!-- LaTeX table converted for retrieval -->"])
            continue
        if in_table and line.startswith("\\end{"):
            in_table = False
            output.append("")
            continue
        if in_table:
            if line in {r"\toprule", r"\midrule", r"\bottomrule", r"\endfirsthead", r"\endhead"}:
                continue
            line = re.sub(r"\\\\\s*$", "", line)
            if "&" in line:
                cells = [_clean_inline(cell) for cell in line.split("&")]
                cells = [cell.replace("|", "\\|") for cell in cells]
                if output and table_header_written:
                    previous_header = output[-2] if len(output) >= 2 else ""
                    current_header = "| " + " | ".join(cells) + " |"
                    if current_header == previous_header:
                        continue
                output.append("| " + " | ".join(cells) + " |")
                if not table_header_written:
                    output.append("| " + " | ".join("---" for _ in cells) + " |")
                    table_header_written = True
            continue

        if line.startswith(r"\item"):
            output.append("- " + _clean_inline(line[len(r"\item") :].strip()))
            continue
        if line.startswith(r"\chapter"):
            value = _brace_value(line, "chapter")
            if value:
                output.extend([f"# {_clean_inline(value)}", ""])
            continue
        if line.startswith(r"\section"):
            value = _brace_value(line, "section")
            if value:
                output.extend([f"## {_clean_inline(value)}", ""])
            continue
        if line.startswith(r"\subsection"):
            value = _brace_value(line, "subsection")
            if value:
                output.extend([f"### {_clean_inline(value)}", ""])
            continue
        if line.startswith(r"\subsubsection"):
            value = _brace_value(line, "subsubsection")
            if value:
                output.extend([f"#### {_clean_inline(value)}", ""])
            continue
        if line.startswith((r"\begin{", r"\end{")):
            if line in {r"\begin{center}", r"\end{center}", r"\begin{enumerate}", r"\end{enumerate}", r"\begin{itemize}", r"\end{itemize}"}:
                continue
            continue
        if line.startswith(r"\includegraphics"):
            match = re.search(r"\{([^{}]+)\}", line)
            if match:
                asset = Path(match.group(1)).name
                output.extend([f"![电脑端工作台截图](assets/{asset})", ""])
            continue
        if line.startswith(r"\caption"):
            value = _brace_value(line, "caption")
            if value:
                output.append(f"*图注：{_clean_inline(value)}*")
            continue
        if line.startswith(r"\label"):
            continue
        if line.startswith((r"\begin{equation", r"\end{equation")):
            continue

        cleaned = _clean_inline(line)
        if cleaned:
            prefix = "> " if box else ""
            output.append(prefix + cleaned)

    text = "\n".join(output)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text + "\n"


def build_index() -> str:
    lines = [
        "---",
        "type: project-index",
        "project: ECom Review Intelligence Agent",
        "tags: [ecom-review-intelligence, project-handbook, obsidian]",
        "---",
        "",
        "# ECom Review Intelligence Agent 知识库",
        "",
        "> [!info] 使用方式",
        "> 解压后，将整个文件夹复制到 Obsidian Vault。先打开本页，再按 P0 → P7 阅读。",
        "> `.tex` 和 PDF 不放在主要检索路径中；它们是单独的打印版和 Overleaf 编辑附件。",
        "",
        "## 项目主线",
        "",
        "本项目面向 B 端电商运营，把商品评论整理成可以回查的本地证据，支持单品诊断、商品比较和问题分流。核心边界是：模型提出计划和表达，本地代码掌握商品授权、工具执行、证据引用与最终放行。",
        "",
        "## 推荐阅读顺序",
        "",
    ]
    for _, note, label, status in PHASES:
        lines.append(f"- [[{note[:-3]}|{label}]] — {status}")
    lines.extend(
        [
            "",
            "## 复习入口",
            "",
            "- [[11-问题修复与失败案例]]：按问题理解工程判断。",
            "- [[12-简历与面试问答]]：准备项目介绍和追问。",
            "- [[13-本地复现与Overleaf]]：运行项目和重新编译手册。",
            "",
            "## 状态边界",
            "",
            "P0–P6 按当前公开证据记录为 PASSED；P7 仍为 IN_PROGRESS，因为目标运营用户的结构化试用尚未完成。不要把自动化测试或合成工作台截图写成真人业务效果。",
            "",
            "## 公开内容边界",
            "",
            "本包不包含原始评论、真实商品或证据标识、密钥、本机绝对路径、私有简历和岗位规划材料。",
        ]
    )
    return "\n".join(lines) + "\n"


def build_readme() -> str:
    return """# 导入 Obsidian

这是 `ECom Review Intelligence Agent` 的知识库版导入包。

## 导入步骤

1. 解压本 ZIP。
2. 将解压后的 `ECom-Review-Intelligence-KB` 文件夹复制到你的 Obsidian Vault。
3. 在 Obsidian 中打开 `00-项目总览与阅读顺序.md`。
4. 按 P0 → P7 阅读，或使用搜索直接定位面试、LangGraph、Verifier、业务价值等主题。

## 为什么不是直接导入 PDF 或 TEX

- Markdown 更适合 Obsidian 的全文搜索、双向链接和分段检索。
- PDF 适合人工连续阅读，但不适合作为唯一检索源。
- TEX 是 Overleaf 排版源文件，包含大量排版命令，不是学习知识的最佳格式。

## 文件层次

- 阶段笔记：项目主线和每个阶段的开发过程。
- 复盘笔记：问题、修复、验证和边界。
- 面试笔记：简历介绍、技术追问和业务追问。
- 复现笔记：本地运行、Windows 环境变量和 Overleaf 编译。
- `assets/`：仅保留电脑端网页截图。

## 当前状态

P0–P6 按当前公开证据记录为 PASSED；P7 为 IN_PROGRESS，真人目标运营用户试用尚未完成。
"""


def export(project_root: Path, output_root: Path) -> Path:
    handbook = project_root / "docs" / "project-handbook"
    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True)
    (output_root / "assets").mkdir()
    (output_root / "README-导入到Obsidian.md").write_text(build_readme(), encoding="utf-8")
    (output_root / "00-项目总览与阅读顺序.md").write_text(build_index(), encoding="utf-8")

    for source_name, note_name, label, status in PHASES:
        source = handbook / "chapters" / source_name
        body = latex_to_markdown(source.read_text(encoding="utf-8"))
        frontmatter = (
            "---\n"
            "type: project-note\n"
            "project: ECom Review Intelligence Agent\n"
            f"phase: {label}\n"
            f"status: {status}\n"
            f"source: docs/project-handbook/chapters/{source_name}\n"
            f"tags: [ecom-review-intelligence, {label.lower().replace(' ', '-')}]\n"
            "---\n\n"
        )
        (output_root / note_name).write_text(frontmatter + body, encoding="utf-8")

    asset = handbook / "assets" / "p7-workbench-desktop.png"
    shutil.copy2(asset, output_root / "assets" / asset.name)
    return output_root


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("output/obsidian/ECom-Review-Intelligence-KB"),
    )
    args = parser.parse_args()
    root = args.project_root.resolve()
    output_root = args.output_root if args.output_root.is_absolute() else root / args.output_root
    export(root, output_root)
    zip_path = output_root.with_suffix(".zip")
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output_root.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(output_root.parent).as_posix())
    print(f"OBSIDIAN_FOLDER={output_root}")
    print(f"OBSIDIAN_ZIP={zip_path}")
    print(f"MARKDOWN_FILES={len(list(output_root.glob('*.md')))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
