#!/usr/bin/env python3
"""
image_spec.py — Derive structured image specs from a finalized article.

The previous pipeline composed Nano Banana prompts directly from skeleton
section text. That caused two failure modes: meta instructions like
"后续 agent 自动补全" being painted into the image, and image models
hallucinating exact protocol names. This module replaces that step with a
deterministic extractor that:

  1) Parses the finalized Markdown after the article body is stable.
  2) Emits a structured JSON spec per illustration: {kind, title_zh,
     subtitle, items, palette}.
  3) Leaves rendering to either deterministic HTML (for label-heavy body
     information graphics) or /create-image raster art (for covers and
     text-light conceptual illustrations).

The spec is intentionally small and deterministic: rendering backends must
be able to produce a correct, audit-friendly diagram from these fields
without ever calling an image model for text.
"""
import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional


# A small palette pool. Backends pick by index so colors remain stable
# across runs of the same article.
PALETTES = [
    {"primary": "#2563eb", "accent": "#0ea5e9", "soft": "#eff6ff", "ink": "#10243f"},
    {"primary": "#0f766e", "accent": "#14b8a6", "soft": "#ecfdf5", "ink": "#0b3b36"},
    {"primary": "#7c3aed", "accent": "#a855f7", "soft": "#f5f3ff", "ink": "#2e1065"},
    {"primary": "#f59e0b", "accent": "#f97316", "soft": "#fff7ed", "ink": "#3f2208"},
    {"primary": "#dc2626", "accent": "#ef4444", "soft": "#fef2f2", "ink": "#3b0a0a"},
]

VISUAL_THEMES: dict[str, dict[str, Any]] = {
    "clean-professional": {
        "palette_index": 0,
        "palettes": [
            {"primary": "#2563eb", "accent": "#0ea5e9", "soft": "#f5f5f5", "ink": "#374151"},
            {"primary": "#1d4ed8", "accent": "#ff6b35", "soft": "#f5f5f5", "ink": "#111827"},
        ],
    },
    "editorial-magazine": {
        "palette_index": 1,
        "palettes": [
            {"primary": "#1d4ed8", "accent": "#f97316", "soft": "#eff6ff", "ink": "#0f172a"},
            {"primary": "#0f766e", "accent": "#e11d48", "soft": "#ecfdf5", "ink": "#10243f"},
            {"primary": "#7c3aed", "accent": "#f59e0b", "soft": "#f5f3ff", "ink": "#1f1235"},
            {"primary": "#0ea5e9", "accent": "#dc2626", "soft": "#f0f9ff", "ink": "#082f49"},
        ],
    },
    "snoopy-comic": {
        "palette_index": 2,
        "palettes": [
            {"primary": "#7c5e2f", "accent": "#eab308", "soft": "#fdf6ec", "ink": "#3f2f1b"},
            {"primary": "#8b5e34", "accent": "#60a5fa", "soft": "#fff7ed", "ink": "#3f2208"},
        ],
    },
    "bold-statement": {
        "palette_index": 3,
        "palettes": [
            {"primary": "#111827", "accent": "#f59e0b", "soft": "#e5e7eb", "ink": "#030712"},
            {"primary": "#1a1a2e", "accent": "#e2b714", "soft": "#f3f4f6", "ink": "#111827"},
        ],
    },
    "data-infographic": {
        "palette_index": 4,
        "palettes": [
            {"primary": "#2563eb", "accent": "#f97316", "soft": "#f8fafc", "ink": "#1f2937"},
            {"primary": "#0f766e", "accent": "#dc2626", "soft": "#f5f5f5", "ink": "#111827"},
        ],
    },
    "whiteboard-sketch": {
        "palette_index": 5,
        "palettes": [
            {"primary": "#111827", "accent": "#dc2626", "soft": "#f8fafc", "ink": "#030712"},
            {"primary": "#1f2937", "accent": "#2563eb", "soft": "#f5f5f5", "ink": "#111827"},
        ],
    },
    "warm-narrative": {
        "palette_index": 6,
        "palettes": [
            {"primary": "#7c2d12", "accent": "#e17055", "soft": "#fdf6ec", "ink": "#3f2208"},
            {"primary": "#9a3412", "accent": "#4a90d9", "soft": "#fff7ed", "ink": "#431407"},
        ],
    },
}


KIND_KEYWORDS = (
    ("flow", ("流程", "工作", "怎么工作", "链路", "攻击链", "链", "路径", "步骤", "时序", "瞬间", "卡在", "触发")),
    ("compare", ("对比", "区别", "之前", "之后", "覆盖", "不覆盖", "范围", "边界", "哪些环境", "命中", "方案", "怎么选")),
    ("checklist", ("清单", "落地", "做什么", "操作", "执行", "动作", "建议", "排查", "配置", "检查", "相关 RFC", "checklist")),
    ("impact", ("影响", "风险", "覆盖", "不覆盖", "适用", "不适用")),
    ("summary", ("关键信息", "一分钟", "总览", "速览", "summary")),
    ("relations", ("关系", "角色", "组件", "拓扑", "原理")),
    ("timeline", ("时间", "时间线", "演进", "历史", "趋势")),
)


@dataclass
class ImageSpec:
    filename: str               # output base filename (extension is set by renderer)
    kind: str                   # cover | summary | flow | compare | checklist | impact | timeline | relations
    title_zh: str               # exact Chinese title rendered on the image
    subtitle: str = ""          # optional one-line subtitle
    items: list[dict] = field(default_factory=list)  # list of {label, sub} or {step, text}
    palette_index: int = 0
    visual_theme: str = ""
    theme_palette: dict = field(default_factory=dict)
    source_section: str = ""    # markdown section header this spec was derived from
    notes: str = ""             # human note for audit log
    cover_text: list[str] = field(default_factory=list)  # short poem lines for AI cover calligraphy
    cover_style: str = ""       # approved Chinese-painting style from frontmatter
    cover_typography: str = ""  # vertical | horizontal | split
    text_style: str = ""         # cover calligraphy / body text rendering hint
    text_policy: str = ""        # renderer/audit policy for visible text
    render_mode: str = "ai"      # ai | html | hybrid
    fact_risk: str = "low"       # low | medium | high
    illustration_backend: str = ""
    rendered_by: str = ""
    overlay_applied: bool = False
    structure: str = "text-scene"
    purpose: str = ""
    labels: list[str] = field(default_factory=list)
    locked_facts: list[str] = field(default_factory=list)
    qa_rules: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Markdown parsing helpers
