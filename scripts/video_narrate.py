#!/usr/bin/env python3
"""Generate one narration clip per video step with edge-tts.

Input is a UTF-8 text file with one spoken line per step (blank lines are
skipped). Each line becomes ``t<i>.wav`` in ``--out-dir`` with leading and
trailing silence trimmed, and the clip lengths in seconds are written to
``--durations`` (default ``<out-dir>/../narration.json``) so the animation can
size each step to its narration.

edge-tts sends the text to Microsoft's online speech service. Only pass text
that is meant to be published.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_VOICE = "zh-CN-YunxiNeural"
DEFAULT_RATE = "+4%"
TRIM = (
    "silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
    "silenceremove=start_periods=1:start_threshold=-45dB,areverse"
)


def read_lines(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def edge_tts_command() -> list[str]:
    """Resolve the edge-tts executable: TOM_WECHAT_EDGE_TTS, then PATH."""
    override = os.environ.get("TOM_WECHAT_EDGE_TTS")
    if override:
        return [override]
    found = shutil.which("edge-tts")
    if found:
        return [found]
    raise SystemExit(
        "edge-tts not found. Install it pinned in a venv, e.g. "
        "`python3 -m venv .venv && .venv/bin/pip install edge-tts==7.3.1`, "
        "then set TOM_WECHAT_EDGE_TTS=.venv/bin/edge-tts"
    )


def duration_of(path: Path) -> float:
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]
    )
    return float(out.decode().strip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("lines", type=Path, help="one spoken line per step")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--voice", default=DEFAULT_VOICE)
    parser.add_argument("--rate", default=DEFAULT_RATE, help="edge-tts rate, e.g. +4%% or -5%%")
    parser.add_argument("--durations", type=Path, help="where to write the clip lengths (JSON list)")
    args = parser.parse_args()

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise SystemExit("ffmpeg/ffprobe are required (brew install ffmpeg)")
    lines = read_lines(args.lines)
    if not lines:
        raise SystemExit(f"no narration lines in {args.lines}")
    tts = edge_tts_command()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    durations = []
    for i, line in enumerate(lines):
        raw = args.out_dir / f"v{i}.mp3"
        clip = args.out_dir / f"t{i}.wav"
        subprocess.run(
            tts + ["--voice", args.voice, f"--rate={args.rate}", "--text", line, "--write-media", str(raw)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-i", str(raw), "-af", TRIM, "-ar", "44100", str(clip)],
            check=True,
        )
        durations.append(round(duration_of(clip), 3))
        print(f"t{i}.wav {durations[-1]:.2f}s  {line[:24]}", file=sys.stderr)

    target = args.durations or args.out_dir.parent / "narration.json"
    target.write_text(json.dumps(durations), encoding="utf-8")
    print(f"{len(durations)} clips, {sum(durations):.1f}s of speech -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
