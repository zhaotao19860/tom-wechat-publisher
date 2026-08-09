from __future__ import annotations

import re
import sys
from pathlib import Path


SUCCESS_RE = re.compile(
    r"发布成功|draft\s+created\s+successfully|publish(?:ed)?\s+successfully", re.I
)
FAILURE_RE = re.compile(
    r"未\s*发布成功|发布失败|草稿(?:创建)?失败|\b(?:not|never|failed|failure|error)\b",
    re.I,
)
MEDIA_ID_RE = re.compile(
    r"(?<![A-Za-z0-9_])media[ _-]?id(?![A-Za-z0-9_])\s*[:=]\s*"
    r"([A-Za-z0-9_-]+)(?![A-Za-z0-9_-])",
    re.I,
)


def extract_draft_media_id(output: str) -> str:
    media_id_found = False
    for line in output.splitlines():
        match = MEDIA_ID_RE.search(line)
        if not match:
            continue
        media_id_found = True
        if SUCCESS_RE.search(line) and not FAILURE_RE.search(line):
            return match.group(1)
    if not media_id_found:
        raise ValueError("draft Media ID was not found")
    raise ValueError("explicit draft success wording was not found")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("draft creation evidence invalid", file=sys.stderr)
        return 1
    try:
        output = Path(argv[1]).read_text(encoding="utf-8")
        media_id = extract_draft_media_id(output)
    except (OSError, UnicodeError, ValueError):
        print("draft creation evidence invalid", file=sys.stderr)
        return 1
    print(media_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
