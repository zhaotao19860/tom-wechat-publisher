#!/usr/bin/env python3
"""Persist a rendered preview and its referenced staged local assets."""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit


CHARSET_RE = re.compile(r"<meta\s+[^>]*charset\s*=", flags=re.I)


def ensure_utf8_charset(html: str) -> str:
    if CHARSET_RE.search(html):
        return html
    meta = '<meta charset="utf-8">'
    if re.search(r"<head(?:\s[^>]*)?>", html, flags=re.I):
        return re.sub(r"(<head(?:\s[^>]*)?>)", rf"\1{meta}", html, count=1, flags=re.I)
    if re.search(r"<html(?:\s[^>]*)?>", html, flags=re.I):
        return re.sub(r"(<html(?:\s[^>]*)?>)", rf"\1<head>{meta}</head>", html, count=1, flags=re.I)
    return meta + html


def persist_preview(preview: Path, staged_dir: Path, target_dir: Path) -> Path:
    preview = preview.resolve()
    staged_dir = staged_dir.resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    output = target_dir / "preview.html"
    html = preview.read_text(encoding="utf-8")
    output.write_text(ensure_utf8_charset(html), encoding="utf-8")
    refs = re.findall(r"(?:src|href)=[\"']([^\"']+)[\"']", html, flags=re.I)
    for raw_ref in refs:
        parsed = urlsplit(raw_ref)
        if parsed.scheme or raw_ref.startswith(("//", "data:", "#")):
            continue
        relative = Path(unquote(parsed.path))
        source = (staged_dir / relative).resolve()
        try:
            source.relative_to(staged_dir)
        except ValueError:
            continue
        if source.is_file():
            destination = target_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    return output


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("usage: persist_preview.py PREVIEW STAGED_DIR TARGET_DIR")
    print(persist_preview(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])))
