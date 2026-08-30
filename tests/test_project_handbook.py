import re
import struct
from pathlib import Path

ROOT = Path(__file__).parents[1]
HANDBOOK = ROOT / "docs" / "project-handbook"
INTEGRATED_CHAPTERS = (
    "00-reading-guide",
    "01-origin-and-value",
    "02-workbench-tour",
    "02-system-roadmap",
    "03-phase-p0",
    "04-phase-p1",
    "05-phase-p2",
    "06-phase-p3",
    "07-phase-p4",
    "08-phase-p5",
    "09-phase-p6",
    "10-phase-p7",
    "11-lessons-and-failures",
    "12-resume-and-interview",
    "13-reproduction",
)

PHASE_CHAPTERS = tuple(f"{index + 3:02}-phase-p{index}" for index in range(8))
PHASE_TEMPLATE_MARKERS = (
    "承上启下",
    "开发过程",
    "本阶段学到的知识",
    "问题、修复与验证",
    "阶段产出与验收边界",
    "怎样进入下一阶段",
    "面试怎么讲",
)


def _tex_text() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(HANDBOOK.rglob("*.tex"))
    )


def test_overleaf_project_has_complete_modular_file_set():
    required = {
        HANDBOOK / "main.tex",
        HANDBOOK / "preamble.tex",
        HANDBOOK / "README.md",
        HANDBOOK / "latexmkrc",
        *(
            HANDBOOK / "chapters" / f"{chapter}.tex"
            for chapter in INTEGRATED_CHAPTERS
        ),
    }

    assert all(path.is_file() for path in required)


def test_main_uses_one_integrated_phase_first_reading_order():
    main = (HANDBOOK / "main.tex").read_text(encoding="utf-8")
    preamble = (HANDBOOK / "preamble.tex").read_text(encoding="utf-8")
    offsets = [
        main.index(f"chapters/{chapter}") for chapter in INTEGRATED_CHAPTERS
    ]

    assert offsets == sorted(offsets)
    assert "chapters/03-agent-learning" not in main
    assert "chapters/04-architecture" not in main
    assert "chapters/05-workbench-guide" not in main
    assert "ctexbook" in main
    assert "fontset=fandol" in main
    assert "minted" not in preamble.casefold()
    assert "listings" in preamble
    assert "hyperref" in preamble
    assert "graphicx" in preamble


def test_each_phase_integrates_development_learning_repair_and_handoff():
    for chapter in PHASE_CHAPTERS:
        text = (HANDBOOK / "chapters" / f"{chapter}.tex").read_text(
            encoding="utf-8"
        )
        assert all(marker in text for marker in PHASE_TEMPLATE_MARKERS), chapter


def test_p6_embeds_agent_learning_in_development_order():
    text = (HANDBOOK / "chapters" / "09-phase-p6.tex").read_text(
        encoding="utf-8"
    )
    markers = (
        "Structured Output",
        "Tools 与 Authority",
        "Verifier",
        "LangGraph",
        "interrupt",
        "resume",
        "checkpoint",
        "Trace",
        "Offline Evaluation",
        "DeepSeek",
        "完成审计",
    )

    assert all(marker in text for marker in markers)


def test_p7_is_desktop_only_and_keeps_user_trial_open():
    text = (HANDBOOK / "chapters" / "10-phase-p7.tex").read_text(
        encoding="utf-8"
    )

    assert "p7-workbench-desktop.png" in text
    assert "p7-workbench-mobile.png" not in text
    assert "手机端" not in text and "移动端" not in text
    assert r"IN\_PROGRESS" in text
    assert "目标运营用户" in text and "结构化试用" in text


def test_workbench_tour_precedes_phase_roadmap_and_explains_four_desktop_screens():
    main = (HANDBOOK / "main.tex").read_text(encoding="utf-8")
    tour = (HANDBOOK / "chapters" / "02-workbench-tour.tex").read_text(
        encoding="utf-8"
    )

    assert main.index("chapters/02-workbench-tour") < main.index(
        "chapters/02-system-roadmap"
    )
    assert all(
        f"assets/{filename}" in tour
        for filename in (
            "p7-tour-01-initial.png",
            "p7-tour-02-diagnosis.png",
            "p7-tour-03-comparison.png",
            "p7-tour-04-clarification.png",
        )
    )
    assert "synthetic" in tour.casefold()
    assert "不调用 DeepSeek" in tour
    assert "diagnosis" in tour and "comparison" in tour and "triage" in tour
    assert "澄清" in tour and "checkpoint" in tour
    assert "手机端" not in tour and "移动端" not in tour
    for marker in ("左侧", "中间", "运行按钮", "右侧", "这一屏在项目中要展现什么"):
        assert marker in tour


