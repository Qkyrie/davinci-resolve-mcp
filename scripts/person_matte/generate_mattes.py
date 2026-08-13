#!/usr/bin/env python3
"""Generate per-frame grayscale person mattes for a Fusion Loader sequence.

Input: a directory of numbered source frames (frame_0000.png, frame_0001.png, ...),
       exported frame-accurate from Resolve (see build_export_lua.py in
       this directory — do NOT use ffmpeg for this; see the guide for why).
Output: matching numbered grayscale mattes (white = subject, black = background)
        in the same naming scheme, ready to point a Fusion Loader's Clip input
        at frame_0000.png of the output directory.

Usage:
    .venv/bin/python generate_mattes.py <frames_dir> <mattes_dir> [--model u2net_human_seg]

Run scripts/person_matte/setup_env.sh first to create the venv with rembg.
"""
import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("frames_dir", type=Path)
    parser.add_argument("mattes_dir", type=Path)
    parser.add_argument(
        "--model",
        default="u2net_human_seg",
        help="rembg model name (default: u2net_human_seg, tuned for people). "
             "Use 'u2net' for general subjects/objects.",
    )
    parser.add_argument(
        "--pattern",
        default="frame_*.png",
        help="glob pattern for input frames within frames_dir (default: frame_*.png)",
    )
    args = parser.parse_args()

    try:
        from rembg import remove, new_session
    except ImportError:
        print(
            "rembg is not installed in this interpreter. Run setup_env.sh first, "
            "then invoke this script with .venv/bin/python.",
            file=sys.stderr,
        )
        return 1

    frame_files = sorted(args.frames_dir.glob(args.pattern))
    if not frame_files:
        print(f"No frames matched {args.pattern!r} in {args.frames_dir}", file=sys.stderr)
        return 1

    args.mattes_dir.mkdir(parents=True, exist_ok=True)
    session = new_session(args.model)

    print(f"Processing {len(frame_files)} frames with model {args.model!r}", file=sys.stderr)
    for i, src in enumerate(frame_files):
        matte_bytes = remove(src.read_bytes(), session=session, only_mask=True)
        (args.mattes_dir / src.name).write_bytes(matte_bytes)
        if i % 20 == 0:
            print(f"  {i}/{len(frame_files)}", file=sys.stderr)

    print(f"Done: {len(frame_files)} mattes written to {args.mattes_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
