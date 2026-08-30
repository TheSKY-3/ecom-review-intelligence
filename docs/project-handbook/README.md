# ECom Review Intelligence Agent 项目手册

本目录是独立、可上传到 Overleaf 的 XeLaTeX 工程。它按 P0-P7 的真实开发顺序讲述项目，并把 Agent、LangGraph、Verifier 等知识放进实际出现的阶段，而不是拆成平行课程。P7 使用手册只展示电脑网页端。

## 阅读主线

手册先说明广州电商运营背景和 B 端价值，再用路线图串起八个阶段。每个阶段都解释承上启下、开发过程、本阶段知识、问题修复、验收边界、下一阶段和面试表达；最后统一整理跨阶段经验、简历面试和复现方式。

第一次学习时先读“第一次使用：先看懂工作台”这一章：先看页面怎样接收一个运营任务、怎样显示结果、怎样在不确定时请求澄清，再沿着 P0-P7 回看每个页面能力是怎样一步步做出来的。这样不需要先背 Agent 术语，也能理解整个项目的业务链路。遇到“哈希、冻结、manifest、Gate、lineage”等词，先把它们理解成“文件指纹、版本盖章、输入清单、放行检查和来源记录”；完整字段、命令和失败门放在后面的工程附录，不要求第一次阅读就背下来。

本目录生成的是一份完整 PDF；它同时包含入门主线和附录。若只想学习，读到 P7 后可以暂时跳过“全项目问题、复现和 Overleaf”；若要准备技术面试或复现，再回来看这些章节。

## Overleaf 编译

1. 将本目录中的 `main.tex`、`preamble.tex`、`latexmkrc`、`chapters/` 和 `assets/` 一起压缩为 ZIP。
2. 在 Overleaf 选择 **New Project > Upload Project** 并上传 ZIP。
3. 打开项目 Menu，将 Compiler 设为 `XeLaTeX`。
4. 确认 Main document 是 `main.tex`，然后点击 Recompile。
5. 编译成功后使用 Overleaf 的 Download PDF 下载。

工程使用 `ctexbook`、Fandol 字体和相对图片路径，不依赖本机私有字体。不要只上传 `main.tex`；章节和电脑网页端截图缺失会导致编译失败。

## 本地构建

在仓库根目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_project_handbook.ps1
.\.venv\Scripts\python.exe scripts\verify_project_handbook.py --project-root .
```

构建脚本使用 PATH 或 `.tools/tectonic/` 中的 Tectonic。中间文件写入 `tmp/pdfs/project-handbook/`，最终文件固定为：

```text
output/pdf/ecom-review-intelligence-project-handbook.pdf
```

## 导出 Obsidian 知识库

如果要把项目交给 Obsidian 或其他知识库读取，不建议直接把 `.tex` 作为主要检索内容，也不建议只上传 63 页 PDF。运行下面的命令，会把各章节转换为带 YAML frontmatter 的 Markdown 笔记，并生成一个可直接解压到 Vault 的 ZIP：

```powershell
.\.venv\Scripts\python.exe scripts\export_handbook_to_obsidian.py --project-root .
```

输出位于 `output/obsidian/ECom-Review-Intelligence-KB/` 和同名 `.zip`。Markdown 是知识库主入口；PDF 继续用于连续阅读，LaTeX 工程继续用于 Overleaf 编辑。

## 状态和公开边界

- P0-P6 按当前公开证据记录为通过。
- P7 保持进行中：工程自动化和受控本地接线已经验证，目标运营用户结构化试用尚未完成。
- 工程不得加入原始评论、真实商品或证据标识、密钥、本机绝对路径、私有简历和岗位规划材料。
- Overleaf 编译和默认 synthetic 工作台都不需要 DeepSeek API Key；只有显式 live gate 才需要进程环境中的 Key。
