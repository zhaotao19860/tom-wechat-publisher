#!/usr/bin/env python3
"""Render a Remotion explainer and mux it with narration into an MP4.

The Remotion project imports ``schedule.json`` (written by video_schedule.py)
to size every step, so picture and voice share one clock:

    {
      "TOTAL": 180.4,                     # seconds
      "NARR_LEAD": 0.15,                  # narration starts this long after each arrival
      "SCENES": [{"start": 0, "end": 89.1}],
      "SCHEDULE": [{"arrive": 1.0, ...}], # one entry per step, aligned with t<i>.wav
      "sfx": [{"kind": "click", "t": 1.0, "gain": 0.35}]   # optional
    }

The composition is rendered silent with ``npx remotion render``. Narration clip
``<audio-dir>/t<i>.wav`` (from video_narrate.py) is placed at
``SCHEDULE[i].arrive + NARR_LEAD``, effects come from a small built-in set
(click, press, ok, bad), and the mix is loudness-normalised before muxing.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

SR = 44100
SFX_KINDS = ("click", "press", "ok", "bad")


def audio_events(schedule: dict, audio_dir: Path, sfx_dir: Path) -> list[tuple[Path, float, float]]:
    """(file, start seconds, gain) for every narration clip and sound effect."""
    lead = float(schedule.get("NARR_LEAD", 0.15))
    events = []
    for i, step in enumerate(schedule["SCHEDULE"]):
        clip = audio_dir / f"t{i}.wav"
        if clip.exists():
            events.append((clip, float(step["arrive"]) + lead, 1.0))
    for fx in schedule.get("sfx", []):
        if fx["kind"] not in SFX_KINDS:
            raise SystemExit(f"unknown sfx kind {fx['kind']!r}; use one of {SFX_KINDS}")
        events.append((sfx_dir / f"{fx['kind']}.wav", float(fx["t"]), float(fx.get("gain", 0.5))))
    return events


def mix_filter(events: list[tuple[Path, float, float]], total: float) -> str:
    parts = [
        f"[{k}:a]aresample={SR},aformat=channel_layouts=mono,volume={gain},adelay={int(round(t * 1000))}:all=1[a{k}]"
        for k, (_, t, gain) in enumerate(events)
    ]
    inputs = "".join(f"[a{k}]" for k in range(len(events)))
    parts.append(
        f"{inputs}amix=inputs={len(events)}:normalize=0,apad,atrim=0:{total:.3f},"
        "loudnorm=I=-16:TP=-1.5:LRA=11,aformat=channel_layouts=stereo[out]"
    )
    return ";".join(parts)


def write_sfx(directory: Path) -> None:
    """Synthesize the built-in effects: a soft tick, a low thunk, a two-note chime, a buzz."""
    random.seed(7)
    shapes = {
        "click": (0.12, lambda t: 0.30 * math.sin(2 * math.pi * 2200 * t) * math.exp(-60 * t)),
        "press": (0.35, lambda t: 0.55 * math.sin(2 * math.pi * (110 * t + 200 * (1 - math.exp(-30 * t)) / 30)) * math.exp(-18 * t)
                  + 0.12 * (random.random() - 0.5) * math.exp(-40 * t)),
        "ok": (0.9, lambda t: 0.26 * (math.sin(2 * math.pi * 880 * t) * math.exp(-5 * t)
                                       + (0.7 * math.sin(2 * math.pi * 1320 * (t - 0.09)) * math.exp(-5 * (t - 0.09)) if t > 0.09 else 0))),
        "bad": (0.6, lambda t: 0.22 * ((1 if math.sin(2 * math.pi * 150 * t) > 0 else -1) * 0.5 + math.sin(2 * math.pi * 157 * t))
                * (1 - math.exp(-80 * t)) * math.exp(-4 * t) * (t < 0.5)),
    }
    for name, (dur, f) in shapes.items():
        with wave.open(str(directory / f"{name}.wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(b"".join(struct.pack("<h", int(max(-1.0, min(1.0, f(i / SR))) * 32000)) for i in range(int(SR * dur))))


def run(cmd: list, **kw) -> None:
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project", type=Path, help="Remotion project directory")
    parser.add_argument("--composition", required=True, help="Remotion composition id")
    parser.add_argument("--schedule", type=Path, required=True)
    parser.add_argument("--audio-dir", type=Path, help="t<i>.wav narration clips; omit for a silent video")
    parser.add_argument("--out", type=Path, required=True, help="output .mp4")
    parser.add_argument("--gif", type=Path, help="also write a silent preview GIF for the article body")
    parser.add_argument("--gif-speed", type=float, default=1.5)
    parser.add_argument("--gif-fps", type=int, default=8, help="GIF frame rate; WeChat rejects GIFs over 300 frames")
    parser.add_argument("--gif-width", type=int, default=720)
    parser.add_argument("--gif-crop-height", type=int, help="crop rendered frames to this many px from the top before the GIF")
    parser.add_argument("--width", type=int, default=1280, help="MP4 width (height follows, even)")
    parser.add_argument("--keep-silent", type=Path, help="keep the silent Remotion render at this path")
    args = parser.parse_args()

    for tool in ("npx", "ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise SystemExit(f"{tool} is required")
    if not (args.project / "package.json").exists():
        raise SystemExit(f"no Remotion project at {args.project}")
    schedule = json.loads(args.schedule.read_text(encoding="utf-8"))
    total = float(schedule["TOTAL"])
    out = args.out.resolve()

    with tempfile.TemporaryDirectory(prefix="wechat-video-") as tmp:
        tmp = Path(tmp)
        silent = (args.keep_silent or tmp / "silent.mp4").resolve()
        print(f"rendering {args.composition} ({total:.1f}s) with Remotion", file=sys.stderr)
        run(["npx", "remotion", "render", args.composition, silent, "--muted", "--log=error"], cwd=args.project)

        encode = ["ffmpeg", "-loglevel", "error", "-y", "-i", silent]
        if args.audio_dir:
            sfx = tmp / "sfx"
            sfx.mkdir()
            write_sfx(sfx)
            events = audio_events(schedule, args.audio_dir, sfx)
            track = tmp / "track.wav"
            mix = ["ffmpeg", "-loglevel", "error", "-y"]
            for f, _, _ in events:
                mix += ["-i", f]
            run(mix + ["-filter_complex", mix_filter(events, total), "-map", "[out]", "-ar", SR, track])
            encode += ["-i", track, "-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "128k", "-shortest"]
        encode += ["-vf", f"scale={args.width}:-2:flags=lanczos,format=yuv420p", "-c:v", "libx264", "-crf", 20, "-preset", "slow", "-movflags", "+faststart", out]
        run(encode)

        if args.gif:
            # WeChat rejects GIFs over 300 frames; speed the GIF up just enough to fit
            speed = max(args.gif_speed, total * args.gif_fps / 298)
            if speed > args.gif_speed:
                print(f"GIF speed raised to {speed:.2f}x to stay under 300 frames", file=sys.stderr)
            crop = f"crop=iw:{args.gif_crop_height}:0:0," if args.gif_crop_height else ""
            vf = (f"setpts=PTS/{speed:.4f},fps={args.gif_fps},{crop}scale={args.gif_width}:-1:flags=lanczos,split[a][b];"
                  "[a]palettegen=max_colors=64:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle")
            run(["ffmpeg", "-loglevel", "error", "-y", "-i", silent, "-vf", vf, args.gif.resolve()])

    probe = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,duration", "-of", "compact", str(out)]).decode().strip()
    print(probe)
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)" + (f" and {args.gif}" if args.gif else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
