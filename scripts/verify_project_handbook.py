from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from pypdf import PdfReader

PUBLIC_PDF = Path("output/pdf/ecom-review-intelligence-project-handbook.pdf")
SOURCE_DIR = Path("docs/project-handbook")
BUILD_LOG = Path("tmp/pdfs/project-handbook/build/main.log")
SOURCE_TOPICS = (
    "项目概览与阅读指南",
    "为什么做",
    "P0",
    "P1",
    "P2",
    "P3",
    "P4",
    "P5",
    "P6",
    "P7",
    "运营工作台",
    "问题、修复与验证",
    "简历",
    "Overleaf",
)
PDF_MARKERS = (
    "P0",
    "P1",
    "P2",
    "P3",
    "P4",
    "P5",
    "P6",
    "P7",
    "Structured Output",
    "LangGraph",
    "Verifier",
    "DeepSeek",
    "Overleaf",
    "checkpoint",
    "SKU",
)
FORBIDDEN_TEXT = (
    re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]"),
    re.compile(r"(?i)sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"(?i)Bearer\s+[A-Za-z0-9._-]{12,}"),
    re.compile(r"(?<![A-Z0-9])B[A-Z0-9]{9}(?![A-Z0-9])"),
    re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])"),
)
LOG_FAILURE_PATTERNS = (
    re.compile(r"undefined references"),
    re.compile(r"citation\(s\) may have changed"),
    re.compile(r"there were undefined"),
    re.compile(r"latex error: file .* not found"),
    re.compile(r"! i can't find file"),
    re.compile(r"missing character"),
    re.compile(r"overfull \\hbox"),
    re.compile(r"overfull \\vbox"),
)


class HandbookVerificationError(RuntimeError):
    pass


def blocking_log_markers(log: str) -> list[str]:
    normalized = log.casefold()
    return [pattern.pattern for pattern in LOG_FAILURE_PATTERNS if pattern.search(normalized)]


def _assert_public_text_safe(text: str, *, label: str) -> None:
    for pattern in FORBIDDEN_TEXT:
        if pattern.search(text):
            raise HandbookVerificationError(f"{label} contains prohibited public data.")


def validate_tex_sources(source_dir: Path) -> dict[str, Any]:
    source_dir = Path(source_dir)
    tex_paths = sorted(source_dir.rglob("*.tex"))
    if not tex_paths:
        raise HandbookVerificationError("No LaTeX sources were found.")
    text = "\n".join(path.read_text(encoding="utf-8") for path in tex_paths)
    _assert_public_text_safe(text, label="LaTeX source")
    if "todo" in text.casefold() or "tbd" in text.casefold():
        raise HandbookVerificationError("LaTeX source contains a placeholder.")
    missing = [chapter for chapter in SOURCE_TOPICS if chapter not in text]
    if missing:
        raise HandbookVerificationError("LaTeX source is missing required chapters.")
    desktop_asset = "p7-workbench-desktop.png"
    if not (source_dir / "assets" / desktop_asset).is_file() or desktop_asset not in text:
        raise HandbookVerificationError("The desktop workbench screenshot is missing.")
    if "p7-workbench-mobile.png" in text:
        raise HandbookVerificationError("The handbook still references the mobile guide.")
    return {"tex_file_count": len(tex_paths), "required_chapters": len(SOURCE_TOPICS)}


def _image_count(reader: PdfReader) -> int:
    count = 0
    for page in reader.pages:
        resources = page.get("/Resources") or {}
        xobjects = resources.get("/XObject") or {}
        xobjects = xobjects.get_object() if hasattr(xobjects, "get_object") else xobjects
        for item in xobjects.values():
            obj = item.get_object()
            if obj.get("/Subtype") == "/Image":
                count += 1
    return count


def verify_pdf(pdf_path: Path, source_dir: Path, log_path: Path) -> dict[str, Any]:
    pdf_path = Path(pdf_path)
    if not pdf_path.is_file():
        raise HandbookVerificationError("The compiled handbook PDF is missing.")
    reader = PdfReader(pdf_path)
    if len(reader.pages) < 20:
        raise HandbookVerificationError("The handbook is unexpectedly short.")
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    _assert_public_text_safe(text, label="PDF")
    missing = [marker for marker in PDF_MARKERS if marker not in text]
    if missing:
        raise HandbookVerificationError("The PDF is missing required chapter text.")
    images = _image_count(reader)
    if images < 1:
        raise HandbookVerificationError("The PDF does not contain the desktop screenshot.")
    log_path = Path(log_path)
    if not log_path.is_file():
        raise HandbookVerificationError("The LaTeX compilation log is missing.")
    log = log_path.read_text(encoding="utf-8", errors="replace").casefold()
    failures = blocking_log_markers(log)
    if failures:
        raise HandbookVerificationError("The LaTeX log contains blocking warnings.")
    return {
        "page_count": len(reader.pages),
        "embedded_image_count": images,
        "pdf_size_bytes": pdf_path.stat().st_size,
    }


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify the public project handbook PDF.")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = args.project_root.resolve()
    try:
        source = validate_tex_sources(root / SOURCE_DIR)
        pdf = verify_pdf(root / PUBLIC_PDF, root / SOURCE_DIR, root / BUILD_LOG)
    except (HandbookVerificationError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {"schema_version": "project-handbook-verification-v1", "status": "FAILED"},
                sort_keys=True,
            )
        )
        print(str(exc), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "schema_version": "project-handbook-verification-v1",
                "status": "PASSED",
                "source": source,
                "pdf": pdf,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