# ---------------------------------------------------------------------------


def _strip_md(text: str) -> str:
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[#>*_`]+", " ", text)
    text = re.sub(r"（?正文要点将由后续\s*agent[^。]*。）?", " ", text, flags=re.I)
    text = re.sub(r"如需手工编辑[^。]*。", " ", text)
    text = re.sub(r"\b(agent|prompt|placeholder|TODO|自动补全|后续补全)\b", " ", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def _parse_frontmatter(md: str) -> tuple[dict, str]:
    if md.startswith("---"):
        end = md.find("\n---", 3)
        if end != -1:
            block = md[3:end].strip()
            body = md[end + 4:].lstrip("\n")
            fm: dict[str, Any] = {}
            current_list = ""
            for line in block.splitlines():
                stripped = line.strip()
                if current_list and stripped.startswith("- "):
                    value = stripped[2:].strip().strip('"').strip("'")
                    if value:
                        fm[current_list].append(value)
                    continue
                current_list = ""
                if ":" in line:
                    k, v = line.split(":", 1)
                    key = k.strip()
                    value = v.strip().strip('"').strip("'")
                    if value:
                        fm[key] = value
                    else:
                        fm[key] = []
                        current_list = key
            return fm, body
    return {}, md


def _parse_sections(body: str) -> list[tuple[str, str]]:
    """Return [(title, body_text)] for each ## section, excluding 参考资料."""
    matches = list(re.finditer(r"^##\s+(.+?)\s*$", body, flags=re.MULTILINE))
    out: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        if title in ("参考资料", "References", "参考"):
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        out.append((title, body[start:end]))
    return out


def _intro_image_sections(body: str) -> list[tuple[str, str]]:
    """Return image-bearing content before the first level-two heading."""
    first_section = re.search(r"^##\s+", body, flags=re.MULTILINE)
    intro = body[:first_section.start()] if first_section else body
    if re.search(r"!\[[^\]]*\]\([^)]+\)", intro):
        return [("", intro)]
    return []


def _image_basename(section_body: str, fallback: str) -> str:
    match = re.search(r"!\[[^\]]*\]\(([^)]+)\)", section_body)
    if not match:
        return fallback
    path = match.group(1).strip().strip('"\'')
    name = Path(path).stem
    return name or fallback


def _extract_bullets(section_body: str, limit: int = 6) -> list[str]:
    bullets: list[str] = []
    lines = section_body.splitlines()
    idx = 0
    while idx < len(lines):
        line = lines[idx].strip()
        idx += 1
        if not line or line.startswith("!["):
            continue
        m = re.match(r"^(?:[-*]|\d+[.)、])\s+(.+)$", line)
        if not m:
            continue
        text = _strip_md(m.group(1))
        continuation: list[str] = []
        while idx < len(lines):
            nxt = lines[idx].strip()
            if not nxt:
                idx += 1
                if continuation:
                    break
                continue
            if nxt.startswith("!["):
                idx += 1
                continue
            if re.match(r"^(?:[-*]|\d+[.)、])\s+", nxt) or nxt.startswith("##"):
                break
            plain = _strip_md(nxt)
            if plain:
                continuation.append(plain)
            idx += 1
            if len(" ".join(continuation)) >= 90:
                break
        if continuation:
            text = f"{text}：{' '.join(continuation)}"
        if text and 2 <= len(text) <= 180:
            bullets.append(text)
        if len(bullets) >= limit:
            break
    return bullets


def _extract_subheadings(section_body: str, limit: int = 6) -> list[str]:
    """Use level-three headings as stable category labels for comparisons."""
    return [
        _strip_md(match.group(1)).strip()
        for match in list(re.finditer(r"^###\s+(.+?)\s*$", section_body, flags=re.MULTILINE))[:limit]
    ]


def _extract_sentences(section_body: str, limit: int = 4, max_len: int = 40) -> list[str]:
    """Pull short declarative sentences when bullets are absent."""
    text = _strip_md(section_body)
    parts = re.split(r"[。！？!?\n]", text)
    out: list[str] = []
    for p in parts:
        s = p.strip()
        if 4 <= len(s) <= max_len:
            out.append(s)
        if len(out) >= limit:
            break
    return out


# DNS / security domain vocabulary used to pull concrete nouns from prose
# when bullets are absent. Order matters: longer / more specific tokens first.
DNS_TECHNICAL_TOKENS = (
    # Protocols
    "DNSSEC", "DNS-over-HTTPS", "DNS-over-TLS", "DNS-over-QUIC", "DNS-over-CoAP",
    "DoH", "DoT", "DoQ", "DoC", "DDR", "DoH3",
    "HTTPS", "HTTP/3", "QUIC", "TLS", "DTLS", "mTLS", "TCP", "UDP",
    "BGP", "OSPF", "RPKI", "EDNS", "EDNS0", "ECS",
    # Record types
    "SVCB", "HTTPS RR", "AAAA", "CNAME", "CAA", "TXT", "MX", "SRV", "PTR", "SOA", "NSEC3", "NSEC", "DNSKEY", "DS", "TLSA", "TSIG", "SIG(0)",
    # Roles / components
    "Recursive Resolver", "Authoritative", "Stub Resolver", "Forwarder", "Root Server",
    "Resolver", "Authority", "Cache", "Anycast", "Unicast",
    # Products / orgs (extend per article)
    "Unbound", "BIND", "Knot", "PowerDNS", "Cloudflare", "Quad9", "Google DNS",
    "OpenDNS", "NSD", "CoreDNS",
    # Concepts
    "Cache Poisoning", "Use-After-Free", "Race Condition", "Buffer Overflow",
    "Bailiwick", "Glue Record", "Zone Transfer", "AXFR", "IXFR", "RPZ", "QNAME minimization",
    "NXDOMAIN", "REFUSED", "SERVFAIL", "TTL",
    # CVE / vendor terms
    "CVSS", "NVD",
)

# Generic Chinese terms we never use as an image label (too vague to teach anything).
# Han-only — ASCII technical terms like "DNS" / "DoH" are handled by the
# technical-token whitelist path and never pass through this set.
_GENERIC_CN_STOPWORDS = {
    "问题", "情况", "方法", "原因", "影响", "意义", "重要", "需要", "可以", "应该",
    "建议", "总结", "概述", "介绍", "背景", "说明", "注意", "其他", "相关", "一些",
    "什么", "怎么", "怎样", "如何", "为什么", "因为", "所以", "但是", "然后",
    "关键", "信息", "内容", "部分", "情形", "场景", "方面", "事项", "风险", "边界",
    "清单", "流程", "对比", "关系", "步骤", "动作", "结论", "要点",
    "技术", "文章", "本文", "我们", "你们", "他们",
    # Connective / explanatory openers that look like nouns but are not concepts:
    "具体来说", "举例来说", "首先", "其次", "最后", "总的来说", "实际上", "事实上",
    "另一方面", "另外", "此外", "总之", "简而言之", "不过", "然而", "尽管如此",
    "由此可见", "也就是说", "换句话说", "一般来说", "通常来说", "比如说",
    "目前", "未来", "过去", "当前", "现在", "今天", "近期", "最近",
}


def _extract_technical_tokens(section_body: str, limit: int = 8) -> list[str]:
    """Return concrete domain nouns mentioned in the section.

    Combines a curated DNS/security whitelist with proper-noun candidates
    (capitalized ASCII words 3-30 chars, version-like tokens, CVE ids).
    """
    text = _strip_md(section_body)
    out: list[str] = []
    seen: set[str] = set()

    def _add(token: str) -> None:
        token = token.strip(" .,;:/()[]")
        if not token or len(token) < 2:
            return
        key = token.lower()
        if key in seen:
            return
        seen.add(key)
        out.append(token)

    # 1) CVE ids, resolver IPv4 addresses, and version numbers.
    for m in re.finditer(r"\bCVE-\d{4}-\d{4,7}\b", text):
        _add(m.group(0).upper())
    for m in re.finditer(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text):
        _add(m.group(0))
    for m in re.finditer(r"\b\d+\.\d+(?:\.\d+)?(?:-[A-Za-z0-9.]+)?\b", text):
        _add(m.group(0))

    # 2) Curated DNS whitelist (case-insensitive match, original casing kept)
    lower = text.lower()
    for token in DNS_TECHNICAL_TOKENS:
        if token.lower() in lower:
            _add(token)
            if len(out) >= limit:
                return out[:limit]

    # 3) Capitalized ASCII proper nouns (e.g. NLnet Labs, TinyDTLS, SCHC)
    for m in re.finditer(r"\b[A-Z][A-Za-z0-9]{2,}(?:[ -][A-Z][A-Za-z0-9]{1,})*\b", text):
        token = m.group(0)
        # Skip if it's a single short sentence-initial word
        if len(token) <= 2:
            continue
        _add(token)
        if len(out) >= limit:
            break

    return out[:limit]


def _extract_chinese_keynouns(section_body: str, limit: int = 6) -> list[str]:
    """Pull 2-4 character Chinese noun-phrases that are NOT generic stopwords.

    No jieba dependency. We scan Chinese runs and pick candidates that look
    like nouns by simple shape rules:
      - 2-4 Han characters in a row, frequency ≥ 2 OR length ≥ 3
      - first character is NOT a verb/connector head (了/的/和/在/把/被/会/有/能/将/这/那/也/与/为/对/从/到/向)
      - last character is NOT a particle (了/的/着/吗/呢/吧/啊/呀/嘛/么)
      - not a sentence-tail fragment ("响了递归" 这类切片碎片)
    The goal is to keep proper-noun-ish technical concepts ("缓存行为", "触发器",
    "递归解析器") while dropping mid-sentence slices ("次更新影", "响了递归").
    """
    text = _strip_md(section_body)
    runs = re.findall(r"[一-鿿]{2,}", text)
    _BAD_HEAD = set("了的和在把被会有能将这那也与为对从到向但即且或如若可使让须该当再又且仍则故才已并就要把及又故")
    _BAD_TAIL = set("了的着吗呢吧啊呀嘛么")
    _SOFT_TAIL = set("起来下去上去回来过来过去出来出去")  # verb-compound tails

    def _all_occurrences_at_boundary(ngram: str) -> bool:
        """Strict: EVERY occurrence of ngram in text must sit at a word boundary.

        A boundary means either side is not a Han character (i.e. it's
        punctuation, whitespace, ASCII, or string edge). This is conservative
        but reliably filters out mid-word slices like "响了递归" that happen
        to appear inside a longer phrase.
        """
        cursor = 0
        ok_count = 0
        while True:
            pos = text.find(ngram, cursor)
            if pos < 0:
                break
            left_ok = pos == 0 or not re.match(r"[一-鿿]", text[pos - 1])
            end = pos + len(ngram)
            right_ok = end >= len(text) or not re.match(r"[一-鿿]", text[end])
            if not (left_ok and right_ok):
                return False
            ok_count += 1
            cursor = pos + 1
        return ok_count > 0

    counts: dict[str, int] = {}
    for run in runs:
        for n in (4, 3, 2):
            for i in range(len(run) - n + 1):
                ngram = run[i:i + n]
                if ngram in _GENERIC_CN_STOPWORDS:
                    continue
                if ngram[0] in _BAD_HEAD or ngram[-1] in _BAD_TAIL:
                    continue
                if any(stop in ngram for stop in ("问题", "方法", "影响范围", "本文", "我们", "他们")):
                    continue
                if not _all_occurrences_at_boundary(ngram):
                    continue
                counts[ngram] = counts.get(ngram, 0) + 1

    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], -len(kv[0])))
    out: list[str] = []
    for ngram, freq in ranked:
        # For 2-char ngrams we require >= 2 occurrences; longer ones can pass
        # on a single hit because they're rarely accidental.
        if len(ngram) == 2 and freq < 2:
            continue
        if any(ngram in existing or existing in ngram for existing in out):
            continue
        out.append(ngram)
        if len(out) >= limit:
            break
    return out


