#!/usr/bin/env python3
"""Validate the cover metadata and local asset required for publication."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from prepare_wechat_md import ALLOWED_COVER_STYLES, is_remote, parse_frontmatter


def validate_article(article: Path) -> list[str]:
    errors: list[str] = []
    try:
        data, _ = parse_frontmatter(article.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        return [f"article could not be read: {exc}"]

    cover = data.get("cover", "")
    if not isinstance(cover, str) or not cover.strip():
        errors.append("cover file is required")
    elif is_remote(cover):
        errors.append("cover file must be a local asset")
    elif not (article.parent / cover).resolve().is_file():
        errors.append(f"cover file does not exist: {cover}")

    generation = data.get("cover_generation", "")
    if not isinstance(generation, str) or generation.strip().lower() != "ai":
        errors.append("cover_generation must be 'ai'")

    style = data.get("cover_style", "")
    if not isinstance(style, str) or style.strip() not in ALLOWED_COVER_STYLES:
        errors.append("cover_style must be an approved Chinese painting style")

    poem = data.get("cover_poem", [])
    if not isinstance(poem, list) or len(poem) not in {2, 4} or any(not str(line).strip() for line in poem):
        errors.append("cover_poem must contain exactly 2 or 4 non-empty lines")

    typography = data.get("cover_typography", "")
    if not isinstance(typography, str) or typography.strip() not in {"vertical", "horizontal", "split"}:
        errors.append("cover_typography must be vertical, horizontal, or split")
    return errors


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: validate_article_assets.py article.md", file=sys.stderr)
        return 2
    article = Path(argv[1]).resolve()
    errors = validate_article(article)
    if errors:
        print("article asset preflight failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print("article asset preflight passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
