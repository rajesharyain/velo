"""
Command-line entry: single theme, optional batch file, logging.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from travel_instagram import pipeline


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate Instagram carousels and reels (Groq + Pexels + FFmpeg).",
    )
    parser.add_argument(
        "--theme",
        "-t",
        help="Travel theme, e.g. 'hidden beaches in Europe'",
    )
    parser.add_argument(
        "--batch",
        "-b",
        type=Path,
        help="Text file with one theme per line (empty lines and # comments skipped).",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Debug logging including ffmpeg command lines.",
    )
    parser.add_argument(
        "--music",
        metavar="TRACK",
        help=(
            "Reel audio: relative path under music/ (see GET /api/music-tracks), "
            "'__none__' for silence, or omit for .env REEL_MUSIC_PATH / first library file."
        ),
    )
    args = parser.parse_args(argv)

    _configure_logging(args.verbose)

    if args.batch:
        lines = args.batch.read_text(encoding="utf-8").splitlines()
        out = pipeline.run_batch(lines)
        print(json.dumps(out, indent=2))
        errs = [x for x in out if x.get("error")]
        return 1 if errs else 0

    if args.theme:
        summary = pipeline.run_pipeline(args.theme, args.music)
        print(json.dumps(summary, indent=2))
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