def _fallback_concrete_items(sec_title: str, sec_body: str, limit: int = 4) -> list[str]:
    """Last-resort label source when bullets and short sentences are absent.

    Returns concrete strings ready to feed `_split_label`. Never returns the
    raw section title — that was the old "Key Flow: Key Flow" footgun.
    """
    tokens = _extract_technical_tokens(sec_body, limit=limit + 2)
    cn_nouns = _extract_chinese_keynouns(sec_body, limit=limit + 2)
    out: list[str] = []
    # Interleave: prefer one technical token, then one CN noun, etc., so the
    # downstream image gets a mix of recognized terms and article-specific
    # Chinese concepts.
    i = j = 0
    while len(out) < limit and (i < len(tokens) or j < len(cn_nouns)):
        if i < len(tokens):
            out.append(tokens[i])
            i += 1
            if len(out) >= limit:
                break
        if j < len(cn_nouns):
            out.append(cn_nouns[j])
            j += 1
    return out


GENERIC_SECTION_TITLES = {
    "关键信息",
    "问题从哪里来",
    "它大概怎么工作",
    "关键关系怎么理解",
    "风险和边界在哪里",
    "现在可以做什么",
    "一句话总结",
}

GENERIC_COVER_CTA = "把新闻变成可执行的判断"