def test_handbook_states_verified_phase_boundary_without_overclaiming_p7():
    text = _tex_text()

    assert "P0--P6" in text and "PASSED" in text
    assert "P7" in text and r"IN\_PROGRESS" in text
    assert not re.search(r"P7.{0,20}(?:PASSED|已通过|已经完成)", text)
    assert "真人" in text and "试用" in text


def test_handbook_covers_business_technical_learning_and_interview_topics():
    text = _tex_text()
    required_topics = (
        "业务价值",
        "技术价值",
        "广州",
        "运营中心",
        "B 端",
        "C 端",
        "LangGraph",
        "Structured Output",
        "Verifier",
        "checkpoint",
        "DeepSeek",
        "工作台",
        "简历",
        "面试",
        "Overleaf",
    )

    assert all(topic in text for topic in required_topics)


def test_beginner_path_explains_core_engineering_words_with_plain_language():
    guide = (HANDBOOK / "chapters" / "00-reading-guide.tex").read_text(
        encoding="utf-8"
    )
    p1 = (HANDBOOK / "chapters" / "04-phase-p1.tex").read_text(
        encoding="utf-8"
    )
    p6 = (HANDBOOK / "chapters" / "09-phase-p6.tex").read_text(
        encoding="utf-8"
    )
    readme = (HANDBOOK / "README.md").read_text(encoding="utf-8")

    assert "文件指纹" in guide or "文件指纹" in p1
    assert "版本记录" in guide or "盖章" in p1
    assert "输入清单" in guide or "输入清单" in p1
    assert "固定栏位的工单" in p6
    assert "出库前的复核员" in p6
    assert "第一次学习时先读“第一次使用：先看懂工作台”" in readme


def test_desktop_workbench_screenshot_is_portable_valid_and_referenced():
    chapter = (HANDBOOK / "chapters" / "10-phase-p7.tex").read_text(
        encoding="utf-8"
    )
    filename = "p7-workbench-desktop.png"
    path = HANDBOOK / "assets" / filename
    payload = path.read_bytes()

    assert payload.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", payload[16:24])
    assert width > 0 and height > 0
    assert f"assets/{filename}" in chapter
    assert not (HANDBOOK / "assets" / "p7-workbench-mobile.png").exists()


def test_public_tex_sources_have_no_placeholders_secrets_or_local_identifiers():
    text = _tex_text()
    casefolded = text.casefold()

    assert "todo" not in casefolded
    assert "tbd" not in casefolded
    assert not re.search(r"(?<![A-Za-z])[A-Za-z]:[\\/]", text)
    assert not re.search(r"(?i)sk-[A-Za-z0-9_-]{12,}", text)
    assert not re.search(r"(?i)Bearer\s+[A-Za-z0-9._-]{12,}", text)
    assert not re.search(r"(?<![A-Z0-9])B[A-Z0-9]{9}(?![A-Z0-9])", text)
    assert not re.search(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", text)


def test_readme_explains_overleaf_and_local_build_paths():
    readme = (HANDBOOK / "README.md").read_text(encoding="utf-8")

    assert "XeLaTeX" in readme
    assert "main.tex" in readme
    assert "Overleaf" in readme
    assert "scripts/build_project_handbook.ps1" in readme
    assert "output/pdf/ecom-review-intelligence-project-handbook.pdf" in readme


def test_build_and_verification_entrypoints_use_fixed_public_output():
    build_path = ROOT / "scripts" / "build_project_handbook.ps1"
    verify_path = ROOT / "scripts" / "verify_project_handbook.py"

    assert build_path.is_file() and verify_path.is_file()
    build = build_path.read_text(encoding="utf-8")
    verify = verify_path.read_text(encoding="utf-8")
    expected = "output/pdf/ecom-review-intelligence-project-handbook.pdf"
    assert expected in build.replace("\\", "/")
    assert expected in verify.replace("\\", "/")
    assert "tectonic" in build.casefold()
    assert "validate_tex_sources" in verify
    assert "verify_pdf" in verify


def test_pdf_verifier_dependency_is_declared_for_reproducible_local_setup():
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert '"pypdf>=6.10,<7"' in project


def test_log_scanner_distinguishes_package_info_from_missing_file_error():
    import scripts.verify_project_handbook as verify

    assert verify.blocking_log_markers("Package pdftexcmds Info: pdfdraftmode not found.") == []
    assert verify.blocking_log_markers("LaTeX Error: File `missing.sty' not found.")
