"""Assemble the README's first-screen GIF from captured workbench screenshots.

The GIF is a slideshow of real UI states (no simulated content): catalog with
the full store, a finished diagnosis, the AI reading, and the chat trace. It
is rebuilt whenever the screenshots are re-captured.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from PIL import Image

DEFAULT_FRAMES = (
    "p7-workbench-overview.png",
    "p7-workbench-desktop.png",
    "p7-workbench-insight.png",
    "p7-workbench-chat-trace.png",
)
FRAME_MS = 2400
TARGET_WIDTH = 1200


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        allow_abbrev=False,
        description="Build the README slideshow GIF from captured screenshots.",
    )
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--frames",
        nargs="+",
        default=list(DEFAULT_FRAMES),
        help="Screenshot filenames in slideshow order.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    frames: list[Image.Image] = []
    for name in args.frames:
        path = Path(args.input_dir) / name
        image = Image.open(path).convert("RGB")
        scale = TARGET_WIDTH / image.width
        image = image.resize(
            (TARGET_WIDTH, round(image.height * scale)), Image.LANCZOS
        )
        frames.append(image)
    frames[0].save(
        Path(args.output),
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=FRAME_MS,
        loop=0,
        optimize=True,
    )
    print(f"written: {args.output} ({Path(args.output).stat().st_size} bytes, {len(frames)} frames)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