SCAFFOLD_BULLET_PREFIXES = {
    "核心变化", "影响对象", "风险重点", "现在动作", "后续观察",
    "核对来源", "核对执行点", "核对透明度", "核对误封成本", "核对审计记录",
    "核对事实", "缩小范围", "安排动作", "留好记录", "更新入口",
}


def _classify_kind(title: str) -> str:
    t = title.lower()
    for kind, keys in KIND_KEYWORDS:
        for key in keys:
            if key in t or key in title:
                return kind
    return "summary"


def _split_label(text: str, label_len: int = 12, sub_len: int = 44) -> dict:
    """Return a readable label/sub pair without losing the concrete value."""
    clean = _strip_md(text).strip()
    key_value = re.match(r"^([^：:]{2,12})[：:]\s*(.+)$", clean)
    if key_value:
        key = key_value.group(1).strip()
        value = key_value.group(2).strip()
        if key in SCAFFOLD_BULLET_PREFIXES:
            concrete = _fallback_concrete_items("", value, limit=1)
            label = concrete[0] if concrete else re.split(r"[，,；;。！？!?]+", value, maxsplit=1)[0]
            return {
                "label": _short_value(label, label_len),
                "sub": _short_value(value, sub_len),
            }
        return {
            "label": _short(key, label_len),
            "sub": _short_value(value, sub_len),
        }
    parts = re.split(r"[，,；;。！？!?]+", clean, maxsplit=1)
    if len(parts[0]) > label_len and len(parts) > 1:
        return {
            "label": _short_value(parts[0], label_len + 8),
            "sub": _short_value(parts[1], sub_len),
        }
    return {
        "label": _short(parts[0], label_len),
        "sub": _short_value(parts[1], sub_len) if len(parts) > 1 else "",
    }


def _section_label_item(sec_title: str, sec_body: str, label_len: int = 10, sub_len: int = 22) -> dict:
    """Prefer concrete article facts over reusable skeleton section titles."""
    bullets = _extract_bullets(sec_body, limit=1)
    if sec_title in GENERIC_SECTION_TITLES and bullets:
        return _split_label(bullets[0], label_len, sub_len)
    return {"label": _short(sec_title, label_len), "sub": ""}


def _item_label_key(item: dict) -> str:
    return re.sub(r"\s+", "", str(item.get("label") or "").strip()).lower()


def _supplement_items(items: list[dict], sec_body: str, limit: int = 4) -> list[dict]:
    """Top up sparse specs with concrete terms from the same section.

    HTML information graphics need enough text anchors to be useful. When a
    section only has one or two bullets, supplement from technical tokens and
    Chinese noun phrases instead of falling back to reusable headings.
    """
    out = list(items)
    seen = {_item_label_key(item) for item in out}
    for text in _fallback_concrete_items("", sec_body, limit=limit + 2):
        item = _split_label(text, 20, 46)
        key = _item_label_key(item)
        if not key or key in seen:
            continue
        out.append(item)
        seen.add(key)
        if len(out) >= limit:
            break
    return out


