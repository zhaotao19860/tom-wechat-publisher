#!/usr/bin/env python3
"""Pick the next Chinese-painting cover style by round-robin over history.

The skill cycles deterministically through the ordered style list instead of
drawing at random: each new article uses the style that follows the most
recently used one, wrapping back to the first after the last. This keeps
consecutive covers from repeating until the whole list has been used.

Usage:
  next_cover_style.py [--history-root DIR] [--styles "a,b,c"] [--verbose]

Output: the chosen style on stdout (single line). Diagnostics go to stderr.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# Canonical ordered style list. Keep in sync with SKILL.md.
DEFAULT_STYLES = [
    "水墨写意",
    "工笔重彩",
    "青绿山水",
    "宋画风",
    "敦煌壁画风",
    "文人画",
    "新中式国风",
]

_STYLE_RE = re.compile(r'^\s*cover_style\s*:\s*["\']?([^"\'\n#]+?)["\']?\s*$', re.MULTILINE)
# Match a leading article date like 2026-0925 or 2026-09-25 with a plausible
# month; anything else (CVE ids, AS numbers, undated folders) yields no date.
_DATE_RE = re.compile(r"^(20\d{2})-?(0[1-9]|1[0-2])-?(\d{2})?")


def _read_cover_style(md: Path) -> str:
    try:
        text = md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    # Only inspect the leading frontmatter block.
    if text.startswith("---"):
        end = text.find("\n---", 3)
        head = text[: end if end != -1 else len(text)]
    else:
        head = text[:2000]
    m = _STYLE_RE.search(head)
    return m.group(1).strip() if m else ""


def _sort_key(md: Path) -> tuple:
    """Order articles chronologically; dated folder name first, mtime as tiebreak."""
    name = md.parent.name
    dm = _DATE_RE.search(name)
    date_key = ""
    if dm:
        date_key = f"{dm.group(1)}{dm.group(2)}{dm.group(3) or '00'}"
    try:
        mtime = md.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (date_key, mtime)


def latest_used_style(history_root: Path, styles: list[str]) -> tuple[str, Path | None]:
    articles = [
        p for p in history_root.rglob("article.md")
        if p.is_file()
    ]
    articles.sort(key=_sort_key)
    for md in reversed(articles):
        style = _read_cover_style(md)
        if style in styles:
            return style, md
    return "", None


def next_style(history_root: Path, styles: list[str]) -> tuple[str, str]:
    last, _ = latest_used_style(history_root, styles)
    if last in styles:
        idx = (styles.index(last) + 1) % len(styles)
    else:
        idx = 0
    return styles[idx], last


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--history-root",
        default=os.environ.get("TOM_WECHAT_HISTORY_ROOT")
        or str(Path.home() / "Desktop" / "wechat"),
    )
    parser.add_argument("--styles", default="", help="comma-separated override of the ordered list")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv[1:])

    styles = [s.strip() for s in args.styles.split(",") if s.strip()] if args.styles else DEFAULT_STYLES
    if not styles:
        print("ERROR: empty style list", file=sys.stderr)
        return 2

    root = Path(args.history_root).expanduser()
    if not root.is_dir():
        # No history yet: start the cycle at the first style.
        print(styles[0])
        if args.verbose:
            print(f"[next-cover-style] history root missing ({root}); starting at {styles[0]}", file=sys.stderr)
        return 0

    chosen, last = next_style(root, styles)
    print(chosen)
    if args.verbose:
        prev = last or "<none>"
        print(f"[next-cover-style] previous={prev} -> next={chosen} (cycle of {len(styles)})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
