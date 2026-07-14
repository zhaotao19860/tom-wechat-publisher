#!/usr/bin/env python3
"""
ocr_validate.py — Compare OCR-recognized text on a rendered image against
the source-of-truth ImageSpec. Returns 0 (clean) or 1 (warnings written).

Strategy:
  - Run scripts/ocr_check.swift via /usr/bin/swift to extract recognized
    strings (macOS Vision; no third-party dep needed).
  - The spec lists every text fragment that should appear: title, subtitle,
    item labels, item subs, step numbers.
  - Anything OCR-recognized that does not approximately match a spec
    fragment is flagged as a stray-text warning.
  - Missing core fragments (title) are flagged as missing-text warnings.

This is best-effort. Vision OCR can misread small text; we tolerate that
for the "extra" check by requiring at least a 4-char unmatched run before
flagging.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SWIFT_OCR = SCRIPT_DIR / "ocr_check.swift"


def run_ocr(image: Path) -> list[str] | None:
    swift = shutil.which("swift") or "/usr/bin/swift"
    if not Path(swift).exists():
        return None
    if not SWIFT_OCR.exists():
        return None
    try:
        res = subprocess.run(
            [swift, str(SWIFT_OCR), str(image)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception as e:
        print(f"[ocr] failed to run: {e}", file=sys.stderr)
        return None
    if res.returncode != 0:
        print(f"[ocr] swift exit {res.returncode}: {res.stderr[:300]}", file=sys.stderr)
        return None
    return [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]


def _norm(text: str) -> str:
    text = text.replace("×", "x").replace("Ｘ", "x").replace("ｘ", "x")
    return re.sub(r"[\s·•，。！？!?,.\-—_·．、；;：:（）()]+", "", text).lower()


def _spec_strings(spec: dict) -> list[str]:
    out = [spec.get("title_zh", ""), spec.get("subtitle", ""), spec.get("notes", "")]
    for it in spec.get("items") or []:
        out.append(it.get("label", ""))
        out.append(it.get("sub", ""))
        if it.get("step"):
            out.append(str(it.get("step")))
    for line in spec.get("cover_text") or []:
        out.append(str(line))
    for key in ("allowed_extra_text", "expected_extra_labels"):
        extra = spec.get(key) or []
        if isinstance(extra, str):
            out.append(extra)
        else:
            out.extend(str(s) for s in extra if s)
    # Common chrome strings rendered by deterministic templates.
    # Cover CTAs must come from the current article spec, not a reusable slogan.
    out.extend([
        "覆盖范围", "不覆盖 / 边界", "不覆盖", "边界",
        "手绘解读", "图解要点", "流程图解", "视觉解读",
    ])
    return [s for s in out if s]


def validate(image: Path, spec: dict) -> tuple[list[str], list[str]]:
    """Return (warnings, ocr_lines)."""
    warnings: list[str] = []
    lines = run_ocr(image)
    if lines is None:
        warnings.append("OCR unavailable; skipping post-render text validation")
        return warnings, []
    spec_norm = [_norm(s) for s in _spec_strings(spec) if _norm(s)]

    # Missing core text
    title_norm = _norm(spec.get("title_zh", ""))
    if title_norm:
        joined = "".join(_norm(l) for l in lines)
        # Vision sometimes splits the title across boxes; check char overlap.
        if title_norm not in joined and sum(1 for ch in title_norm if ch in joined) < max(3, len(title_norm) - 2):
            warnings.append(f"missing title in OCR: expected '{spec.get('title_zh','')}'")

    # Stray text
    for raw in lines:
        n = _norm(raw)
        if not n:
            continue
        if any((n in s) or (s in n) for s in spec_norm if len(s) >= 2):
            continue
        # Tolerate short fragments (likely OCR splits) and pure digits.
        if len(n) < 4 or n.isdigit():
            continue
        warnings.append(f"stray text in image: {raw!r}")

    return warnings, lines


def _print_result(label: str, warnings: list[str], lines: list[str]) -> None:
    if warnings:
        print(f"[ocr-validate] {label}: {len(warnings)} warnings:")
        for w in warnings:
            print(f"  - {w}")
        if lines:
            print("[ocr-validate] recognized:")
            for line in lines:
                print(f"  | {line}")
    else:
        print(f"[ocr-validate] {label}: clean")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image")
    ap.add_argument("--image-dir", help="directory containing images named from image_specs.json")
    ap.add_argument("--spec", required=True, help="image_specs.json")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--index", type=int)
    g.add_argument("--all", action="store_true")
    args = ap.parse_args(argv[1:])
    specs = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    if args.all:
        image_dir = Path(args.image_dir or Path(args.spec).parent / "images")
        total_warnings = 0
        missing = 0
        for idx, spec in enumerate(specs):
            image = image_dir / f"{spec.get('filename')}.png"
            label = f"#{idx} {image.name}"
            if not image.exists():
                print(f"[ocr-validate] {label}: missing image at {image}")
                missing += 1
                continue
            warnings, lines = validate(image, spec)
            total_warnings += len(warnings)
            _print_result(label, warnings, lines)
        return 1 if total_warnings or missing else 0
    if not args.image:
        ap.error("--image is required with --index")
    spec = specs[args.index]
    warnings, lines = validate(Path(args.image), spec)
    _print_result(Path(args.image).name, warnings, lines)
    return 1 if warnings else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