def _dmarc_np_section_spec(sec_title: str, sec_body: str) -> Optional[tuple[str, list[dict]]]:
    """Build concrete diagram cards for DMARC np / DNSSEC compact-denial articles."""
    text = _strip_md(f"{sec_title} {sec_body}")
    lower = text.lower()
    if not ("dmarc" in lower or "dnssec" in lower or "nxdomain" in lower or "compact denial" in lower or "nxname" in lower):
        return None
    if "一分钟看懂" in sec_title:
        return None
    if "现在可以做什么" in sec_title:
        return "checklist", [
            {"label": "查托管 DNS", "sub": "确认 DNSSEC 域名是否托管在 compact denial 权威 DNS 上", "step": "1"},
            {"label": "测不存在子域", "sub": "比较 NXDOMAIN 与 NOERROR + NSEC/NSEC3 + NXNAME", "step": "2"},
            {"label": "查收件方实现", "sub": "确认 DMARC np 判断是否识别 NXNAME", "step": "3"},
            {"label": "保留父域防护", "sub": "p、sp、SPF、DKIM 和对齐监控仍是主防线", "step": "4"},
            {"label": "跟进标准讨论", "sub": "关注 RFC 9989 与 RFC 9824 的后续澄清", "step": "5"},
        ]
    if "给团队" in sec_title or "落地清单" in sec_title:
        return "checklist", [
            {"label": "补资产字段", "sub": "记录 DNSSEC、权威 DNS 厂商、否定应答形态", "step": "1"},
            {"label": "加测试样例", "sub": "覆盖 NXDOMAIN 与 compact denial 两种不存在子域返回", "step": "2"},
            {"label": "检查评估器", "sub": "确认 DMARC np 是否识别 NXNAME", "step": "3"},
            {"label": "问清供应商", "sub": "确认网关如何处理 RFC 9989 与 RFC 9824", "step": "4"},
        ]
    if "问题卡在哪个信号" in sec_title:
        return "impact", [
            {"label": "RFC 9989", "sub": "DMARC np 按 NXDOMAIN 判断不存在子域"},
            {"label": "NXDOMAIN", "sub": "传统 DNS 路径里，不存在名字会直接返回这个响应码"},
            {"label": "DNSSEC 否定证明", "sub": "NSEC/NSEC3 用签名记录证明名字不存在"},
            {"label": "Compact denial", "sub": "可能把不存在名字表达成 NOERROR + NSEC/NSEC3"},
            {"label": "NXNAME", "sub": "真正的不存在性信号藏在 NSEC/NSEC3 type bitmap 里"},
        ]
    if "Compact denial" in sec_title:
        return "impact", [
            {"label": "在线签名更轻", "sub": "compact denial 减少否定应答体积，适合在线 DNSSEC 签名"},
            {"label": "NOERROR", "sub": "客户端可能不再看到传统 NXDOMAIN 响应码"},
            {"label": "NXNAME", "sub": "RFC 9824 要求客户端从 NSEC/NSEC3 里识别不存在性"},
            {"label": "CO flag", "sub": "可请求恢复 NXDOMAIN，但现实链路支持不完整"},
            {"label": "DMARC 实现", "sub": "只看响应码时，np 可能不会触发"},
        ]
    if "影响范围" in sec_title:
        return "compare", [
            {"label": "域名启用 DNSSEC", "sub": "未启用 DNSSEC 的域名通常不进入这个问题路径"},
            {"label": "权威 DNS 用 compact denial", "sub": "Cloudflare、NS1、Route 53、Azure DNS 等均相关"},
            {"label": "收件方实现 np", "sub": "很多 DMARC 实现仍未完整支持 RFC 9989 np"},
            {"label": "实现只看 NXDOMAIN", "sub": "不识别 NXNAME 时容易把不存在子域误判为存在"},
            {"label": "需要实测确认", "sub": "闭源网关和商业收件系统不能只靠文档判断"},
        ]
    return None



def _public_dns_comparison_items(sec_title: str, sec_body: str) -> list[dict]:
    """Build rich comparison cards for public resolver governance sections."""
    text = _strip_md(f"{sec_title} {sec_body}")
    title_lower = _strip_md(sec_title).lower()
    body_lower = _strip_md(sec_body).lower()
    title_matches = "公共 dns" in title_lower or "public dns" in title_lower or "普通公共" in title_lower or "区别" in title_lower
    has_multiple_resolvers = sum(1 for token in ("8.8.8.8", "180.76.76.76", "1.1.1.1", "9.9.9.9", "cloudflare", "quad9") if token in body_lower) >= 2
    if not (title_matches and has_multiple_resolvers):
        return []
    lower = f"{title_lower} {body_lower}"
    resolver_profiles = [
        ("DNS4EU", ("dns4eu",), "欧盟资助，强调 GDPR、数字主权和多档过滤"),
        ("8.8.8.8", ("8.8.8.8", "google public dns", "google dns"), "Google Public DNS，默认中立解析，按司法命令阻断"),
        ("180.76.76.76", ("180.76.76.76", "百度公共 dns", "baidu"), "百度公共 DNS，更贴近中国大陆网络环境"),
        ("1.1.1.1", ("1.1.1.1", "cloudflare"), "Cloudflare，强调隐私，反对全球公共解析器承担内容封锁"),
        ("9.9.9.9", ("9.9.9.9", "quad9"), "Quad9，主要阻断恶意软件和钓鱼域名"),
    ]
    items: list[dict] = []
    for label, needles, sub in resolver_profiles:
        if any(needle in lower for needle in needles):
            items.append({"label": label, "sub": sub})
    return items


def _cover_cta(sections: list[tuple[str, str]], fallback: str) -> str:
    for _, sec_body in sections:
        bullets = _extract_bullets(sec_body, limit=3)
        for bullet in bullets:
            if bullet and not re.search(r"后续\s*agent|自动补全|placeholder|TODO", bullet, flags=re.I):
                return _short(bullet, 22)
    for _, sec_body in sections:
        sentences = _extract_sentences(sec_body, limit=2, max_len=80)
        for sentence in sentences:
            return _short(sentence, 22)
    return _short(fallback, 22)


def _poetic_cover_line(text: str) -> str:
    """Return a safe short line for cover calligraphy.

    Cover text is rendered by an image model, so partial sentences are worse than
    no text. Prefer compact noun phrases and reject fragments with unmatched
    quotes, dangling punctuation, or long numeric/legal wording.
    """
    raw = str(text or "").strip()
    line = re.sub(r"^(?:[-*]|\d+[.)、])\s+", "", raw)
    line = re.sub(r"[\[\]（）()《》<>#*_`]+", "", _strip_md(line)).strip(" -—，,；;：:。！？!?、\"'")
    if not line:
        return ""
    if re.search(r"[。！？!?；;]", line):
        return ""
    if line.count("“") != line.count("”") or line.count("‘") != line.count("’"):
        return ""
    if re.search(r"\d{2,}.*\d{2,}", line):
        return ""
    line = re.sub(r"\s+", "", line)
    if re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9+./-]{2,8}", line):
        return line
    m = re.search(r"([\u4e00-\u9fff]{2,8})(?:账本|成本|边界|限速|解析|查询|爬虫|基础设施|云厂商|站长|迁移|免费|收费)", line)
    if m:
        phrase = m.group(0)
        return phrase[:8]
    m = re.search(r"((?:免费|云上|域名|解析|查询|账本|成本|边界|爬虫|站长|迁移|限速)[\u4e00-\u9fff]{0,6})", line)
    if m and 2 <= len(m.group(1)) <= 8:
        return m.group(1)
    return ""


