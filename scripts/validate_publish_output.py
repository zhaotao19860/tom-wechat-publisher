from __future__ import annotations

import re
import sys
from pathlib import Path


SUCCESS_RE = re.compile(
    r"发布成功|draft\s+created\s+successfully|publish(?:ed)?\s+successfully", re.I
)
MEDIA_ID_RE = re.compile(r"(?:media[ _-]?id)\s*[:=]\s*([A-Za-z0-9_-]+)", re.I)


def extract_draft_media_id(output: str) -> str:
    match = MEDIA_ID_RE.search(output)
    if not match:
        raise ValueError("draft Media ID was not found")
    if not SUCCESS_RE.search(output):
        raise ValueError("explicit draft success wording was not found")
    return match.group(1)


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
