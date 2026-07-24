#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

SUPPORTED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif"}
CONVERTIBLE_IMAGE_EXTS = {".svg", ".webp", ".tif", ".tiff", ".heic", ".bmp", ".pdf"}
IMAGE_REF_RE = re.compile(r"(!\[[^\]]*\]\()([^\)\s]+)(\))")
HTML_IMG_SRC_RE = re.compile(r"(<img\b[^>]*\bsrc=[\"'])([^\"']+)([\"'][^>]*>)", re.IGNORECASE)
FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
ALLOWED_COVER_STYLES = {
    "水墨写意",
    "工笔重彩",
    "青绿山水",
    "宋画风",
    "敦煌壁画风",
    "文人画",
    "新中式国风",
}


def is_remote(path: str) -> bool:
    return path.startswith("http://") or path.startswith("https://")


def strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def quote_yaml(value: str) -> str:
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def unique_path(directory: Path, name: str) -> Path:
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    counter = 2
    while True:
        next_candidate = directory / f"{stem}-{counter}{suffix}"
        if not next_candidate.exists():
            return next_candidate
        counter += 1


def resolve_local_path(raw_path: str, base_dir: Path) -> Path:
    raw_path = strip_quotes(raw_path).split("#", 1)[0].split("?", 1)[0]
    path = Path(raw_path)
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def png_is_readable(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 8 and path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def convert_image(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        ["sips", "-s", "format", "png", str(source), "--out", str(target)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode == 0 and png_is_readable(target):
        return

    raise RuntimeError(
        f"failed to convert image to PNG: {source}\n{result.stderr.strip()}"
    )


def stage_image(raw_path: str, base_dir: Path, asset_dir: Path, cache: dict[str, str]) -> str:
    if is_remote(raw_path):
        return raw_path

    source = resolve_local_path(raw_path, base_dir)
    if not source.exists():
        raise FileNotFoundError(f"image file not found: {source}")

    cache_key = str(source)
    if cache_key in cache:
        return cache[cache_key]

    ext = source.suffix.lower()
    if ext in SUPPORTED_IMAGE_EXTS:
        target = unique_path(asset_dir, source.name)
        shutil.copy2(source, target)
    elif ext in CONVERTIBLE_IMAGE_EXTS or ext:
        target = unique_path(asset_dir, f"{source.stem}.png")
        convert_image(source, target)
    else:
        raise ValueError(f"unsupported image without extension: {source}")

    staged_ref = f"./{target.name}"
    cache[cache_key] = staged_ref
    return staged_ref


def parse_frontmatter(content: str) -> tuple[dict[str, str | list[str]], str]:
    match = FRONTMATTER_RE.match(content)
    if not match:
        return {}, content

    data: dict[str, str | list[str]] = {}
    current_list = ""
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if current_list and stripped.startswith("- "):
            value = strip_quotes(stripped[2:])
            existing = data.get(current_list)
            if isinstance(existing, list) and value:
                existing.append(value)
            continue
        current_list = ""
        if not stripped or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = strip_quotes(value)
        if value:
            data[key] = value
        else:
            data[key] = []
            current_list = key
    return data, content[match.end():]


def build_frontmatter(data: dict[str, str | list[str]]) -> str:
    preferred = ["title", "cover", "cover_generation", "cover_style", "cover_poem", "cover_typography", "author"]
    lines = ["---"]
    for key in preferred + [key for key in data if key not in preferred]:
        value = data.get(key, "")
        if isinstance(value, list):
            if value:
                lines.append(f"{key}:")
                lines.extend(f"  - {quote_yaml(item)}" for item in value)
        elif value.strip():
            lines.append(f"{key}: {quote_yaml(value)}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare Markdown and images for WeChat upload.")
    parser.add_argument("article")
    parser.add_argument("--output", required=True)
    parser.add_argument("--title", default="")
    parser.add_argument("--cover", default="")
    parser.add_argument("--author", default="")
    args = parser.parse_args()

    article = Path(args.article).resolve()
    output = Path(args.output).resolve()
    base_dir = article.parent
    asset_dir = output.parent
    cache: dict[str, str] = {}

    content = article.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(content)

    if args.title:
        frontmatter["title"] = args.title
    if args.author:
        frontmatter["author"] = args.author
    if args.cover:
        frontmatter["cover"] = args.cover

    cover_value = frontmatter.get("cover", "")
    if isinstance(cover_value, str) and cover_value and not is_remote(cover_value):
        frontmatter["cover"] = stage_image(cover_value, base_dir, asset_dir, cache)

    def replace_image(match: re.Match[str]) -> str:
        prefix, raw_path, suffix = match.groups()
        if raw_path.startswith("<") and raw_path.endswith(">"):
            inner_path = raw_path[1:-1]
            staged = stage_image(inner_path, base_dir, asset_dir, cache)
            return f"{prefix}<{staged}>{suffix}"
        staged = stage_image(raw_path, base_dir, asset_dir, cache)
        return f"{prefix}{staged}{suffix}"

    def replace_html_image(match: re.Match[str]) -> str:
        prefix, raw_path, suffix = match.groups()
        staged = stage_image(raw_path, base_dir, asset_dir, cache)
        return f"{prefix}{staged}{suffix}"

    body = IMAGE_REF_RE.sub(replace_image, body)
    body = HTML_IMG_SRC_RE.sub(replace_html_image, body)

    if not frontmatter.get("title"):
        raise ValueError("article title is required in frontmatter or --title")
    if not frontmatter.get("cover"):
        raise ValueError("cover image is required in frontmatter or --cover")
    cover_generation = frontmatter.get("cover_generation", "")
    cover_style = frontmatter.get("cover_style", "")
    cover_poem = frontmatter.get("cover_poem", [])
    cover_typography = frontmatter.get("cover_typography", "")
    if not isinstance(cover_generation, str) or not isinstance(cover_style, str):
        raise ValueError("cover generation and style metadata must be strings")
    cover_generation = cover_generation.strip().lower()
    cover_style = cover_style.strip()
    if cover_generation != "ai":
        raise ValueError("cover_generation must be 'ai'; HTML-rendered or stock covers are not allowed")
    if cover_style not in ALLOWED_COVER_STYLES:
        allowed = ", ".join(sorted(ALLOWED_COVER_STYLES))
        raise ValueError(f"cover_style must be one approved Chinese painting style: {allowed}")
    if not isinstance(cover_poem, list) or len(cover_poem) not in {2, 4} or any(not line.strip() for line in cover_poem):
        raise ValueError("cover_poem must contain exactly 2 or 4 non-empty lines")
    if not isinstance(cover_typography, str) or cover_typography.strip() not in {"vertical", "horizontal", "split"}:
        raise ValueError("cover_typography must be vertical, horizontal, or split")

    output.write_text(build_frontmatter(frontmatter) + body, encoding="utf-8")
    print(f"Prepared WeChat Markdown: {output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