def _cover_keyword_phrases(title: str, article_text: str) -> list[str]:
    """Build stable cover inscription candidates from article-level keywords."""
    text = _strip_md(f"{title} {article_text}")
    lower = text.lower()
    phrase_rules = [
        (("dns", "限速"), "云上问名急"),
        (("免费", "账本"), "免费非无价"),
        (("基础设施", "成本"), "成本入深潭"),
        (("10 万", "pv"), "十万非客流"),
        (("权威", "递归"), "权递各有界"),
        (("爬虫", "ai"), "机流渐如潮"),
        (("迁移", "站长"), "站长临河望"),
        (("sla", "付费"), "稳字自有价"),
    ]
    phrases: list[str] = []
    for needles, phrase in phrase_rules:
        if all(needle.lower() in lower for needle in needles):
            phrases.append(phrase)
    title_clean = re.sub(r"\s+", "", _strip_md(title))
    title_clean = re.sub(r"[：:丨|｜].*$", "", title_clean)
    if title_clean:
        title_line = _poetic_cover_line(title_clean)
        if title_line:
            phrases.insert(0, title_line)
    out: list[str] = []
    seen: set[str] = set()
    for phrase in phrases:
        if 2 <= len(phrase) <= 8 and phrase not in seen:
            out.append(phrase)
            seen.add(phrase)
    return out


DEFAULT_COVER_QUATRAIN = ["山水开新卷", "风云入远汀", "孤灯查旧录", "一问见分明"]


def _four_line_cover_poem(candidates: list[str]) -> list[str]:
    lines = [line for line in candidates if re.fullmatch(r"[\u4e00-\u9fff]{5}", line)][:4]
    for slot in range(len(lines), 4):
        lines.append(DEFAULT_COVER_QUATRAIN[slot])
    return lines


def _cover_poem_lines(title: str, sections: list[tuple[str, str]]) -> list[str]:
    """Create four short calligraphy lines grounded in the current article.

    Prefer article-level poetic phrases over sentence truncation. This keeps AI
    covers in the intended Chinese-painting mode and avoids garbled half-lines.
    """
    article_text = _strip_md(" ".join([title, *[f"{sec_title} {sec_body}" for sec_title, sec_body in sections[:6]]]))
    lower = article_text.lower()
    if all(token in lower for token in ("dmarc", "dnssec")) and ("compact denial" in lower or "nxname" in lower):
        return _four_line_cover_poem(["空名问有无", "墨证辨真伪"])
    if (
        ("phantom squatting" in lower or "幻觉域名" in article_text or "胡编" in article_text)
        and ("域名" in article_text or "dns" in lower)
    ):
        return _four_line_cover_poem(["幻名浮水面", "真址辨归途"])
    if ("ai" in lower or "模型" in article_text) and "域名" in article_text and "钓鱼" in article_text:
        return _four_line_cover_poem(["模型生幻名", "问路辨真伪"])
    cleaned = _cover_keyword_phrases(title, article_text)
    fallback_candidates: list[str] = []
    for sec_title, sec_body in sections[:3]:
        fallback_candidates.append(sec_title)
        fallback_candidates.extend(_extract_bullets(sec_body, limit=1))
    for candidate in fallback_candidates:
        line = _poetic_cover_line(candidate)
        if line and line not in cleaned:
            cleaned.append(line)
    return _four_line_cover_poem(cleaned)


