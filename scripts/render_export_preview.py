"""Build the local HTML preview for one export directory.

The renderer lives in ``src/ecom_review_intelligence/export_preview.py``; this is
the thin command-line wrapper, matching the other scripts here.

Usage:
    .venv/Scripts/python.exe scripts/render_export_preview.py tmp/exports/<name>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ecom_review_intelligence.export_preview import PreviewError, latest_export, render


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, nargs="?", default=None)
    args = parser.parse_args(argv)
    try:
        directory = args.directory or latest_export(ROOT)
        print(render(directory.resolve()))
    except PreviewError as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
