#!/usr/bin/env python3
"""Content tracks (内容线) for the WeChat account.

An article declares its track with an optional frontmatter field::

    track: l4-gateway

Known tracks are ``dns``, ``l4-gateway`` and ``ai-gateway``. A missing or
unknown value always resolves to ``dns`` so older articles behave exactly as
before.

CLI (used by publish_draft.sh)::

    tracks.py track ARTICLE.md   -> prints the resolved track id
    tracks.py album ARTICLE.md   -> prints the recommended album name
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional

DEFAULT_TRACK = "dns"

L4_GATEWAY_TOKENS = (
    "FullNAT", "SYN Cookie", "conntrack", "Maglev", "eBPF", "XDP",
    "LVS", "DPDK", "VIP", "ECMP", "BGP", "ARP", "SNAT", "DNAT", "NAT",
    "RS", "DR", "TUN", "QPS", "PPS", "CPS",
    "会话保持", "健康检查", "一致性哈希",
)

AI_GATEWAY_TOKENS = (
    "Prompt Cache", "API Key", "Streaming", "Embedding", "Anthropic", "OpenAI",
    "LiteLLM", "Higress", "Envoy", "Kong", "vLLM",
    "fallback", "Token", "LLM", "TPM", "RPM", "MCP", "SSE",
    "模型路由", "限流", "熔断", "鉴权",
)

TRACKS = {
    "dns": {
        "display_name": "DNS",
        "kicker": "DNS Daily",
        "kicker_upper": "DNS DAILY",
        "column": "安全观察",
        "album": "DNS知识库",
        "tokens": (),  # DNS vocabulary lives in image_spec.DNS_TECHNICAL_TOKENS
    },
    "l4-gateway": {
        "display_name": "四层负载均衡网关",
        "kicker": "L4 GATEWAY",
        "kicker_upper": "L4 GATEWAY",
        "column": "架构实践",
        "album": "网关知识库",
        "tokens": L4_GATEWAY_TOKENS,
    },
    "ai-gateway": {
        "display_name": "七层 AI 网关",
        "kicker": "AI GATEWAY",
        "kicker_upper": "AI GATEWAY",
        "column": "架构实践",
        "album": "网关知识库",
        "tokens": AI_GATEWAY_TOKENS,
    },
}


def normalize_track(value: object) -> str:
    """Return a known track id; missing or unknown values become ``dns``."""
    key = str(value or "").strip().strip('"').strip("'").lower()
    return key if key in TRACKS else DEFAULT_TRACK


def track_info(value: object) -> dict:
    return TRACKS[normalize_track(value)]


def kicker(value: object, upper: bool = False) -> str:
    info = track_info(value)
    return info["kicker_upper"] if upper else info["kicker"]


def column(value: object) -> str:
    """Chinese column label shown after the kicker on the dark-gold cover."""
    return track_info(value)["column"]


def album(value: object) -> str:
    return track_info(value)["album"]


def track_tokens(value: object) -> tuple:
    return track_info(value)["tokens"]


def _frontmatter_block(text: str) -> Optional[str]:
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None
    return text[3:end]


def raw_track_from_markdown(text: str) -> str:
    """Return the literal frontmatter ``track:`` value, or "" when absent."""
    block = _frontmatter_block(text)
    if block is None:
        return ""
    match = re.search(r"(?mi)^track\s*:\s*(.*?)\s*$", block)
    if not match:
        return ""
    return match.group(1).strip().strip('"').strip("'")


def track_from_markdown(text: str) -> str:
    return normalize_track(raw_track_from_markdown(text))


def track_from_path(path: Path) -> str:
    try:
        return track_from_markdown(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        return DEFAULT_TRACK


def ensure_frontmatter_track(markdown: str, track: str) -> str:
    """Insert ``track: <id>`` into existing frontmatter unless one is present."""
    block = _frontmatter_block(markdown)
    if block is None or raw_track_from_markdown(markdown):
        return markdown
    end = markdown.find("\n---", 3)
    return markdown[:end] + "\ntrack: " + normalize_track(track) + markdown[end:]


# Words that strongly suggest a gateway article. Matched case-insensitively on word
# boundaries for ASCII terms, so "RS" in "users" or "VIP" in prose do not fire alone.
GATEWAY_HINTS = {
    "l4-gateway": ("LVS", "FullNAT", "四层网关", "四层负载均衡", "ECMP", "Maglev", "conntrack", "DPVS", "XDP"),
    "ai-gateway": ("AI 网关", "AI网关", "模型路由", "LLM 网关", "LiteLLM", "Higress", "TPM", "token 计费", "OpenAI 兼容"),
}


def suggest_track(text: str) -> str:
    """Return a gateway track when the body has >=2 distinct hint terms, else ''."""
    best, best_hits = "", 1
    for track, words in GATEWAY_HINTS.items():
        hits = sum(1 for w in words if (re.search(r"(?<![A-Za-z])" + re.escape(w) + r"(?![A-Za-z])", text, re.I)
                                        if w.isascii() else w in text))
        if hits > best_hits:
            best, best_hits = track, hits
    return best


def cli(argv: list) -> int:
    if len(argv) != 3 or argv[1] not in {"track", "album", "hint"}:
        print("usage: tracks.py {track|album|hint} ARTICLE.md", file=sys.stderr)
        return 2
    path = Path(argv[2])
    if argv[1] == "hint":
        text = path.read_text(encoding="utf-8")
        if raw_track_from_markdown(text):
            return 0
        guess = suggest_track(text)
        if guess:
            print(f"Track hint: 正文像是网关文章，frontmatter 没写 track；如果是，请加 `track: {guess}`（不加就按 DNS 处理，眉标和合集提醒都会按 DNS）")
        return 0
    track = track_from_path(path)
    print(track if argv[1] == "track" else album(track))
    return 0


if __name__ == "__main__":
    raise SystemExit(cli(sys.argv))