def _complete_ascii_tail(original: str, cut: str, n: int, max_extra: int = 36) -> str:
    """Avoid cutting technical ASCII tokens such as bailiwick or netty-resolver-dns."""
    if len(cut) >= len(original) or not cut:
        return cut
    if not re.search(r"[A-Za-z0-9_.:-]$", cut):
        return cut
    match = re.search(r"[A-Za-z0-9_.:-]+$", cut)
    if not match:
        return cut
    tail_start = match.start()
    token_match = re.match(r"[A-Za-z0-9_.:-]+", original[tail_start:])
    if not token_match:
        return cut
    completed = original[:tail_start] + token_match.group(0)
    if len(completed) <= n + max_extra:
        return completed
    if tail_start >= max(4, n // 2):
        return original[:tail_start].rstrip(" ，,；;：:。！？!?")
    return cut


def _short(text: str, n: int = 22) -> str:
    """Keep labels complete enough for explanatory diagrams."""
    text = _strip_md(text).strip()
    parts = re.split(r"[。！？!?]+", text, maxsplit=1)
    head = parts[0] if parts else text
    if len(head) <= n:
        return head.rstrip("，,；;：:。！？!?")
    for sep in ("，", ",", "；", ";", "、", " "):
        pos = head.rfind(sep, 0, n + 1)
        if pos >= max(8, n // 2):
            return head[:pos].rstrip("，,；;：:。！？!?")
    cut = head[:n].rstrip("，,；;：:。！？!?")
    return _complete_ascii_tail(head, cut, n, max_extra=18).rstrip("，,；;：:。！？!?")


def _short_value(text: str, n: int = 40) -> str:
    """Trim secondary text gently; diagrams can wrap text safely."""
    text = _strip_md(text).strip()
    head = re.split(r"[。！？!?]+", text, maxsplit=1)[0].strip()
    if len(head) <= n:
        return head
    technical = re.search(r"(?:CVE-\d{4}-\d+|[A-Za-z0-9_.-]+:[A-Za-z0-9_.:-]+|[A-Za-z0-9_.-]+\.[A-Za-z0-9_.-]+|\d+(?:\.\d+)+(?:\.[A-Za-z]+)*)", head)
    if technical and len(technical.group(0)) <= n + 24:
        return technical.group(0)
    for sep in ("；", ";", "，", ",", "、", " "):
        pos = head.rfind(sep, 0, n + 1)
        if pos >= max(12, n // 2):
            return head[:pos].rstrip("，,；;：:。！？!?")
    cut = head[:n].rstrip("，,；;：:。！？!?")
    completed = _complete_ascii_tail(head, cut, n, max_extra=24)
    return completed.rstrip("，,；;：:。！？!?")


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


def _theme_palette(visual_theme: str, slot_index: int) -> tuple[int, dict]:
    theme = VISUAL_THEMES.get(visual_theme)
    if not theme:
        idx = slot_index % len(PALETTES)
        return idx, PALETTES[idx]
    palettes = theme["palettes"]
    idx = int(theme["palette_index"])
    return idx, palettes[slot_index % len(palettes)]


def _assign_render_policy(spec: ImageSpec) -> None:
    exact_text = " ".join(
        [spec.title_zh, spec.subtitle, spec.source_section]
        + [str(value) for item in spec.items for value in item.values()]
    )
    code_or_data = re.findall(r"`([^`]+)`|\b(?:CVE|GHSA)-[A-Za-z0-9-]+\b|\b\d+(?:\.\d+){1,3}\b|\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+|\(\))", exact_text)
    locked = []
    for match in code_or_data:
        value = match if isinstance(match, str) else next((part for part in match if part), "")
        if value and value not in locked:
            locked.append(value)

    if spec.kind == "cover":
        spec.render_mode = "ai"
        spec.fact_risk = "low"
        spec.structure = "text-scene"
        spec.purpose = "建立文章主题和视觉记忆"
    elif spec.kind in {"checklist", "compare", "timeline"} or len(exact_text) > 260:
        spec.render_mode = "html"
        spec.fact_risk = "high"
        spec.structure = "checklist" if spec.kind == "checklist" else "data-first"
        spec.purpose = "准确呈现可执行事实和对照信息"
    elif spec.kind in {"flow", "relations"}:
        spec.render_mode = "hybrid"
        spec.fact_risk = "high" if locked else "medium"
        spec.structure = "pipeline" if spec.kind == "flow" else "hub-and-spoke"
        spec.purpose = "同时解释空间机制和精确处理路径"
    elif spec.kind in {"impact", "summary"}:
        spec.render_mode = "ai"
        spec.fact_risk = "low"
        spec.structure = "before-after" if spec.kind == "impact" else "text-scene"
        spec.purpose = "用场景和状态变化降低抽象理解成本"
    else:
        spec.render_mode = "hybrid"
        spec.fact_risk = "medium"
        spec.structure = "layer-stack"
        spec.purpose = "结合视觉对象与少量精确标签解释概念"

    if spec.render_mode in {"html", "hybrid"} and spec.fact_risk == "high" and not locked:
        for item in spec.items:
            for key in ("label", "sub"):
                value = str(item.get(key) or "").strip()
                if value and value not in locked:
                    locked.append(value)

    if spec.kind == "cover":
        spec.illustration_backend = "create-image"
    elif spec.render_mode == "ai" and spec.fact_risk == "low":
        spec.illustration_backend = "ian-xiaohei-illustrations"
    elif spec.render_mode == "html":
        spec.illustration_backend = "html-render"
    else:
        spec.illustration_backend = "html-render"

    spec.labels = [
        str(item.get("label") or item.get("step") or "").strip()
        for item in spec.items[:5]
        if str(item.get("label") or item.get("step") or "").strip()
    ]
    spec.locked_facts = locked
    spec.qa_rules = [
        "主体和标签不得裁切",
        "标签必须指向正确对象",
        "公众号宽度下仍可读",
        "不得出现无关英文、Logo 或水印",
    ]
    if locked:
        spec.qa_rules.append("锁定事实必须逐字匹配文章")


def derive_specs(markdown_path: Path, visual_theme: str = "") -> list[ImageSpec]:
    """Read article.md and produce one ImageSpec per planned illustration.

    Image filenames mirror the existing slot conventions used by
    generate_article.py so backends can be swapped without changing the
    Markdown.
    """
    md = markdown_path.read_text(encoding="utf-8")
    fm, body = _parse_frontmatter(md)
    title = fm.get("title") or _strip_md(re.search(r"^#\s+(.+)$", body, re.MULTILINE).group(1) if re.search(r"^#\s+(.+)$", body, re.MULTILINE) else "DNS 技术解读")
    sections = _parse_sections(body)

    specs: list[ImageSpec] = []
    visual_theme = visual_theme.strip().lower()

    # 1) Cover. Use AI-first Chinese painting style with article-derived poem text.
    cover_items = []
    for sec_title, sec_body in sections[:3]:
        cover_items.append(_section_label_item(sec_title, sec_body, 8, 24))
    cover_palette_index, cover_palette = _theme_palette(visual_theme, 0)
    cover_value = str(fm.get("cover") or "")
    cover_filename = Path(cover_value).stem if cover_value else "cover_article"
    explicit_poem = fm.get("cover_poem")
    cover_text = (
        [str(line).strip() for line in explicit_poem if str(line).strip()]
        if isinstance(explicit_poem, list)
        else _cover_poem_lines(title, sections)
    )
    specs.append(ImageSpec(
        filename=cover_filename,
        kind="cover",
        title_zh=title,
        subtitle="5 分钟看懂 · 图多字少",
        items=cover_items,
        palette_index=cover_palette_index,
        visual_theme=visual_theme,
        theme_palette=cover_palette,
        source_section="title",
        notes=_cover_cta(sections, title),
        cover_text=cover_text,
        cover_style=str(fm.get("cover_style") or ""),
        cover_typography=str(fm.get("cover_typography") or ""),
        text_style="Chinese calligraphy, lishu or kaishu, ink-brush title poem",
        text_policy="cover-poem-calligraphy",
    ))

    # 2..N) one spec per illustrated section. Prefer the real Markdown image
    # filename so adding a new image section cannot shift later slots.
    slot_filenames = [
        "img_section_summary",
        "img_section_context",
        "img_section_mechanism",
        "img_section_relations",
        "img_section_boundary",
        "img_section_actions",
    ]
    illustrated_sections = _intro_image_sections(body) + [
        (sec_title, sec_body)
        for sec_title, sec_body in sections
        if re.search(r"!\[[^\]]*\]\([^)]+\)", sec_body)
    ]
    if not illustrated_sections:
        # Keep the default article set focused: one image should resolve one
        # material reader question, not mirror every heading in the article.
        # Prefer mechanism, comparison, flow, and action sections, then fill
        # remaining slots in document order. Explicit Markdown image sections
        # are preserved above and are not reduced here.
        priority_terms = (
            "原理", "机制", "流程", "方案", "对比", "配置", "检查",
            "行动", "防护", "趋势", "风险", "路径", "架构",
        )
        ranked = sorted(
            enumerate(sections),
            key=lambda pair: (
                not any(term in pair[1][0] for term in priority_terms),
                pair[0],
            ),
        )
        selected_indexes = sorted(index for index, _ in ranked[:4])
        illustrated_sections = [sections[index] for index in selected_indexes]
    else:
        # Do not let an article with many pre-existing image references create
        # an unbounded image set; four body slots is the normal WeChat range.
        illustrated_sections = illustrated_sections[:4]
    for idx, (sec_title, sec_body) in enumerate(illustrated_sections):
        display_title = sec_title or "开场场景"
        kind = _classify_kind(display_title)
        bullets = _extract_bullets(sec_body, limit=6)
        subheadings = _extract_subheadings(sec_body, limit=6)
        if kind == "compare" and len(subheadings) >= 3:
            bullets = subheadings
        if not bullets:
            bullets = _extract_sentences(sec_body, limit=4)
        if not bullets:
            # Last-resort: pull concrete technical tokens + Chinese nounphrases
            # from the section text. Never reuse the section title (that was
            # the source of the old "Key Flow: Key Flow" placeholder bug).
            bullets = _fallback_concrete_items(sec_title, sec_body, limit=4)
        special = _dmarc_np_section_spec(display_title, sec_body)
        items: list[dict] = []
        if special:
            kind, items = special
        else:
            items = _public_dns_comparison_items(display_title, sec_body)
            if items:
                kind = "compare"
            elif kind == "flow":
                for i, b in enumerate(bullets[:5]):
                    item = _split_label(b, 20, 44)
                    item["step"] = str(i + 1)
                    items.append(item)
            elif kind in ("compare", "impact"):
                # Try to split into "覆盖/不覆盖" or "before/after" pairs.
                for b in bullets[:4]:
                    items.append(_split_label(b, 20, 46))
            elif kind == "checklist":
                for i, b in enumerate(bullets[:6]):
                    item = _split_label(b, 20, 46)
                    item["step"] = str(i + 1)
                    items.append(item)
            else:  # summary / relations / timeline
                for b in bullets[:4]:
                    items.append(_split_label(b, 20, 46))
        if not items:
            # Final fallback: try the technical-token / Chinese-noun extractor
            # one more time directly on the section body. Only if that also
            # comes back empty do we degrade to the section title — and we mark
            # the spec as low-quality so the prompt builder can flag it.
            concrete = _fallback_concrete_items(display_title, sec_body, limit=4)
            if concrete:
                items = [_split_label(t, 14, 30) for t in concrete]
            else:
                items = [{"label": _short(display_title, 10), "sub": "", "_low_quality": True}]
        elif kind != "cover" and len(items) < 3:
            items = _supplement_items(items, sec_body, limit=4)
        if kind == "flow":
            items = _supplement_items(items, sec_body, limit=5)
            deduped: list[dict] = []
            seen_steps: set[str] = set()
            for item in items:
                key = _item_label_key(item)
                if not key or key in seen_steps:
                    continue
                deduped.append(item)
                seen_steps.add(key)
            items = deduped
            sub_count = sum(1 for item in items if str(item.get("sub") or "").strip())
            if len(items) < 5 or sub_count < 4:
                # Some explanatory sections describe a relationship or boundary,
                # not an ordered process. Rendering them as a sparse flow creates
                # empty 16:9 diagrams, so downgrade before the quality gate.
                kind = "impact"
                for item in items:
                    item.pop("step", None)
            else:
                for i, item in enumerate(items[:5]):
                    item["step"] = str(i + 1)
                items = items[:5]
        fallback_filename = slot_filenames[idx] if idx < len(slot_filenames) else f"img_section_{idx}"
        palette_index, theme_palette = _theme_palette(visual_theme, idx + 1)
        specs.append(ImageSpec(
            filename=_image_basename(sec_body, fallback_filename),
            kind=kind,
            title_zh=display_title,
            subtitle="",
            items=items,
            palette_index=palette_index,
            visual_theme=visual_theme,
            theme_palette=theme_palette,
            source_section=sec_title,
            notes=f"derived from section: {sec_title or 'intro'}",
        ))
    for spec in specs:
        _assign_render_policy(spec)
    body_specs = [spec for spec in specs if spec.kind != "cover"]
    if body_specs:
        # Keep the first body slot as the editorial entry point. Exact facts
        # belong in later deterministic slots; the opening image should lower
        # cognitive load with a sparse Ian Xiaohei scene.
        first = body_specs[0]
        first.render_mode = "ai"
        first.fact_risk = "low"
        first.illustration_backend = "ian-xiaohei-illustrations"
        first.rendered_by = "ian-xiaohei-illustrations/create-image"
        first.structure = "text-scene"
        first.purpose = "用小黑的核心动作建立文章的第一视觉锚点"
        first.locked_facts = []
        first.labels = first.labels[:3]
        first.qa_rules = [
            "纯白背景",
            "小黑必须承担核心动作",
            "正文第一张图",
            "公众号宽度下主体清晰",
            "不得出现事实数字、命令或水印",
        ]
    return specs


def write_specs(specs: list[ImageSpec], out_path: Path) -> None:
    out_path.write_text(
        json.dumps([s.to_dict() for s in specs], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def cli(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: image_spec.py <article.md> [out_specs.json] | image_spec.py <article.md> --out out_specs.json", file=__import__("sys").stderr)
        return 2
    md = Path(argv[1])
    out = md.parent / "image_specs.json"
    visual_theme = ""
    i = 2
    while i < len(argv):
        if argv[i] == "--out":
            if i + 1 >= len(argv):
                print("ERROR: --out requires a value", file=__import__("sys").stderr)
                return 2
            out = Path(argv[i + 1])
            i += 2
        elif argv[i] == "--visual-theme":
            if i + 1 >= len(argv):
                print("ERROR: --visual-theme requires a value", file=__import__("sys").stderr)
                return 2
            visual_theme = argv[i + 1]
            i += 2
        elif not argv[i].startswith("--"):
            out = Path(argv[i])
            i += 1
        else:
            print(f"ERROR: unknown option: {argv[i]}", file=__import__("sys").stderr)
            return 2
    specs = derive_specs(md, visual_theme=visual_theme)
    write_specs(specs, out)
    print(f"wrote {len(specs)} specs -> {out}")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(cli(sys.argv))
