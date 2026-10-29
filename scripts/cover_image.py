#!/usr/bin/env python3
"""Check or crop a WeChat headline cover to 2.35:1 (1800x766) without third-party packages.

Uses macOS `sips`, so it works with the system python where Pillow is not installed and
`pip install` is blocked by PEP 668.

  cover_image.py check images/cover-ai-poem.png
  cover_image.py crop images/cover-raw.png --out images/cover-ai-poem.png [--anchor top|center|bottom|0.3]

`check` exits 0 when the image is already 1800x766 (or 2.35:1 within --tolerance and at
least --min-width wide), 1 otherwise. `crop` keeps the full width, cuts the height to 2.35:1
at the chosen vertical anchor, then resamples to 1800x766.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TARGET_W, TARGET_H = 1800, 766
RATIO = TARGET_W / TARGET_H
ANCHORS = {"top": 0.0, "center": 0.5, "bottom": 1.0}
FORMATS = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg"}


def _sips() -> str:
    path = shutil.which("sips")
    if not path:
        raise SystemExit("ERROR: sips not found; this helper requires macOS")
    return path


def size(path: Path) -> tuple[int, int]:
    out = subprocess.run([_sips(), "-g", "pixelWidth", "-g", "pixelHeight", str(path)],
                         capture_output=True, text=True)
    w = re.search(r"pixelWidth:\s*(\d+)", out.stdout)
    h = re.search(r"pixelHeight:\s*(\d+)", out.stdout)
    if out.returncode != 0 or not w or not h:
        raise SystemExit(f"ERROR: cannot read image size: {path}")
    return int(w.group(1)), int(h.group(1))


def check(path: Path, tolerance: float, min_width: int) -> dict:
    w, h = size(path)
    ratio = w / h
    exact = (w, h) == (TARGET_W, TARGET_H)
    ok = exact or (abs(ratio - RATIO) / RATIO <= tolerance and w >= min_width)
    return {"path": str(path), "width": w, "height": h, "ratio": round(ratio, 4),
            "target": f"{TARGET_W}x{TARGET_H}", "ok": ok, "exact": exact}


def _anchor(value: str) -> float:
    if value in ANCHORS:
        return ANCHORS[value]
    try:
        f = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("anchor must be top, center, bottom or a number in [0, 1]")
    if not 0.0 <= f <= 1.0:
        raise argparse.ArgumentTypeError("anchor must be in [0, 1]")
    return f


def crop(src: Path, out: Path, anchor: float) -> dict:
    fmt = FORMATS.get(out.suffix.lower())
    if not fmt:
        raise SystemExit("ERROR: --out must end in .png, .jpg or .jpeg")
    if src.resolve() == out.resolve():
        raise SystemExit("ERROR: --out must differ from the source; keep the raw generation")
    w, h = size(src)
    if w / h < RATIO:
        crop_w, crop_h = w, round(w / RATIO)   # too tall: cut height
    else:
        crop_w, crop_h = round(h * RATIO), h   # too wide: cut width, centered
    if crop_w < TARGET_W:
        print(f"WARNING: crop is only {crop_w}px wide; upscaling to {TARGET_W} loses detail", file=sys.stderr)
    off_y = round((h - crop_h) * anchor)
    off_x = round((w - crop_w) / 2)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp) / ("stage.png" if fmt == "png" else "stage.jpg")
        run = lambda *a: subprocess.run([_sips(), *a], capture_output=True, text=True)
        steps = [
            ("-s", "format", fmt, str(src), "--out", str(staged)),
            ("-c", str(crop_h), str(crop_w), "--cropOffset", str(off_y), str(off_x), str(staged), "--out", str(staged)),
            ("-z", str(TARGET_H), str(TARGET_W), str(staged), "--out", str(staged)),
        ]
        for step in steps:
            r = run(*step)
            if r.returncode != 0:
                raise SystemExit(f"ERROR: sips failed: {r.stderr.strip() or r.stdout.strip()}")
        shutil.move(str(staged), out)
    result = check(out, 0.0, TARGET_W)
    result.update(source=str(src), source_size=f"{w}x{h}", crop=f"{crop_w}x{crop_h}+{off_x}+{off_y}")
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="verify cover size / ratio")
    c.add_argument("image", type=Path)
    c.add_argument("--tolerance", type=float, default=0.01, help="allowed relative ratio error (default 1%%)")
    c.add_argument("--min-width", type=int, default=TARGET_W)
    k = sub.add_parser("crop", help="crop to 2.35:1 and resize to 1800x766")
    k.add_argument("image", type=Path)
    k.add_argument("--out", type=Path, required=True)
    k.add_argument("--anchor", type=_anchor, default=0.5,
                   help="vertical crop position: top, center (default), bottom, or 0..1")
    for p in (c, k):
        p.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not args.image.is_file():
        print(f"ERROR: image not found: {args.image}", file=sys.stderr)
        return 2
    res = check(args.image, args.tolerance, args.min_width) if args.cmd == "check" else crop(args.image, args.out, args.anchor)
    if args.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        verdict = "OK" if res["ok"] else "NOT A COVER SIZE"
        extra = f" (from {res['source_size']}, crop {res['crop']})" if args.cmd == "crop" else ""
        print(f"{verdict}: {res['path']} {res['width']}x{res['height']} ratio {res['ratio']} target {res['target']}{extra}")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
