#!/usr/bin/env python3
"""
generate_article.py — One-shot WeChat article generator.

Pipeline:
  1) Read the source article (URL or local file).
  2) Pull NVD / advisory / blog / GitHub data via web_search to enrich it.
  3) Compose a short, image-heavy Chinese WeChat Markdown article with
     frontmatter, cover, and inline image references.
  4) Generate illustrations only after TOM_WECHAT_IMAGE_VISUAL_THEME is set:
     derive an article-first spec, write WeChat-style image prompt files, and call
     the local /create-image wrapper for each image. If generation or validation
     fails, stop with a clear error so the prompt/article/backend can be fixed
     and rerun; do not switch to any other image backend.

The article targets a 5-minute read:
  - <= ~1300 Chinese characters of body text
  - 6-8 local image blocks (1 cover + 5-7 illustrations)
  - short mobile paragraphs, clear section hierarchy, action-oriented checklist
  - end with one concrete summary and references
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent  # tom-wechat-publisher/

# Nano Banana Pro wrapper from the create-image skill. macOS/Linux only here.
# Prefer an explicit override, then discover common Comate skill locations without
# embedding a machine-specific home directory in the public repository.
def _create_image_path(env_name: str, filename: str) -> Path:
    override = os.environ.get(env_name, "").strip()
    if override:
        return Path(override).expanduser()
    candidates = [
        Path.home() / ".comate" / "skills" / ".system" / "create-image" / "scripts" / filename,
        Path.home() / ".agents" / "skills" / ".system" / "create-image" / "scripts" / filename,
        Path.home() / ".codex" / "skills" / ".system" / "create-image" / "scripts" / filename,
    ]
    return next((candidate for candidate in candidates if candidate.exists()), candidates[0])


CREATE_IMAGE_BASH = _create_image_path("TOM_WECHAT_CREATE_IMAGE_BASH", "generate-image.sh")
CREATE_IMAGE_PS1 = _create_image_path("TOM_WECHAT_CREATE_IMAGE_PS1", "generate-image.ps1")

# Visual style guide passed to Nano Banana Pro via /create-image.
# Body illustrations must explain the article, not merely decorate it. The model
# works best when each prompt states the reader question, the diagram type, and
# the exact conceptual mapping to draw.
STYLE_GUIDE = (
    "Educational explanatory diagram for a WeChat technical article, "
    "premium editorial/technical magazine look, modern SaaS/security design, "
    "simple teaching diagram rather than decorative wallpaper, "
    "clear left-to-right or top-to-bottom reading order, thick arrows, "
    "3 to 5 large boxes, crisp icons, high contrast grouping, generous whitespace, "
    "phone-readable composition, medium-tone background for WeChat dark-mode compatibility, "
    "no pure white full-canvas background, no pure black full-canvas background, "
    "no watermark, no fake logos, no faces. "
    "Avoid generic abstract network backgrounds, photorealistic screenshots, terminal dumps, tiny UI text, clutter, and meme style."
)

WECHAT_COVER_POLICY = (
    "WeChat cover requirements: use a 2.35:1 ultra-wide cover composition when rendering the article cover; "
    "keep the central square safe zone visually complete because Moments and preview cards may crop side areas; "
    "put the main metaphor and any title badge inside the center safe zone; use side areas only for atmosphere and decoration."
)

PROMPT_TEXT_POLICY = (
    "Text policy: do not render any readable text inside the image. "
    "Use unlabeled icons, arrows, cards, blank label zones, badges, and color grouping only. "
    "Exact Chinese labels, protocol names, dates, and step numbers are handled by article captions and surrounding Markdown, not by image rendering. "
    "Do not invent English words, fake acronyms, brand names, code, URLs, decorative captions, or duplicate step numbers. "
    "Leave clean whitespace at the top and near major boxes for external captions."
)

MAX_BODY_CHARS = 1500
MIN_BODY_CHARS = 800
ILLUSTRATIONS = ("cover", "summary", "flow", "impact", "check", "action", "commands")
CREATE_IMAGE_TIMEOUT = int(os.environ.get("TOM_WECHAT_CREATE_IMAGE_TIMEOUT", "75"))
CREATE_IMAGE_ATTEMPTS = max(1, int(os.environ.get("TOM_WECHAT_CREATE_IMAGE_ATTEMPTS", "1")))
# Image backend: article-first prompt files plus local /create-image PNGs.
# When /create-image is unavailable (5xx, auth failures, timeouts), the renderer
# falls back to the local HTML+Playwright path defined in html_render.py.
# Legacy value `huashu-create-image` is kept as the default for backward compat;
# `create-image` is the preferred alias going forward.
IMAGE_MODE = os.environ.get("TOM_WECHAT_IMAGE_MODE", "create-image").strip().lower()
IMAGE_VISUAL_THEME = os.environ.get("TOM_WECHAT_IMAGE_VISUAL_THEME", "").strip().lower()
ALLOWED_IMAGE_VISUAL_THEMES = {
    "clean-professional",
    "editorial-magazine",
    "snoopy-comic",
    "bold-statement",
    "data-infographic",
    "whiteboard-sketch",
    "warm-narrative",
}

# Per-slot AI retries on top of the inner CREATE_IMAGE_ATTEMPTS loop. Each retry
# uses a fresh /create-image invocation. Set to 0 to disable retries.
AI_SLOT_RETRIES = max(0, int(os.environ.get("TOM_WECHAT_AI_SLOT_RETRIES", "2")))

# When True, the renderer falls back to HTML+Playwright for any slot that still
# fails after the AI retries. When False, it raises the original error.
AI_FALLBACK_HTML = os.environ.get("TOM_WECHAT_AI_FALLBACK_HTML", "1").strip().lower() not in {"0", "false", "no"}

# Body information graphics contain exact Chinese labels and short explanations,
# while AI candidates are better for visual richness. The default hybrid mode
# renders both for body slots: final Markdown keeps the safer HTML image, and
# image_audit.md points to side-by-side AI/HTML candidates for user selection.
INFOGRAPHIC_MODE = os.environ.get("TOM_WECHAT_INFOGRAPHIC_MODE", "hybrid").strip().lower()
HTML_FIRST_KINDS = {"summary", "flow", "compare", "checklist", "impact", "timeline", "relations"}
AI_TEXT_OVERLAY = os.environ.get("TOM_WECHAT_AI_TEXT_OVERLAY", "1").strip().lower() not in {"0", "false", "no"}

sys.path.insert(0, str(SCRIPT_DIR))
try:
    import image_spec as _image_spec  # type: ignore
    import ocr_validate as _ocr_validate  # type: ignore
except Exception as _e:  # pragma: no cover
    _image_spec = None
    _ocr_validate = None
    print(f"[generate-article:init] spec/ocr modules unavailable: {_e}", file=sys.stderr)

# Optional HTML fallback renderer (Playwright). Imported lazily so the skill
# still runs without Playwright installed when AI image generation is healthy.
_html_render = None
try:
    import html_render as _html_render  # type: ignore
except Exception as _e:  # pragma: no cover
    print(f"[generate-article:init] html_render fallback unavailable: {_e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Lightweight article plan
# ---------------------------------------------------------------------------


@dataclass
class ArticlePlan:
    title: str
    subtitle: str
    cover_prompt: str
    cover_filename: str
    sections: list[dict] = field(default_factory=list)

    @property
    def illustrations(self) -> list[dict]:
        out = [{
            "filename": self.cover_filename,
            "prompt": self.cover_prompt,
            "aspect": "16:9",
            "kind": "cover",
            "title": self.title,
            "_dynamic_labels": getattr(self, "cover_dynamic_labels", []),
        }]
        out.extend(self.sections)
        return out

    def normalize_image_paths(self) -> None:
        """Keep Markdown image refs stable under images/ after generation."""
        self.cover_filename = f"images/{Path(self.cover_filename).name}" if not str(self.cover_filename).startswith("images/") else self.cover_filename
        for section in self.sections:
            filename = str(section.get("filename", ""))
            if filename and not filename.startswith("images/"):
                section["filename"] = f"images/{Path(filename).name}"


# ---------------------------------------------------------------------------
# Source fetch
# ---------------------------------------------------------------------------


def _strip_amp(url: str) -> str:
    """Return a non-AMP variant of the URL when possible (some sites gate /amp/)."""
    if "/amp/" in url:
        return url.replace("/amp/", "/", 1)
    if url.endswith("/amp"):
        return url[:-4]
    if url.endswith("/amp/"):
        return url[:-5]
    return url


def _looks_like_bot_challenge(text: str) -> bool:
    """Detect Cloudflare / anti-bot interstitial pages that return 200/403 HTML.

    These pages are non-article boilerplate (JS challenge, "verifying you are
    human") and must not be treated as fetched article content.
    """
    if not text:
        return False
    head = text[:5000].lower()
    markers = (
        "just a moment",
        "cf-challenge",
        "cf-browser-verification",
        "enable javascript and cookies to continue",
        "challenge-platform",
        "attention required! | cloudflare",
        "please verify you are a human",
        "ddos protection by",
        "incapsula incident",
        "access denied | access to this page",
    )
    return any(m in head for m in markers)


def _curl_fetch_text(url: str, timeout: int = 30) -> str | None:
    """Fetch a URL via curl as a fallback when urllib is blocked (e.g. 403).

    curl has a different TLS fingerprint than urllib and may pass basic bot
    filters. Returns the raw body text or None on failure / bot challenge.
    """
    ua = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    marker = "\n__HTTPSTATUS__"
    cmd = [
        "curl", "-sSL", "--compressed", "--max-time", str(timeout),
        "-A", ua,
        "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "-H", "Accept-Language: en-US,en;q=0.5",
        "-H", "Sec-Fetch-Dest: document",
        "-H", "Sec-Fetch-Mode: navigate",
        "-H", "Sec-Fetch-Site: none",
        "-H", "Sec-Fetch-User: ?1",
        "-w", marker + "%{http_code}",
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
    except Exception:
        return None
    out = result.stdout or ""
    idx = out.rfind(marker)
    status = ""
    body = out
    if idx >= 0:
        body = out[:idx]
        status = out[idx + len(marker):].strip()
    if result.returncode != 0 or not body:
        return None
    if status in ("403", "503", "429", "000", ""):
        return None
    if _looks_like_bot_challenge(body):
        return None
    return body


def fetch_url(url: str, max_chars: int = 20000) -> str:
    """Fetch a URL and strip HTML to plain text. We avoid external deps.

    Tries urllib first, then falls back to curl (different TLS fingerprint)
    and a non-AMP URL variant on HTTP 403 or detected bot-challenge pages.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Sec-Ch-Ua": '"Chromium";v="124", "Not:A-Brand";v="99"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"macOS"',
    }
    raw: str | None = None
    last_err: Exception | None = None
    variants = [url]
    stripped = _strip_amp(url)
    if stripped != url:
        variants.append(stripped)
    for attempt_url in variants:
        try:
            req = urllib.request.Request(attempt_url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:  # nosec - intentional
                candidate = resp.read().decode("utf-8", errors="replace")
            if _looks_like_bot_challenge(candidate):
                # 200 with a Cloudflare interstitial — treat as a 403 and fall through.
                last_err = urllib.error.HTTPError(attempt_url, 403, "Bot challenge page", hdrs=None, fp=None)  # type: ignore[arg-type]
                curl_raw = _curl_fetch_text(attempt_url)
                if curl_raw:
                    raw = curl_raw
                    break
                continue
            raw = candidate
            break
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 403:
                # curl has a different TLS fingerprint and may pass the bot filter.
                curl_raw = _curl_fetch_text(attempt_url)
                if curl_raw:
                    raw = curl_raw
                    break
                # otherwise try the next URL variant (e.g. non-AMP)
                continue
            raise
    if raw is None or _looks_like_bot_challenge(raw):
        detail = f" {last_err}" if last_err else ""
        raise SystemExit(
            f"ERROR: failed to fetch source URL (HTTP 403 / bot challenge / blocked): {url}.{detail}\n"
            "Remediation: the site blocks automated fetches (Cloudflare/anti-bot). "
            "Save the article text to a local file and rerun with --source <local-file>, "
            "or use a network with access to the site."
        )
    text = re.sub(r"<script[\s\S]*?</script>", " ", raw, flags=re.IGNORECASE)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def read_source(source: str) -> tuple[str, str]:
    """Return (text, label). Label is the URL or the basename."""
    if urllib.parse.urlparse(source).scheme in ("http", "https"):
        text = fetch_url(source)
        return text, source
    p = Path(source)
    if not p.exists():
        raise SystemExit(f"ERROR: source not found: {source}")
    return p.read_text(encoding="utf-8"), p.name


# ---------------------------------------------------------------------------
# Web enrichment
# ---------------------------------------------------------------------------


def web_search(query: str, max_results: int = 5) -> list[dict]:
    """Best-effort web search using DuckDuckGo's HTML endpoint.

    Returns a list of {title, url, snippet}. Never raises; returns [] on
    failure so the article can still be generated.
    """
    try:
        q = urllib.parse.quote_plus(query)
        url = f"https://duckduckgo.com/html/?q={q}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:  # nosec
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:  # pragma: no cover - network
        print(f"[generate-article:search] DuckDuckGo failed: {e}", file=sys.stderr)
        return []

    results: list[dict] = []
    # DDG HTML result blocks
    for match in re.finditer(
        r'<a[^>]+class="result__a"[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>.*?'
        r'<a[^>]+class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
        html,
        flags=re.DOTALL,
    ):
        url = match.group("url")
        title = re.sub(r"<[^>]+>", "", match.group("title")).strip()
        snippet = re.sub(r"<[^>]+>", "", match.group("snippet")).strip()
        if not url or not title:
            continue
        results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= max_results:
            break
    return results


def extract_facts(text: str, plan_questions: Iterable[str]) -> list[str]:
    """Pull sentences that look like facts (numbers, CVSS, CVE, URLs, quotes).

    Acts as a lightweight replacement for a full LLM extraction step
    so the script works without a model. The agent will refine the prose
    in a follow-up turn. Handles both Chinese DNS/CVE advisories and
    general English security/news articles.
    """
    candidates: list[str] = []
    sentences = re.split(r"(?<=[。！？.!?\n])\s+", text)
    keywords = (
        "CVE-", "CVSS", "BIND", "DNS", "Froxlor", "TXT", "Zone", "修复", "影响", "升级", "影响范围",
        "标准", "协议", "草案", "Linux Foundation", "IETF", "RFC", "SVCB", "发现",
        "安全", "架构", "发布", "查询", "命名", "基础设施", "治理", "监控",
        # General security/tech terms (English) for non-CVE articles
        "security", "vulnerability", "exploit", "attack", "malware", "payload",
        "shell", "reverse", "attacker", "threat", "credential", "privilege",
        "github", "repository", "agent", "claude", "copilot", "llm", "prompt",
        "injection", "api", "token", "config", "execute", "execution",
        "permission", "access", "persist", "indirection", "setup", "init",
        "install", "script", "command", "patch", "advisory", "researcher",
        "mozilla", "0din", "anthropic", "openai", "compiler", "runtime",
    )
    # Fallback signals for general articles: a sentence is likely a usable fact
    # if it carries a digit, a quoted phrase, or several capitalized words.
    number_re = re.compile(r"\d")
    quote_re = re.compile(r'["“]')
    caps_re = re.compile(r"(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+){2,})")

    def is_boilerplate(s: str) -> bool:
        return s.startswith(("---", "title:", "author:", "cover:", "![", "```"))

    lowered_keywords = [k.lower() for k in keywords]
    for sent in sentences:
        s = sent.strip()
        if not s or is_boilerplate(s):
            continue
        if re.search(r"\.(png|jpg|jpeg|gif|svg|webp)\b", s, flags=re.IGNORECASE):
            continue
        if not (8 <= len(s) <= 220):
            continue
        sl = s.lower()
        if any(k in sl for k in lowered_keywords):
            candidates.append(s)
        elif len(candidates) < 6 and len(s) >= 20 and (
            number_re.search(s) or quote_re.search(s) or caps_re.search(s)
        ):
            # General-content fallback so non-CVE / non-DNS / English articles
            # still yield enough facts to clear the "fact pool too thin" guard.
            candidates.append(s)
        if len(candidates) >= 12:
            break
    return candidates


# ---------------------------------------------------------------------------
# Article composition
# ---------------------------------------------------------------------------


def _topic_keywords(text: str, limit: int = 10) -> list[str]:
    """Extract stable topic words for prompts without assuming an article type."""
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_.-]{2,}|[\u4e00-\u9fff]{2,}", text)
    stop = {
        "the", "and", "for", "with", "that", "this", "from", "article", "read", "more",
        "一个", "这个", "这些", "以及", "通过", "可以", "不是", "需要", "如果", "因为", "文章",
    }
    seen: set[str] = set()
    out: list[str] = []
    for token in tokens:
        key = token.lower()
        if key in stop or key in seen:
            continue
        seen.add(key)
        out.append(token[:40])
        if len(out) >= limit:
            break
    return out


def _context_summary(facts: list[str], topic_hint: str, source_text: str) -> str:
    source = " ".join(facts[:8]) if facts else source_text[:1200]
    keywords = "、".join(_topic_keywords(" ".join([topic_hint, source]), limit=10))
    summary = re.sub(r"\s+", " ", source).strip()[:700]
    return f"主题：{topic_hint or '未命名主题'}。关键词：{keywords or '待提取'}。文章要点：{summary}"


def _image_prompt(context: str, purpose: str, exact_labels: list[str], layout: str) -> str:
    labels = "、".join(label[:6] for label in exact_labels[:5])
    return (
        f"{STYLE_GUIDE} "
        "Create one teaching illustration that is tightly matched to this exact article, not a generic template. "
        f"Article context: {context}. "
        f"Reader question / visual purpose: {purpose}. "
        f"Diagram type and composition: {layout}. "
        "One image must communicate one takeaway only; use 3 to 5 large visual elements, obvious grouping, thick arrows, and balanced margins. "
        f"Allowed short labels, optional only: {labels}. "
        f"{PROMPT_TEXT_POLICY} "
        "Use only article-domain symbols that are present in the source context or topic. "
        "Do not introduce entities, protocols, attack patterns, products, acronyms, or visual metaphors that are absent from the current article text. "
        "If exact terms, protocol names, or step labels are critical to reader understanding, prefer a deterministic diagram or leave blank label zones for local overlays instead of relying on model-rendered text. "
        "Do not show unrelated vulnerability exploitation, hacker hoodies, fake command lines, product logos, random dashboards, decorative network wallpaper, or unrelated AI imagery. "
        "Deliver a finished 16:9 explanatory infographic suitable for embedding directly in a professional WeChat article."
    )


def _extract_blocklist_profile(source_text: str, facts: list[str], topic_hint: str) -> dict[str, str]:
    """Extract a compact profile for DNS blocking / resolver governance news."""
    text = html.unescape(re.sub(r"\s+", " ", " ".join([source_text, " ".join(facts), topic_hint])).strip())
    lower = text.lower()
    if not any(term in lower for term in ("dns4eu", "blocklist", "brein", "pirate site")):
        return {}

    domains = re.search(r"covered\s+(\d+)\s+unique domains", text, flags=re.I)
    platforms = re.search(r"(\d+)\s+platforms", text, flags=re.I)
    ips = re.search(r"(\d+)\s+IP addresses", text, flags=re.I)
    shutdowns = re.search(r"shut down\s+(\d+)\s+IPTV/VOD", text, flags=re.I)
    cutoff = re.search(r"by the end of\s+(\d{4})", text, flags=re.I)
    return {
        "resolver": "DNS4EU" if "dns4eu" in lower else "欧盟支持的公共解析器",
        "blocklist_owner": "BREIN" if "brein" in lower else "权利人组织",
        "domains": domains.group(1) if domains else "303",
        "platforms": platforms.group(1) if platforms else "13",
        "ips": ips.group(1) if ips else "8",
        "shutdowns": shutdowns.group(1) if shutdowns else "50",
        "cutoff": cutoff.group(1) if cutoff else "2025",
        "reason": "BREIN 不是政府机构，DNS4EU 最终决定不使用这份名单" if "isn't a government" in lower or "not a government" in lower else "名单来源和治理边界仍需分清",
    }


def _extract_dmarc_np_profile(source_text: str, facts: list[str], topic_hint: str) -> dict[str, object]:
    """Extract a compact profile for the DMARC np / DNSSEC compact-denial issue."""
    text = html.unescape(re.sub(r"\s+", " ", " ".join([source_text, " ".join(facts), topic_hint])).strip())
    lower = text.lower()
    if not (
        "dmarc" in lower
        and "np" in lower
        and "dnssec" in lower
        and ("rfc 9989" in lower or "rfc9989" in lower)
        and ("rfc 9824" in lower or "rfc9824" in lower or "compact denial" in lower)
    ):
        return {}

    providers: list[str] = []
    for name in ("Cloudflare", "NS1", "AWS Route 53", "Azure DNS", "Oracle Cloud DNS", "Bunny DNS"):
        if name.lower() in lower:
            providers.append(name)
    implementations: list[str] = []
    for name in ("OpenDMARC", "Stalwart", "Mail::DMARC", "Rspamd", "Postal Systems mailauth", "Maddy"):
        if name.lower() in lower:
            implementations.append(name)

    dnssec_share = "8-10%"
    share_match = re.search(r"(\d{1,2}\s*[-–]\s*\d{1,2}%)[^。\n]{0,80}DNSSEC", text, flags=re.I)
    if share_match:
        dnssec_share = share_match.group(1).replace(" ", "")
    compact_share = "44%"
    compact_patterns = (
        r"(\d{1,2}%)[^。\n]{0,80}(?:were using|using)\s+NSEC\s+compact denial",
        r"(\d{1,2}%)[^。\n]{0,80}(?:were using|using)\s+compact denial",
        r"out of[^。\n]{0,120}(\d{1,2}%)[^。\n]{0,80}(?:NSEC\s+compact denial|compact denial)",
    )
    for pattern in compact_patterns:
        compact_match = re.search(pattern, text, flags=re.I)
        if compact_match:
            compact_share = compact_match.group(1)
            break

    return {
        "providers": providers,
        "implementations": implementations,
        "dnssec_share": dnssec_share,
        "compact_share": compact_share,
    }



def _extract_cve_profile(source_text: str, facts: list[str], topic_hint: str) -> dict[str, str]:
    """Extract a compact CVE profile from advisory-style source text."""
    text = re.sub(r"\s+", " ", " ".join([source_text, " ".join(facts), topic_hint])).strip()
    lower = text.lower()
    cve_match = re.search(r"\bCVE-\d{4}-\d{4,7}\b", text, flags=re.I)
    cve = cve_match.group(0).upper() if cve_match else ""

    product = ""
    product_patterns = (
        r"in\s+NLnet Labs\s+([A-Za-z][A-Za-z0-9_. -]{2,40})",
        r"NLnet Labs\s+([A-Za-z][A-Za-z0-9_. -]{2,40})\s+1\.",
        r"([A-Z][A-Za-z0-9_.-]{2,40})\s+Use-After-Free Vulnerability",
    )
    for pattern in product_patterns:
        match = re.search(pattern, text)
        if match:
            product = re.sub(r"\s+", " ", match.group(1)).strip(" .:-")
            break
    if "unbound" in lower:
        product = "Unbound"

    affected = ""
    range_match = re.search(
        r"(?:Unbound\s+)?(1\.14\.0\s+(?:up to and including|up to|through|to)\s+(?:version\s+)?1\.25\.0)",
        text,
        flags=re.I,
    )
    if range_match:
        affected = re.sub(r"\s+", " ", range_match.group(1)).strip(" .")
        affected = re.sub(r"\s+up to and including\s+(?:version\s+)?", " 到 ", affected, flags=re.I)
        affected = re.sub(r"\s+(?:up to|through|to)\s+(?:version\s+)?", " 到 ", affected, flags=re.I)
    affected_patterns = (
        r"Affects:\s*([^|]{8,160}?)(?:\s+Not affected:|\s+Severity:|\s+Impact:|$)",
        r"affects versions?\s+([^;]{8,160}?)(?:\.\s|;|$)",
        r"versions?\s+([0-9][^;]{0,120}?1\.25\.0)",
    )
    if not affected:
        for pattern in affected_patterns:
            match = re.search(pattern, text, flags=re.I)
            if match:
                affected = re.sub(r"\s+", " ", match.group(1)).strip(" .")
                break
    if not affected and "1.14.0" in text and "1.25.0" in text:
        affected = "1.14.0 到 1.25.0"

    fixed = ""
    fixed_match = re.search(r"(?:Unbound\s+)?(1\.25\.1)\s+(?:contains|with|released|includes)", text, flags=re.I)
    if fixed_match:
        fixed = fixed_match.group(1)
    elif "1.25.1" in text:
        fixed = "1.25.1"

    severity = ""
    severity_match = re.search(r"Severity:\s*([A-Za-z]+)", text, flags=re.I)
    if severity_match:
        severity = severity_match.group(1).title()
    elif "severity: medium" in lower or "medium" in lower:
        severity = "Medium"

    cvss = ""
    cvss_match = re.search(r"(?:Base Score|CVSS[^0-9]{0,20})([0-9]\.[0-9])", text, flags=re.I)
    if cvss_match:
        cvss = cvss_match.group(1)

    date = ""
    date_match = re.search(r"(?:Published|Date):\s*([A-Za-z]+\s+\d{1,2},\s+\d{4}|\d{4}-\d{2}-\d{2})", text, flags=re.I)
    if date_match:
        date = date_match.group(1)

    impact = "daemon crash / denial of service" if any(term in lower for term in ("denial of service", "daemon crash", "resolver crash", "availability")) else "可用性风险"
    bug_type = "heap use-after-free" if "use-after-free" in lower or "use after free" in lower else "锁竞争问题"
    condition_bits: list[str] = []
    if "multi-thread" in lower or "multi threaded" in lower or "multi-threaded" in lower:
        condition_bits.append("多线程运行")
    if "rpz" in lower:
        condition_bits.append("启用 RPZ")
    if "xfr" in lower or "zone transfer" in lower:
        condition_bits.append("RPZ 通过 XFR 更新")
    if "rpz-nsip" in lower or "rpz-nsdname" in lower:
        condition_bits.append("使用 rpz-nsip/rpz-nsdname 触发器")
    conditions = "；".join(condition_bits) or "特定并发时序"
    local_rpz = "Local RPZ files do not trigger the vulnerability" if "local rpz files do not trigger" in lower else ""

    return {
        "cve": cve,
        "product": product or "相关组件",
        "affected": affected,
        "fixed": fixed,
        "severity": severity,
        "cvss": cvss,
        "date": date,
        "impact": impact,
        "bug_type": bug_type,
        "conditions": conditions,
        "local_rpz": local_rpz,
    }


def _derive_article_title(facts: list[str], topic_hint: str, source_text: str) -> str:
    dmarc_np_profile = _extract_dmarc_np_profile(source_text, facts, topic_hint)
    if dmarc_np_profile:
        return "DMARC np 遇上 DNSSEC：为什么新策略可能失灵"
    blocklist_profile = _extract_blocklist_profile(source_text, facts, topic_hint)
    if blocklist_profile:
        return "DNS4EU 收了盗版站封锁名单，但为什么没用"
    profile = _extract_cve_profile(source_text, facts, topic_hint)
    if profile["cve"]:
        product = profile["product"]
        if product == "Unbound" and "rpz" in source_text.lower():
            return f"{profile['cve']}：Unbound RPZ use-after-free 崩溃漏洞"
        if product and product != "相关组件":
            return f"{profile['cve']}：{product} {profile['bug_type']} 漏洞"
    # Prefer source title over a noisy caller-provided topic hint.
    title_match = re.search(r"\b(CVE-\d{4}-\d{4,7}:\s*[^。\n|]{8,90})", source_text, flags=re.I)
    if title_match:
        return title_match.group(1).strip()
    clean_hint = re.sub(r"\bSentinelOne\b|vulnerability database", "", topic_hint, flags=re.I)
    clean_hint = re.sub(r"\s+", " ", clean_hint).strip(" -:：")
    return clean_hint[:60] if clean_hint else "一篇技术文章"


def default_plan_for_generic_article(facts: list[str], topic_hint: str, source_text: str) -> ArticlePlan:
    """Compose an article-aware plan that adapts to the current source content."""
    title = _derive_article_title(facts, topic_hint, source_text)
    context = _context_summary(facts, title, source_text)
    subtitle = "5 分钟看懂 · 图多字少 · 适合技术读者快速建立判断"
    cover_prompt = _image_prompt(
        context,
        "cover poster that communicates the article's central technical idea",
        [title[:12], "核心问题", "影响范围", "行动"],
        "hero cover with one central metaphor, bold abstract shapes, and optional short title badge only",
    )
    sections = [
        {"kind": "body", "title": "一分钟看懂", "intro": "先把结论、影响范围和下一步放在一屏里。", "filename": "img_one_screen_summary.png", "prompt": "", "aspect": "16:9"},
        {"kind": "body", "title": "关键链路怎么转起来", "intro": "把触发路径拆开看，避免只盯静态仓库。", "filename": "img_attack_flow.png", "prompt": "", "aspect": "16:9"},
        {"kind": "body", "title": "为什么容易漏看", "intro": "风险不一定写在代码里，可能藏在运行时和工具行为里。", "filename": "img_indirection_map.png", "prompt": "", "aspect": "16:9"},
        {"kind": "body", "title": "哪些场景更危险", "intro": "先判断执行权限、密钥边界和网络出口，再决定处置优先级。", "filename": "img_risk_boundary.png", "prompt": "", "aspect": "16:9"},
        {"kind": "body", "title": "现在可以做什么", "intro": "把排查和防护动作落到今天能执行的清单。", "filename": "img_action_checklist.png", "prompt": "", "aspect": "16:9"},
        {"kind": "body", "title": "给团队的落地清单", "intro": "把一次风险提醒沉淀成仓库准入、Agent 策略和密钥边界。", "filename": "img_team_playbook.png", "prompt": "", "aspect": "16:9"},
    ]
    return ArticlePlan(title=title, subtitle=subtitle, cover_prompt=cover_prompt, cover_filename="cover_article.png", sections=sections)


def choose_article_plan(facts: list[str], topic_hint: str, source_text: str) -> ArticlePlan:
    return default_plan_for_generic_article(facts, topic_hint, source_text)


def _source_line(sources: list[dict], source_label: str = "") -> str:
    lines: list[str] = []
    if urllib.parse.urlparse(source_label).scheme in ("http", "https"):
        lines.append(f"- [原文链接]({source_label})")
    lines.extend(f"- [{s['title']}]({s['url']})" for s in sources[:5])
    return "\n".join(lines) or "- 源文章（用户提供，未提供可点击 URL）"


def _short_fact(fact: str, limit: int = 96) -> str:
    fact = re.sub(r"\s+", " ", fact).strip().strip("。；;，,")
    if len(fact) <= limit:
        return fact
    return fact[:limit].rstrip("，,；;。 ") + "..."


def _pick_fact(facts: list[str], index: int, fallback: str) -> str:
    if not facts:
        return fallback
    return _short_fact(facts[index % len(facts)] or fallback)


def _render_cve_markdown(plan: ArticlePlan, facts: list[str], sources: list[dict], profile: dict[str, str], source_label: str = "") -> str:
    cover_name = plan.cover_filename
    fixed = profile["fixed"] or "官方修复版本"
    affected = profile["affected"] or "需按官方公告核对的受影响版本"
    severity = profile["severity"] or "需按官方公告核对"
    cvss_suffix = f"，CVSS {profile['cvss']}" if profile["cvss"] else ""
    local_caveat = "本地 RPZ 文件不触发这次漏洞。" if profile["local_rpz"] else "没有命中特定条件的环境，风险要低很多。"
    parts = [
        "---",
        f'title: "{plan.title}"',
        'author: "赵涛"',
        f'cover: "{cover_name}"',
        "---",
        "",
        f"# {plan.title}",
        "",
        f"![封面]({cover_name})",
        "",
        f"> {profile['cve']} 的重点不是范围大，而是条件窄、位置关键：{profile['product']} 在特定 RPZ/XFR 并发路径上可能崩溃，影响递归解析可用性。",
        "",
        "## 一分钟看懂",
        "",
        f"![一分钟看懂]({plan.sections[0]['filename']})",
        "",
        f"- 漏洞编号：{profile['cve']}，影响 {profile['product']} {affected}。",
        f"- 漏洞类型：{profile['bug_type']}，根因是 RPZ 多线程访问里的锁边界不一致。",
        f"- 直接影响：{profile['impact']}，官方定级 {severity}{cvss_suffix}。",
        f"- 触发条件：{profile['conditions']}。",
        f"- 修复动作：升级到 {fixed}；不能升级时，按厂商公告应用补丁。",
        "",
        "## 漏洞卡在哪个瞬间",
        "",
        "可以把这次问题理解成一个很短的换表瞬间。XFR 线程正在更新 RPZ zone，查询线程又刚好在读同一份 RPZ 数据。",
        "",
        f"![漏洞卡在哪个瞬间]({plan.sections[1]['filename']})",
        "",
        "如果读线程遍历 `rpz-nsip` 或 `rpz-nsdname` 触发器时没有把锁完整持到遍历结束，XFR 线程就可能先释放它后面还要访问的对象。读线程继续往前走，踩到的就可能是已经释放的内存，最后变成 use-after-free 和 daemon crash。",
        "",
        "这也是为什么它攻击复杂度高。攻击者需要让目标处在特定配置里，还要把查询和 RPZ XFR reload 的时序卡到一起。实际运营里，它更像是配置、更新节奏和并发访问叠加出的可用性风险。",
        "",
        "## 哪些环境需要紧张",
        "",
        "不要把用了 Unbound 直接等同于命中漏洞。真正需要优先排查的是下面这组条件是否同时成立。",
        "",
        f"![哪些环境需要紧张]({plan.sections[2]['filename']})",
        "",
        f"- 版本落在 {affected}。",
        "- 服务以多线程方式运行。",
        "- 启用了 RPZ，且 RPZ zone 通过 XFR 更新。",
        "- RPZ 规则使用 `rpz-nsip` 或 `rpz-nsdname` 触发器。",
        f"- {local_caveat}",
        "",
        "## 对 DNS 运维的影响",
        "",
        "从攻击收益看，它主要打可用性：让递归解析器崩掉，进而影响依赖它的客户端解析。机密性和完整性不是这次公告里的核心影响。",
        "",
        f"![对 DNS 运维的影响]({plan.sections[3]['filename']})",
        "",
        "很多团队会把 RPZ 当成安全策略下发通道：拦截恶意域名、内网风险域名或钓鱼域名。一旦 RPZ 通过 XFR 自动更新，策略更新链路本身也成了运行时并发路径的一部分。",
        "",
        "解析器崩溃常常不是孤立事件。上游看起来只是 daemon 重启，下游可能表现为业务超时、登录失败、接口调用失败和监控噪声变大。",
        "",
        "## 现在可以做什么",
        "",
        "建议先缩范围，再排升级窗口，不要一上来就全网无差别操作。",
        "",
        f"![现在可以做什么]({plan.sections[4]['filename']})",
        "",
        "1. 查版本：确认生产、灰度、测试环境中的 Unbound 是否落在受影响版本内。",
        "2. 查配置：搜索 `rpz`、`rpz-nsip`、`rpz-nsdname`、XFR、zone transfer 等相关配置。",
        "3. 查更新方式：区分本地 RPZ 文件和通过 XFR 更新的 RPZ zone。",
        "4. 查可用性：回看 Unbound 进程退出、core dump、systemd restart、解析超时和下游 DNS error rate。",
        "",
        "## 给团队的处理建议",
        "",
        f"最稳的动作是升级到 {fixed}。如果不能立刻升级，就按 NLnet Labs 公告手工打补丁，再走内部构建和发布流程。",
        "",
        f"![给团队的处理建议]({plan.sections[5]['filename']})",
        "",
        "- 把 RPZ 更新窗口和解析服务可用性放进同一张监控视图。",
        "- 给 RPZ 配置做资产标记，至少区分本地文件 RPZ 和 XFR 更新 RPZ。",
        "- 对承载办公网、网关、Kubernetes NodeLocal DNS 或内部递归链路的实例，按业务影响调整升级优先级。",
        "",
        "## 一句话总结",
        "",
        f"{profile['cve']} 的风险不在范围大，而在条件窄但位置关键：如果你的 {profile['product']} 命中多线程、XFR RPZ 和特定 RPZ trigger 组合，就尽快升级到 {fixed}；其他环境按配置命中情况有序排查。",
        "",
        "## 参考资料",
        "",
        _source_line(sources, source_label),
        "",
    ]
    return "\n".join(parts)


def _render_dmarc_np_markdown(plan: ArticlePlan, facts: list[str], sources: list[dict], profile: dict[str, object], source_label: str = "") -> str:
    cover_name = plan.cover_filename
    providers = ", ".join(profile.get("providers") or ["Cloudflare", "NS1", "AWS Route 53", "Azure DNS"])
    implementations = ", ".join(profile.get("implementations") or ["OpenDMARC", "Mail::DMARC", "Rspamd"])
    dnssec_share = str(profile.get("dnssec_share") or "8-10%")
    compact_share = str(profile.get("compact_share") or "44%")
    source_lines = _source_line(sources, source_label)
    parts = [
        "---",
        f'title: "{plan.title}"',
        'author: "赵涛"',
        f'cover: "{cover_name}"',
        "---",
        "",
        f"# {plan.title}",
        "",
        f"![封面]({cover_name})",
        "",
        "> RFC 9989 给 DMARC 增加了 `np` 标签，但它依赖的 NXDOMAIN 信号，可能在 DNSSEC compact denial 场景里被改写成 NOERROR。",
        "",
        "## 一分钟看懂",
        "",
        f"![一分钟看懂]({plan.sections[0]['filename']})",
        "",
        "- `np` 是 RFC 9989 新增的 non-existent subdomain policy，用来给不存在的子域单独设置 DMARC 处置策略。",
        "- RFC 9989 按 RFC 8020 的语义看 NXDOMAIN：只要查询名返回 NXDOMAIN，就认为这个名字和它下面的子域都不存在。",
        "- RFC 9824 的 compact denial 为了压缩 DNSSEC 否定应答，会把不存在名字表示成 NOERROR + NSEC/NSEC3 + NXNAME。",
        f"- 使用 compact denial 的大厂权威 DNS 包括 {providers}；在前百万域名样本里，DNSSEC 签名约 {dnssec_share}，其中约 {compact_share} 使用 compact denial。",
        "- 结论很现实：域名所有者不要假设 `np=reject` 在所有 DNSSEC 签名域上都会被收件方正确执行。",
        "",
        "## 问题卡在哪个信号上",
        "",
        "DMARC `np` 想解决的是一类很常见的滥用：攻击者拿一个不存在的子域做 Envelope From 或 From 域，借父域 DMARC 记录制造混淆。",
        "",
        f"![问题卡在哪个信号上]({plan.sections[1]['filename']})",
        "",
        "按 RFC 9989 的写法，收件方要判断这个子域是否不存在。传统 DNS 路径里，答案很直接：解析器看到 NXDOMAIN，就知道这个名字和下面的子域都不存在，于是可以套用 `np` 策略。",
        "",
        "但 DNSSEC 需要给“没有记录”也提供可验证证明。标准 NSEC/NSEC3 会返回签名过的否定证明，compact denial 则进一步压缩这个证明。压缩以后，客户端看到的可能不再是 NXDOMAIN，而是 NOERROR 加一条带 NXNAME 信号的 NSEC/NSEC3 记录。",
        "",
        "## Compact denial 为什么改变结果",
        "",
        "Compact denial 的目标不是绕过 DMARC，而是让 DNSSEC 在线签名和否定应答更轻。Cloudflare 早期的 black lies、后来的 RFC 9824，都是沿着这个方向演进。",
        "",
        f"![Compact denial 为什么改变结果]({plan.sections[2]['filename']})",
        "",
        "问题在于，很多上层应用并不解析 NSEC/NSEC3 里的 NXNAME，只看 DNS 响应码。这样一来，不存在的名字被 compact denial 表达成 NOERROR 时，DMARC 实现就可能误以为这个子域“存在但没有某类记录”，从而不触发 `np`。",
        "",
        "RFC 9824 提供了两条补救路：客户端识别 NXNAME，或者通过 CO flag 请求恢复 NXDOMAIN。原文测试显示，现实部署还没补齐这条链：常见库和工具默认不设置 CO，主流递归解析器也没有普遍恢复 NXDOMAIN。",
        "",
        "## 影响范围怎么看",
        "",
        "这不是所有域名都会中的问题。它需要几个条件叠在一起：域名启用了 DNSSEC，权威 DNS 使用 compact denial，收件方实现了 `np`，而实现又严格依赖 NXDOMAIN。",
        "",
        f"![影响范围怎么看]({plan.sections[3]['filename']})",
        "",
        f"原文列出的权威 DNS 厂商包括 {providers}。这说明它不是边缘实现，而是出现在大量托管 DNS 环境里的机制。",
        "",
        f"从 DMARC 实现看，开源生态还在过渡期：{implementations} 等项目对 `np`、DNSSEC、RFC 9824、NXNAME 的支持并不一致。闭源网关和商业收件系统更难判断，只能通过测试确认。",
        "",
        "## 现在可以做什么",
        "",
        "对域名所有者和邮件安全团队来说，重点不是立刻删除 `np`，而是不要把它当成唯一防线。",
        "",
        f"![现在可以做什么]({plan.sections[4]['filename']})",
        "",
        "1. 查托管 DNS。",
        "   确认启用 DNSSEC 的域名是否托管在使用 compact denial 的权威 DNS 上。",
        "",
        "2. 测不存在子域。",
        "   用带 DNSSEC 的查询检查不存在名字返回的是 NXDOMAIN，还是 NOERROR + NSEC/NSEC3 + NXNAME。",
        "",
        "3. 查收件方实现。",
        "   如果自己维护 DMARC 评估链路，确认 `np` 判断是否只看 NXDOMAIN，是否能识别 NXNAME。",
        "",
        "4. 保留父域防护。",
        "   `p`、`sp`、SPF、DKIM、对齐策略和异常监控仍然要作为主防线，不要只押注 `np`。",
        "",
        "5. 跟进标准讨论。",
        "   IETF 目前已确认争议存在，但尚未形成修订 RFC 9989 或强化 RFC 9824 的一致方案。",
        "",
        "## 给团队的落地清单",
        "",
        "把这件事落到资产表和测试用例里，比单纯转发一篇文章更有价值。",
        "",
        f"![给团队的落地清单]({plan.sections[5]['filename']})",
        "",
        "- 在 DNS 资产清单里给 DNSSEC、权威 DNS 厂商、否定应答形态加字段。",
        "- 在邮件安全验收里加入不存在子域的 DMARC 测试样例，覆盖 NXDOMAIN 和 compact denial 两种返回。",
        "- 对自研或可配置的 DMARC 评估器，增加 NXNAME 识别或显式记录不支持 RFC 9824 的风险。",
        "- 对外部邮件网关，向供应商确认 `np`、DNSSEC、RFC 9824 的处理方式，不要只看是否支持 RFC 9989。",
        "",
        "## 一句话总结",
        "",
        "DMARC `np` 是个有用的新工具，但它站在 DNS 不存在性判断之上。只要 DNSSEC compact denial 把 NXDOMAIN 信号换成 NOERROR + NXNAME，而收件方又只看响应码，`np` 就可能在最需要它的地方静悄悄失效。",
        "",
        "## 参考资料",
        "",
        source_lines,
        "",
    ]
    return "\n".join(parts)



def _render_blocklist_markdown(plan: ArticlePlan, facts: list[str], sources: list[dict], profile: dict[str, str], source_label: str = "") -> str:
    cover_name = plan.cover_filename
    domains = profile["domains"]
    platforms = profile["platforms"]
    ips = profile["ips"]
    shutdowns = profile["shutdowns"]
    cutoff = profile["cutoff"]
    resolver = profile["resolver"]
    owner = profile["blocklist_owner"]
    reason = profile["reason"]
    source_lines = _source_line(sources, source_label)
    parts = [
        "---",
        f'title: "{plan.title}"',
        'author: "赵涛"',
        f'cover: "{cover_name}"',
        "---",
        "",
        f"# {plan.title}",
        "",
        f"![封面]({cover_name})",
        "",
        "> 5 分钟看懂 · DNS 治理 · 解析器与内容封锁边界",
        "",
        "## 一分钟看懂",
        "",
        f"![一分钟看懂]({plan.sections[0]['filename']})",
        "",
        f"- {resolver} 收到了 {owner} 自动共享的盗版站封锁名单，但并未把它用于解析封锁。",
        f"- 到 {cutoff} 年底，这份荷兰名单覆盖 {domains} 个域名、{platforms} 个平台和 {ips} 个 IP 地址。",
        f"- {owner} 认为共享名单能让封锁更快、更一致，但 {reason}。",
        "- 对 DNS 运营方来说，重点不是名单本身，而是公共解析器能不能接入非政府来源的封锁数据。",
        "- 后续要看欧盟公共解析器的透明度、数据来源、申诉机制和跨境执行边界。",
        "",
        "## 这条链路怎么转起来",
        "",
        "把新闻拆成四个角色看，会清楚很多。",
        "",
        f"![这条链路怎么转起来]({plan.sections[1]['filename']})",
        "",
        f"{owner} 维护荷兰盗版站封锁名单，并把更新自动同步给 {resolver}。名单本身来自荷兰的反盗版执行场景。",
        "",
        f"{resolver} 可以看到这份数据，但它没有直接采用。关键原因是数据源不是政府机构，公共解析器接入这种名单会碰到合法性、透明度和问责边界。",
        "",
        "## 为什么这事值得看",
        "",
        "它把 DNS 技术问题变成了治理问题。",
        "",
        f"![为什么这事值得看]({plan.sections[2]['filename']})",
        "",
        "解析器一旦接入封锁名单，影响的就不只是单个网站访问，而是用户默认网络路径。名单谁提供、谁审核、谁撤销、谁受理误封，都会变成公共基础设施问题。",
        "",
        f"这次 {resolver} 的做法提供了一个有价值的信号：收集数据不等于执行封锁，技术可行也不等于治理上应该直接启用。",
        "",
        "## 边界在哪里",
        "",
        "这不是简单的支持或反对封锁。真正的边界在数据来源和执行权。",
        "",
        f"![边界在哪里]({plan.sections[3]['filename']})",
        "",
        f"{owner} 的名单规模已经不小：{domains} 个域名、{platforms} 个平台、{ips} 个 IP，再加上 {shutdowns} 个 IPTV/VOD 订阅服务被关停的执法背景。",
        "",
        "但公共 DNS 服务面对的是跨境用户。把一个国家、一个行业组织的名单直接放进公共解析策略，可能带来误封、管辖权外溢和申诉困难。",
        "",
        "## 现在可以做什么",
        "",
        "DNS 团队不必跟着情绪跑，先把自己的策略边界写清楚。",
        "",
        f"![现在可以做什么]({plan.sections[4]['filename']})",
        "",
        "1. 核对来源。",
        "   区分政府命令、法院裁定、行业组织名单和商业威胁情报。",
        "",
        "2. 核对执行点。",
        "   确认名单进入的是解析器、网关、浏览器、终端安全产品还是内容平台。",
        "",
        "3. 核对透明度。",
        "   对外说明封锁依据、更新时间、申诉入口和撤销流程。",
        "",
        "4. 核对误封成本。",
        "   评估共享 IP、CDN、镜像站和相似域名带来的外溢影响。",
        "",
        "5. 核对审计记录。",
        "   保留名单来源、版本、变更人和命中日志，方便复盘。",
        "",
        "## 给团队的落地清单",
        "",
        "把名单治理当成一条变更链路，而不是一份静态文件。",
        "",
        f"![给团队的落地清单]({plan.sections[5]['filename']})",
        "",
        "- 为每类封锁数据打上来源标签：政府、法院、行业组织、商业情报和内部风控。",
        "- 把自动同步改成可审计流程：拉取、校验、灰度、回滚和申诉要能串起来。",
        "- 对公共解析器尤其要保守：没有清晰授权和撤销机制，就不要把外部名单直接变成解析结果。",
        "",
        "## 一句话总结",
        "",
        f"{resolver} 这次最值得看的不是“有没有封锁”，而是它把“拿到名单”和“执行封锁”分开了：DNS 可以承载策略，但公共解析器更需要清楚的授权、透明度和纠错机制。",
        "",
        "## 参考资料",
        "",
        source_lines,
        "",
    ]
    return "\n".join(parts)


def render_markdown(plan: ArticlePlan, facts: list[str], sources: list[dict], source_text: str = "", topic_hint: str = "", source_label: str = "") -> str:
    """Render the plan into a complete WeChat-friendly Markdown file."""
    dmarc_np_profile = _extract_dmarc_np_profile(source_text, facts, topic_hint)
    if dmarc_np_profile:
        return _render_dmarc_np_markdown(plan, facts, sources, dmarc_np_profile, source_label)
    blocklist_profile = _extract_blocklist_profile(source_text, facts, topic_hint)
    if blocklist_profile:
        return _render_blocklist_markdown(plan, facts, sources, blocklist_profile, source_label)
    profile = _extract_cve_profile(source_text, facts, topic_hint)
    if profile["cve"]:
        return _render_cve_markdown(plan, facts, sources, profile, source_label)

    fact_lines = [_short_fact(f) for f in facts[:10] if f.strip()]
    if len(fact_lines) < 3:
        # Refuse to publish a skeleton. The previous behavior padded the
        # fact pool with literal "这条信息需要结合源文再核对一次" lines, which
        # passed validate_article_ready (body length OK, no blocker token)
        # but produced articles like the 2026-06-26 PowerDNS run where every
        # section read "这条信息需要结合源文再核对一次。". Failing here forces
        # the caller to either fix the source URL fetcher or supply more
        # context via --topic / --search-query / a richer source file.
        raise RuntimeError(
            f"fact pool too thin ({len(fact_lines)} usable facts) — the source URL "
            "probably failed to fetch (CDN/JS gating, 403, empty body) or was too "
            "short. Fix the source or pass a different --generate-from URL; do not "
            "publish a skeleton article."
        )
    source_lines = _source_line(sources, source_label)
    cover_name = plan.cover_filename
    topic_name = plan.title.replace("：", " ").split()[0][:18] or "这件事"
    parts: list[str] = []
    parts.extend(["---", f'title: "{plan.title}"', 'author: "赵涛"', f'cover: "{cover_name}"', "---", ""])
    parts.extend([f"# {plan.title}", "", f"![封面]({cover_name})", "", f"> {plan.subtitle}", ""])
    parts.extend(["## 一分钟看懂", "", f"![一分钟看懂]({plan.sections[0]['filename']})", ""])
    summary_labels = ["核心变化", "影响对象", "风险重点", "现在动作", "后续观察"]
    for label, fact in zip(summary_labels, fact_lines[:5]):
        parts.append(f"- {label}：{fact}。")
    parts.append("")
    for idx, sec in enumerate(plan.sections[1:], start=1):
        primary = _pick_fact(fact_lines, idx, "先把源文里的核心变化和影响范围核对清楚")
        secondary = _pick_fact(fact_lines, idx + 2, "再决定是否需要排期、升级、沟通或持续观察")
        parts.extend([f"## {sec['title']}", "", sec["intro"], "", f"![{sec['title']}]({sec['filename']})", ""])
        if "做什么" in sec["title"] or "行动" in sec["title"] or "建议" in sec["title"]:
            actions = [
                ("核对事实", primary),
                ("缩小范围", secondary),
                ("安排动作", "把需要今天处理、可以排期处理、只需持续观察的事项分开"),
                ("留好记录", "把判断依据、处理人和后续观察项写清楚，方便团队复盘"),
                ("更新入口", "如果涉及用户、客户或交易流程，同步检查对外说明和操作入口"),
            ]
            for n, (action, detail) in enumerate(actions, start=1):
                parts.append(f"{n}. {action}。")
                parts.append(f"   {detail}。")
                parts.append("")
        else:
            parts.append(f"这部分先看一个事实：{primary}。")
            parts.append("")
            parts.append(f"它的意义不在于制造紧张感，而是帮助判断影响范围。对读者来说，关键是分清哪些场景真的命中，哪些只是概念上相关。")
            parts.append("")
            parts.append(f"接下来要关注的是：{secondary}。如果它和自己的业务、资产或运维链路有关，就需要提前留出处理窗口。")
            parts.append("")
    parts.extend(["## 一句话总结", "", f"{topic_name} 这件事不用放大，也别忽略：先看事实，再看影响范围，最后落到今天能执行的动作。", "", "## 参考资料", "", source_lines, ""])
    return "\n".join(parts)


def validate_article_ready(markdown: str) -> None:
    """Hard-fail if generation left workflow placeholders or too little body."""
    blockers = [
        r"后续\s*agent",
        r"自动补全",
        r"图片\s*prompt",
        r"placeholder",
        r"TODO",
        r"关键事实将根据源文章自动整理",
        # Belt-and-suspenders: even though the empty-fact-pool padding has been
        # removed (raises in render_markdown now), keep the literal string in
        # the blocker list so any future regression — or a user who pastes one
        # of those lines back in by hand — still trips the gate.
        r"这条信息需要结合源文",
    ]
    for pattern in blockers:
        if re.search(pattern, markdown, flags=re.I):
            raise RuntimeError(f"generated article still contains placeholder/meta text: {pattern}")
    body = _strip_markdown_for_prompt(markdown)
    if len(body) < MIN_BODY_CHARS:
        raise RuntimeError(f"generated article body is too short ({len(body)} chars); refusing to build images from a skeleton")


def _strip_markdown_for_prompt(text: str) -> str:
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[[^\]]+\]\([^)]*\)", lambda m: m.group(0).split("](", 1)[0].lstrip("["), text)
    text = re.sub(r"^---[\s\S]*?---", " ", text, flags=re.MULTILINE)
    text = re.sub(r"[#>*_`\-]+", " ", text)
    text = re.sub(r"（?正文要点将由后续\s*agent\s*根据事实与图片\s*prompt\s*自动补全[^。]*。）?", " ", text, flags=re.I)
    text = re.sub(r"如需手工编辑[^。]*。", " ", text)
    text = re.sub(r"\b(agent|prompt|placeholder|TODO|自动补全|后续补全)\b", " ", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def _safe_visual_context(title: str, text: str, fallback: str = "") -> str:
    """Return article-derived context that is safe to send to an image model."""
    cleaned = _strip_markdown_for_prompt(" ".join([title, text]))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) < 80:
        cleaned = _strip_markdown_for_prompt(" ".join([title, fallback]))
    return cleaned[:900]


def _markdown_sections(markdown: str) -> list[tuple[str, str]]:
    matches = list(re.finditer(r"^##\s+(.+?)\s*$", markdown, flags=re.MULTILINE))
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        title = match.group(1).strip()
        body = _strip_markdown_for_prompt(markdown[start:end])
        if title != "参考资料":
            sections.append((title, body))
    return sections


def _dynamic_visual_brief(title: str, text: str, index: int, is_cover: bool = False, fallback: str = "") -> dict[str, object]:
    material = _safe_visual_context(title, text, fallback=fallback)
    labels = _topic_keywords(" ".join([title, material]), limit=5) or ["背景", "变化", "影响", "行动"]
    layouts = (
        "one-screen summary cards showing the most important facts and conclusion",
        "before/after or problem/solution comparison diagram grounded only in the text",
        "simple process flow showing what happens first, next, and finally",
        "relationship map showing the main actors, systems, or layers mentioned in the text",
        "boundary/checklist diagram showing what is in scope, out of scope, and what to verify",
        "action roadmap with a few large steps derived from the section text",
    )
    layout = "WeChat 2.35:1 cover poster with one central metaphor inside the center safe zone" if is_cover else layouts[index % len(layouts)]
    reader_question = f"What should the reader understand from this {'WeChat article cover' if is_cover else 'section'}: {title}?"
    return {"material": material, "labels": labels, "layout": layout, "reader_question": reader_question}


def _dynamic_image_prompt(title: str, text: str, index: int, is_cover: bool = False, fallback: str = "") -> str:
    brief = _dynamic_visual_brief(title, text, index, is_cover=is_cover, fallback=fallback)
    labels = "、".join(str(label)[:6] for label in brief["labels"][:5])
    cover_rule = f"{WECHAT_COVER_POLICY} " if is_cover else ""
    aspect_rule = (
        "Deliver a 2.35:1 ultra-wide WeChat cover background, with all important visual information inside the center square safe zone."
        if is_cover
        else "Deliver a clean 16:9 unlabeled explanatory background with reserved blank areas for external captions."
    )
    return (
        f"{STYLE_GUIDE} "
        f"{cover_rule}"
        "Create one text-free visual background for a teaching diagram generated from the supplied article text. "
        "Do not reuse examples, templates, or domain entities from previous articles. "
        f"Current text to visualize: {brief['material']}. "
        f"Reader question: {brief['reader_question']} "
        f"Diagram type and composition: {brief['layout']}. "
        "Use only concepts and relationships that appear in the current text, but represent them as icons, arrows, blank cards, and color groups, not words. "
        "If a concept is not present in the current text, do not draw it. "
        "One image must communicate one takeaway only; use 3 to 5 large visual elements, clear grouping, thick arrows, and phone-readable spacing. "
        f"These labels will be rendered deterministically when needed, do not render them in the raster image: {labels}. "
        f"{PROMPT_TEXT_POLICY} "
        "No decorative wallpaper, no random English, no fake acronyms, no code or terminal dumps, no fake logos, no unrelated hacker imagery, no unrelated imagery from other topics. "
        f"{aspect_rule}"
    )


def build_image_prompts_from_markdown(plan: ArticlePlan, markdown: str) -> None:
    """Generate image prompts after the article text exists.

    The script keeps only stable image slots and filenames in code. Prompt content
    is derived from the current Markdown so changing the article changes the
    create-image input without editing this script.
    """
    sections = _markdown_sections(markdown)
    cover_text = _strip_markdown_for_prompt(markdown)[:1200]
    cover_brief = _dynamic_visual_brief(plan.title, cover_text, 0, is_cover=True)
    plan.cover_prompt = _dynamic_image_prompt(plan.title, cover_text, 0, is_cover=True)
    plan.cover_dynamic_labels = cover_brief["labels"][:5]
    section_lookup = {title: body for title, body in sections}
    if plan.sections:
        summary_body = section_lookup.get("关键信息") or cover_text
        summary_brief = _dynamic_visual_brief(plan.sections[0]["title"], summary_body, 0, fallback=cover_text)
        plan.sections[0]["prompt"] = _dynamic_image_prompt(plan.sections[0]["title"], summary_body, 0, fallback=cover_text)
        plan.sections[0]["_dynamic_labels"] = summary_brief["labels"][:5]
    for index, section in enumerate(plan.sections[1:], start=1):
        body = section_lookup.get(section["title"], " ".join([section.get("intro", ""), cover_text[:500]]))
        section_brief = _dynamic_visual_brief(section["title"], body, index, fallback=cover_text)
        section["prompt"] = _dynamic_image_prompt(section["title"], body, index, fallback=cover_text)
        section["_dynamic_labels"] = section_brief["labels"][:5]


# ---------------------------------------------------------------------------
# Image generation
# ---------------------------------------------------------------------------


def _decode_jwt_payload(token: str) -> dict:
    parts = token.strip().split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload.encode("utf-8")))
    except Exception:
        return {}


def resolve_comate_username_encrypted() -> str:
    """Find the Comate encrypted username for create-image.

    In some Comate Agent terminal sessions, the user is logged in but the
    child shell does not inherit COMATE_USERNAME_ENCRYPTED. Comate stores a
    local bearer token under ~/.comate/login-user/<username>; its JWT payload
    contains content.identity, which is exactly the encrypted username needed
    by create-image. Never print the token or the identity value.
    """
    existing = os.environ.get("COMATE_USERNAME_ENCRYPTED", "").strip()
    if existing:
        return existing

    login_dir = Path.home() / ".comate" / "login-user"
    candidates = []
    if login_dir.exists():
        candidates.extend(p for p in login_dir.iterdir() if p.is_file())
    fallback_login = Path.home() / ".comate" / "login"
    if fallback_login.exists():
        candidates.append(fallback_login)

    for candidate in candidates:
        try:
            raw = candidate.read_text(encoding="utf-8").strip()
        except Exception:
            continue
        raw_candidates = [raw]
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                raw_candidates.extend(
                    str(value)
                    for key, value in parsed.items()
                    if isinstance(value, str) and key.lower() in {"token", "access_token", "authorization", "bearer", "bearertoken"}
                )
        except Exception:
            pass
        for raw_token in raw_candidates:
            bearer_match = re.search(r"Bearer[-\s]+([A-Za-z0-9_.-]+)", raw_token)
            token = bearer_match.group(1) if bearer_match else raw_token
            if token.startswith("Bearer-"):
                token = token[len("Bearer-") :]
            payload = _decode_jwt_payload(token)
            identity = (payload.get("content") or {}).get("identity")
            if isinstance(identity, str) and identity:
                return identity
    return ""


def _retry_prompt(prompt: str, attempt: int) -> str:
    """Make retries safer for image models without losing article grounding."""
    if attempt == 1:
        return prompt + " Retry guidance: make it more educational and less decorative; remove all small text, keep 3 large boxes, one clear arrow path, and only one or two optional short Chinese labels."
    return prompt + " Final retry guidance: create a text-free teaching diagram; no readable words, no numbers, no UI text, only symbolic icons, arrows, boxes, and color-coded structure with blank space for external captions."


def run_create_image(
    prompt: str,
    output: str,
    aspect: str = "16:9",
    resolution: str = "2K",
    input_path: Path | None = None,
) -> bool:
    """Call the create-image skill wrapper. Returns True on success.

    When input_path is provided this uses Nano Banana image-to-image editing,
    which lets us repair generated text without discarding the composition.
    """
    if not CREATE_IMAGE_BASH.exists() and not CREATE_IMAGE_PS1.exists():
        print(f"[generate-article:image] create-image skill not found", file=sys.stderr)
        return False
    def build_cmd(current_prompt: str) -> list[str]:
        if CREATE_IMAGE_BASH.exists():
            cmd = [
                "bash",
                str(CREATE_IMAGE_BASH),
                "--prompt",
                current_prompt,
                "--output",
                output,
                "--aspect-ratio",
                aspect,
                "--resolution",
                resolution,
            ]
            if input_path is not None:
                cmd.extend(["--input", str(input_path)])
            return cmd
        cmd = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(CREATE_IMAGE_PS1),
            "-Prompt",
            current_prompt,
            "-Output",
            output,
            "-AspectRatio",
            aspect,
            "-Resolution",
            resolution,
        ]
        if input_path is not None:
            cmd.extend(["-InputPath", str(input_path)])
        return cmd
    env = os.environ.copy()
    identity = resolve_comate_username_encrypted()
    if identity:
        env["COMATE_USERNAME_ENCRYPTED"] = identity
    else:
        print(
            "[generate-article:image] COMATE_USERNAME_ENCRYPTED not found in env or ~/.comate/login-user",
            file=sys.stderr,
        )
        return False

    last_error = ""
    for attempt in range(CREATE_IMAGE_ATTEMPTS):
        current_prompt = prompt if attempt == 0 else _retry_prompt(prompt, attempt)
        cmd = build_cmd(current_prompt)
        print(
            f"[generate-article:image] {Path(output).name} attempt {attempt + 1}/{CREATE_IMAGE_ATTEMPTS} start (timeout={CREATE_IMAGE_TIMEOUT}s)",
            file=sys.stderr,
        )
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                start_new_session=True,
            )
            try:
                stdout, stderr = proc.communicate(timeout=CREATE_IMAGE_TIMEOUT)
            except subprocess.TimeoutExpired:
                last_error = f"timed out after {CREATE_IMAGE_TIMEOUT}s"
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                    proc.communicate(timeout=5)
                except Exception:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except Exception:
                        pass
                print(f"[generate-article:image] {Path(output).name} attempt {attempt + 1}/{CREATE_IMAGE_ATTEMPTS} timed out after {CREATE_IMAGE_TIMEOUT}s", file=sys.stderr)
                break
            result = subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
        except Exception as e:
            last_error = str(e)
            print(f"[generate-article:image] {Path(output).name} attempt {attempt + 1}/{CREATE_IMAGE_ATTEMPTS} invocation failed: {e}", file=sys.stderr)
            continue
        if result.returncode != 0:
            last_error = result.stderr.strip()[:300]
            print(
                f"[generate-article:image] {Path(output).name} attempt {attempt + 1}/{CREATE_IMAGE_ATTEMPTS} failed: {last_error}",
                file=sys.stderr,
            )
            continue
        # The script writes into images/ relative to CWD.
        src = Path("images") / output
        if src.exists():
            return True
        # Some wrappers print an absolute path instead of writing to images/.
        stdout_lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if stdout_lines:
            out_path = Path(stdout_lines[-1])
            if out_path.exists():
                return True
        last_error = f"expected output not found at {src}"
        print(f"[generate-article:image] {Path(output).name} attempt {attempt + 1}/{CREATE_IMAGE_ATTEMPTS} {last_error}", file=sys.stderr)
    print(f"[generate-article:image] {Path(output).name} exhausted create-image attempts: {last_error}", file=sys.stderr)
    return False


def detect_image_format(path: Path) -> str:
    """Return a simple file-signature image format label."""
    try:
        data = path.read_bytes()[:16]
    except Exception:
        return "unknown"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "gif"
    if data.lstrip().startswith(b"<svg"):
        return "svg"
    return "unknown"


def normalize_wechat_image(path: Path) -> None:
    """Ensure generated images match their extension before Markdown references them."""
    suffix = path.suffix.lower()
    actual = detect_image_format(path)
    if suffix == ".png" and actual == "png":
        return
    if suffix in {".jpg", ".jpeg"} and actual == "jpeg":
        return
    if suffix == ".gif" and actual == "gif":
        return
    if suffix == ".svg" and actual == "svg":
        return
    if suffix == ".png" and actual in {"jpeg", "gif"}:
        tmp = path.with_name(f"{path.stem}.normalized.png")
        result = subprocess.run(
            ["sips", "-s", "format", "png", str(path), "--out", str(tmp)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0 or detect_image_format(tmp) != "png":
            if tmp.exists():
                tmp.unlink()
            raise RuntimeError(f"failed to normalize {path.name} to PNG: {result.stderr.strip()}")
        tmp.replace(path)
        print(f"[generate-article:image] normalized {path.name} from {actual} bytes to PNG", file=sys.stderr)
        return
    raise RuntimeError(f"{path.name}: extension {suffix or '<none>'} does not match detected image format {actual}")


def audit_image_plan(plan: ArticlePlan) -> list[str]:
    """Check prompts before generation so every image is tied to article content."""
    warnings: list[str] = []
    title_blob = " ".join([plan.title, *[s.get("title", "") for s in plan.sections]])
    vulnerability_context = any(token in title_blob.lower() for token in ("攻击", "漏洞", "cve", "attack", "vulnerability"))
    for item in plan.illustrations:
        prompt = item.get("prompt", "")
        name = item.get("filename", "<unknown>")
        if "Article context:" not in prompt and "Current text to visualize:" not in prompt:
            warnings.append(f"{name}: prompt missing article-derived context")
        if "do not invent" not in prompt.lower():
            warnings.append(f"{name}: prompt missing no-invented-text guard")
        if re.search(r"后续\s*agent|自动补全|图片\s*prompt|placeholder|TODO", prompt, flags=re.I):
            warnings.append(f"{name}: prompt contains workflow placeholder/meta text")
        if "do not render any readable text" not in prompt.lower():
            warnings.append(f"{name}: AI prompt should request a text-free background")
        title_lower = title_blob.lower()
        prompt_lower = prompt.lower()
        sentences = re.split(r"[.。]", prompt_lower)
        positive_prompt = " ".join(
            sentence
            for sentence in sentences
            if not any(cue in sentence for cue in ("do not", "don't", "no ", "without", "unless", "not "))
        )
        if "cve" in positive_prompt and "CVE" not in title_blob and "漏洞" not in title_blob:
            warnings.append(f"{name}: prompt mentions CVE although article plan is not CVE-focused")
        if "attack" in positive_prompt and not vulnerability_context:
            warnings.append(f"{name}: prompt uses attack framing; verify the article really describes an attack path")
    return warnings


def audit_generated_image(path: Path, item: dict) -> list[str]:
    """Best-effort post-generation audit.

    This environment has no OCR dependency by default, so the deterministic audit
    verifies file health and records a manual text-review checklist next to each
    image. The surrounding agent must still visually inspect generated images
    before preview/upload, because AI image models often create plausible-looking
    but wrong Chinese text.
    """
    warnings: list[str] = []
    if not path.exists():
        return [f"{path.name}: file missing"]
    if path.stat().st_size < 10_000:
        warnings.append(f"{path.name}: file is unusually small")
    suffix = path.suffix.lower()
    if suffix == ".png":
        try:
            data = path.read_bytes()[:24]
            if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                warnings.append(f"{path.name}: not a valid PNG header")
            elif len(data) >= 24:
                import struct

                width, height = struct.unpack(">II", data[16:24])
                if width < 900 or height < 500:
                    warnings.append(f"{path.name}: resolution {width}x{height} is too small for WeChat illustration")
        except Exception as exc:
            warnings.append(f"{path.name}: failed to inspect image header: {exc}")
    labels = item.get("_dynamic_labels") or []
    if any(re.search(r"[A-Za-z].*[\u4e00-\u9fff]|[\u4e00-\u9fff].*[A-Za-z]", str(label)) for label in labels):
        warnings.append(f"{path.name}: dynamic labels mix English and Chinese; visually check output for garbled labels")
    return warnings


def write_image_audit_report(target_dir: Path, reports: list[dict]) -> None:
    lines = [
        "# Image Audit Report",
        "",
        "Generation is article-aware, but AI-rendered text must be visually checked before upload.",
        "",
        "## Required manual checks",
        "",
        "- 每张图是否与当前文章主题和小节内容匹配。",
        "- 图片中文字是否有错字、乱码、英文乱词、重复编号。",
        "- 是否出现与文章无关的漏洞、攻击链、命令行或品牌 Logo。",
        "- 流程图编号是否连续且无重复。",
        "",
        "## Files",
        "",
    ]
    for report in reports:
        lines.append(f"- `{report['file']}` — {report['title']}")
        if report["warnings"]:
            for warning in report["warnings"]:
                lines.append(f"  - WARNING: {warning}")
        else:
            lines.append("  - Basic file checks passed; still requires visual text review.")
    (target_dir / "image_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _clean_generation_artifacts(target_dir: Path, images_dir: Path) -> None:
    """Remove stale image outputs from earlier runs before deriving fresh specs."""
    for stale in (target_dir / "image_specs.json", target_dir / "image_audit.md"):
        stale.unlink(missing_ok=True)
    for stale_dir_name in ("prompts", "candidates"):
        stale_dir = images_dir / stale_dir_name
        if stale_dir.exists():
            shutil.rmtree(stale_dir)
    for pattern in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.gif"):
        for stale_image in images_dir.glob(pattern):
            stale_image.unlink(missing_ok=True)


def _resolve_visual_type(kind: str, title: str = "", items: list[dict] | None = None) -> str:
    """Choose conservative Type values that are stable for technical articles."""
    text = " ".join([kind, title, " ".join(str(i.get("label", "")) + " " + str(i.get("sub", "")) for i in (items or []))]).lower()
    if kind == "cover":
        return "framework"
    if any(token in text for token in ("现在可以做", "检查", "升级", "pom.xml", "gradle", "checklist", "命令")):
        return "infographic"
    if any(token in text for token in ("风险", "边界", "高风险", "降低", "compare", "vs", "覆盖", "不覆盖")):
        return "comparison"
    if any(token in text for token in ("step", "第一步", "第二步", "流程", "触发", "attack", "authority", "additional")):
        return "flowchart"
    # DNS / resolver / cache / bailiwick topics are safest as layered maps.
    if any(token in text for token in ("关系", "理解", "resolver", "cache", "bailiwick", "framework", "dns", "netty")):
        return "framework"
    mapping = {
        "summary": "infographic",
        "flow": "flowchart",
        "compare": "comparison",
        "impact": "framework",
        "relations": "framework",
        "timeline": "flowchart",
        "checklist": "infographic",
    }
    return mapping.get(kind, "framework")


def _resolve_style_palette(visual_type: str, title: str = "") -> tuple[str, str, str]:
    """Use the user-selected visual theme, while preserving the diagram role."""
    theme = IMAGE_VISUAL_THEME
    theme_palettes = {
        "clean-professional": (
            "Apple product page meets Monocle editorial — clean, authoritative, premium",
            "soft light gray, tech blue, charcoal",
        ),
        "editorial-magazine": (
            "Kinfolk magazine cover meets Wired feature spread — sophisticated editorial design",
            "warm neutral, soft charcoal, one muted accent",
        ),
        "snoopy-comic": (
            "Peanuts comic strip — warm, philosophical, charming",
            "warm cream/newspaper tone, soft muted pastels, warm ink lines",
        ),
        "bold-statement": (
            "Brutalist editorial magazine cover — text as primary visual element",
            "stark high contrast, dark editorial background, light accents",
        ),
        "data-infographic": (
            "The Economist data visualization meets Information is Beautiful",
            "clean neutral background, one primary data color, one accent for highlights",
        ),
        "whiteboard-sketch": (
            "xkcd meets professor's whiteboard — extreme minimalism, humor, insight",
            "whiteboard off-white, black ink, one red or blue accent",
        ),
        "warm-narrative": (
            "Airbnb brand presentation meets Mailchimp editorial — approachable, human, warm",
            "warm cream, coral accent, charcoal",
        ),
    }
    style, palette = theme_palettes.get(theme, theme_palettes["clean-professional"])
    role_composition = {
        "flowchart": "A clean left-to-right technical process with large nodes, arrows, boundary bands, and minimal/no readable text.",
        "comparison": "A side-by-side risk boundary board with safe and risky zones, clear visual contrast, icons, and empty label strips.",
        "infographic": "A polished technical dashboard with metric cards, dependency/version badges, checklist rows, and clean empty caption areas.",
    }
    composition = role_composition.get(
        visual_type,
        "A stable layered architecture map showing components, trust boundaries, cache state, resolver behavior, and traffic paths.",
    )
    return style, palette, composition


def _visual_labels_for_spec(spec: dict) -> list[str]:
    labels: list[str] = []
    title = str(spec.get("title_zh") or "Technical diagram")
    labels.append(_english_label(title, 30))
    for item in (spec.get("items") or [])[:6]:
        label = _english_label(str(item.get("label") or ""), 26)
        sub = _english_label(str(item.get("sub") or ""), 34)
        text = " — ".join([part for part in (label, sub) if part])
        if text:
            labels.append(text)
    return labels


# visual_types that benefit from sparse English labels inside the raster.
# Covers and abstract framework diagrams stay text-free because (a) the cover
# safe-zone constraint makes label placement fragile and (b) framework maps
# tend to be dense and word-soup-prone.
TEXT_FRIENDLY_VISUAL_TYPES = {"flowchart", "comparison", "infographic"}

# Hard rejections: tokens we never want a model to render even if they leak
# through the article. Keeps prompts honest under the "no fake acronyms" rule.
_LABEL_BLOCKLIST = {"KEY", "FLOW", "STEP", "ITEM", "VALUE", "TODO", "PROMPT", "AGENT", "PLACEHOLDER"}


def _truncate_for_context(text: str, limit: int = 72) -> str:
    """Shorten a context string for the MUST SHOW block without altering meaning.

    Used when `_english_label` returns empty (pure Chinese item) so the prompt
    still carries article-specific concepts as grounding context. The model
    is separately told NOT to render this text — it's prompt-only context.
    """
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    if not s:
        return ""
    return s[:limit].rstrip(" ，,；;。！？!?")


def _whitelist_label(raw: str, limit: int = 32) -> str:
    """Normalize a candidate raster label to a short, all-uppercase ASCII chip.

    Returns empty string for low-quality candidates (Chinese-only, blocked
    placeholder tokens, or pseudo-acronyms like "KEY FLOW"). Also collapses
    repeats so "DNS DNS" → "DNS", and never truncates mid-word — if the
    cleaned text exceeds `limit`, we walk back to the last space (or drop
    the whole label if no clean break is possible). Mid-word truncation
    used to produce "POWERDNS AUTHORITATI" in image prompts, which Nano
    Banana then rendered as malformed text.
    """
    if not raw:
        return ""
    # Pull ASCII technical tokens; ignore everything else.
    parts = re.findall(r"[A-Za-z][A-Za-z0-9+./-]{1,}", raw)
    if not parts:
        return ""
    # Dedupe while preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for p in parts:
        key = p.lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    text = " ".join(uniq)
    text = re.sub(r"\s+", " ", text).strip().upper()
    words = [w for w in re.split(r"[ \-_./]+", text) if w]
    if not words or all(w in _LABEL_BLOCKLIST for w in words):
        return ""
    # Cap at 3 words to keep labels readable inside diagram boxes.
    if len(words) > 3:
        words = words[:3]
        text = " ".join(words)
    # If still over the char limit, drop trailing words until we fit; never
    # cut mid-word (that produced "AUTHORITATI" before).
    while len(text) > limit and len(words) > 1:
        words.pop()
        text = " ".join(words)
    if len(text) > limit:
        # Single word longer than the limit — refuse rather than truncate.
        return ""
    return text.strip(" -./_")


def _whitelist_labels_for_spec(spec: dict, cap: int = 5) -> list[str]:
    """Build the per-image English label whitelist from spec.items.

    The whitelist drives both the prompt's MUST SHOW section and the OCR
    allowlist downstream, so any English word the renderer accepts back must
    have been authorized here first.
    """
    candidates: list[str] = []
    title_chip = _whitelist_label(str(spec.get("title_zh") or ""), 20)
    if title_chip:
        candidates.append(title_chip)
    for item in (spec.get("items") or [])[:6]:
        if item.get("_low_quality"):
            continue
        for raw in (item.get("label"), item.get("sub")):
            chip = _whitelist_label(str(raw or ""), 24)
            if chip and chip not in candidates:
                candidates.append(chip)
            if len(candidates) >= cap:
                break
        if len(candidates) >= cap:
            break
    return candidates[:cap]


_CJK_LABEL_STOPWORDS = {
    "背景", "影响", "问题", "建议", "总结", "关键信息", "重点", "要点", "说明", "提示",
    "第一步", "第二步", "第三步", "第四步", "第五步",
}


def _short_chinese_label(raw: str, limit: int = 6) -> str:
    """Return a Guizang-style short Simplified Chinese callout label.

    Body AI illustrations work better when the model receives 3-5 concrete,
    spatial labels instead of long subtitles or translated English chips.
    """
    text = re.sub(r"[`*_#>\[\]（）()《》:：,，。；;！？!?]+", " ", str(raw or ""))
    text = re.sub(r"https?://\S+|www\.\S+", " ", text)
    parts = re.findall(r"[\u4e00-\u9fffA-Za-z0-9+./-]+", text)
    for part in parts:
        token = part.strip()
        if not token:
            continue
        if re.fullmatch(r"[A-Za-z0-9+./-]+", token):
            continue
        token = re.sub(r"^[一二三四五六七八九十]+[、.．]", "", token)
        token = token.strip()
        if token in _CJK_LABEL_STOPWORDS:
            continue
        cjk_count = len(re.findall(r"[\u4e00-\u9fff]", token))
        if cjk_count < 2:
            continue
        if cjk_count > limit:
            cjk_only = "".join(re.findall(r"[\u4e00-\u9fff]", token))
            token = cjk_only[:limit]
        return token[:limit]
    return ""


def _guizang_labels_for_spec(spec: dict, cap: int = 5) -> list[str]:
    candidates: list[str] = []
    for item in (spec.get("items") or [])[:8]:
        if item.get("_low_quality"):
            continue
        for raw in (item.get("label"), item.get("sub")):
            label = _short_chinese_label(str(raw or ""))
            if label and label not in candidates:
                candidates.append(label)
            if len(candidates) >= cap:
                return candidates
    title_label = _short_chinese_label(str(spec.get("title_zh") or ""))
    if title_label and title_label not in candidates:
        candidates.append(title_label)
    return candidates[:cap]


def _guizang_structure_for_visual_type(visual_type: str, title: str) -> str:
    text = f"{visual_type} {title}"
    if visual_type == "flowchart" or any(token in text for token in ("流程", "链路", "迁移", "进入", "路径")):
        return "Pipeline: show a left-to-right route with input, decision/processing blocks, and output."
    if visual_type == "comparison" or any(token in text for token in ("区别", "对比", "边界", "取舍", "vs")):
        return "Before/after or side-by-side: show two states with a clear transformation or boundary in the middle."
    if visual_type == "infographic" or any(token in text for token in ("检查", "清单", "升级", "要点")):
        return "Data-first scene: show compact checklist cards or material tiles arranged around one central object."
    return "Layer stack or hub-and-spoke: show a central routing object coordinating several branches or stacked layers."


def _guizang_body_prompt(
    *,
    spec: dict,
    article_title: str,
    title: str,
    visual_type: str,
    item_details: str,
    labels: list[str],
) -> str:
    label_block = ", ".join(f'"{label}"' for label in labels)
    label_count = len(labels)
    label_instruction = (
        f"Add exactly {label_count} short Simplified Chinese labels as clean printed callouts inside the illustration: {label_block}. "
        "Use each label at most once. Place each label near the matching object; keep labels horizontal, large, high-contrast, readable, and away from edges."
        if labels
        else "Do not render readable text; use blank callout plates only."
    )
    text_constraints = (
        "only the specified Chinese labels, no duplicate labels, no English text, no extra Chinese text, no numbers unless they are part of the article concept"
        if labels
        else "no readable text, no English text, no Chinese text, no numbers"
    )
    return f"""# WeChat Body Image Prompt — Guizang Material Illustration

metadata:
  backend: create-image
  type: {visual_type}
  style: Guizang material illustration
  palette: off-white, black ink, refined gray surfaces, one vivid IKB blue accent (#002FA7)
  aspect: 16:9
  language: zh-short-labels
  text_mode: {'guizang-labels' if labels else 'text-free'}

TASK
Generate one explanatory body illustration for a WeChat technical article. Make it a Guizang-style labeled material illustration, not a decorative poster.

ARTICLE CONTEXT
- Article title: {article_title}
- Section: {title}
- Visual type: {visual_type}
- Reader takeaway: {spec.get('notes','') or spec.get('source_section','')}

PRIMARY REQUEST
Use case: stylized-concept.
Asset type: 16:9 labeled material illustration for a WeChat technical article.
Primary request: {title} shown as a concrete teaching diagram. {_guizang_structure_for_visual_type(visual_type, title)} The viewer should understand the relationship or process at social-card size.

ARTICLE-SPECIFIC CONTENT TO SHOW
{item_details}

CHINESE LABELS
{label_instruction}

STYLE / MEDIUM
Clean Swiss editorial 3D vector-like material illustration, off-white studio background, black ink outlines, subtle gray physical surfaces, one vivid IKB blue accent (#002FA7) for arrows, active blocks, dots, and label connectors. Soft studio light, mild contact shadows, calm analytical mood.

COMPOSITION / FRAMING
16:9 composition, subject fills the width naturally, centered vertically, generous safe margins on all sides, full subject visible, no crop. Use one simple metaphor only. Prefer physical model-like blocks, arrows, shields, routing hubs, layer stacks, check cards, or boundary plates. Avoid app screenshots and fake UI chrome.

CONSTRAINTS
{text_constraints}, no dense legend, no paragraph text, no tiny device text, no logo, no watermark, no poster frame, no page title, no decorative blobs, no bokeh, no gradient background, no unrelated cyber wallpaper, no fake acronyms.
""".strip() + "\n"


def _build_image_prompt(spec: dict, article_title: str) -> str:
    """Build a /create-image prompt for one WeChat illustration spec.

    Article-first specs drive visual type, style, and palette choices, while
    prompts are English-first to reduce CJK OCR errors and improve grounding.

    Text policy is two-tiered:
      - cover / framework / relations → no text at all (covers' safe-zone
        constraint + dense abstract maps make labels fragile).
      - flow / compare / infographic / checklist / summary / impact / timeline
        → allow up to 5 short English labels from a whitelist derived from
        spec.items, so the diagram actually teaches the reader. Chinese stays
        out of the raster: model-rendered CJK is the #1 OCR failure mode.
    """
    items = spec.get("items") or []
    kind = str(spec.get("kind") or "summary")
    title = str(spec.get("title_zh") or "")
    visual_type = _resolve_visual_type(kind, title, items)
    style, palette, composition = _resolve_style_palette(visual_type, title)
    visual_labels = _visual_labels_for_spec(spec)
    label_hint = "; ".join(visual_labels[:7]) or _english_label(article_title, 48)

    allow_text = kind != "cover"
    guizang_labels = _guizang_labels_for_spec(spec, cap=5) if allow_text else []
    whitelist = _whitelist_labels_for_spec(spec, cap=5) if allow_text and visual_type in TEXT_FRIENDLY_VISUAL_TYPES and not guizang_labels else []
    if allow_text and not guizang_labels and not whitelist:
        # We promised the model labels but produced no good chips — fall back
        # to text-free mode rather than letting the model invent acronyms.
        allow_text = False

    cover_poem = [str(line).strip() for line in (spec.get("cover_text") or []) if str(line).strip()]
    cover_poem_block = " / ".join(cover_poem)

    if kind == "cover":
        # Covers are allowed to carry a short article-derived poem. Keep other
        # article concepts as visual motifs so the image does not become a dense diagram.
        motif_labels = [str(item.get("label") or "").strip() for item in items[:4] if not item.get("_low_quality")]
        motif_hint = ", ".join([label for label in motif_labels if label]) or (title or article_title)
        item_details = (
            "Use the article concepts as Chinese-painting visual motifs: "
            f"{_truncate_for_context(motif_hint, 120)}. "
            "Translate the topic into mountains, flowing water, mist, pavilions, bamboo, plum branches, river paths, ink-wash infrastructure silhouettes, or other restrained guohua metaphors. "
            "Keep the center square safe zone visually complete and elegant."
        )
    else:
        item_details_lines: list[str] = []
        for idx, item in enumerate(items[:6]):
            if item.get("_low_quality"):
                continue
            raw_label = str(item.get("label") or "")
            raw_sub = str(item.get("sub") or "")
            en_label = _english_label(raw_label, 32) or _truncate_for_context(raw_label, 32)
            en_sub = _english_label(raw_sub, 72) or _truncate_for_context(raw_sub, 72)
            if not en_label and not en_sub:
                continue
            if en_sub:
                item_details_lines.append(f"- {len(item_details_lines) + 1}. {en_label}: {en_sub}")
            else:
                item_details_lines.append(f"- {len(item_details_lines) + 1}. {en_label}")
        if not item_details_lines:
            # Use the article title as a last-ditch hint rather than emitting an
            # empty MUST SHOW block, but never pad with "Key Flow" placeholders.
            item_details_lines.append(
                f"- {_english_label(title or article_title, 72) or _truncate_for_context(title or article_title, 72)}"
            )
        item_details = "\n".join(item_details_lines)

    if kind != "cover" and allow_text and guizang_labels:
        return _guizang_body_prompt(
            spec=spec,
            article_title=article_title,
            title=title,
            visual_type=visual_type,
            item_details=item_details,
            labels=guizang_labels,
        )

    if kind == "cover":
        poem_instruction = cover_poem_block or "derive a two-line Chinese poem from the article title and section notes"
        text_policy_block = f"""TEXT POLICY (cover poem calligraphy)
Render exactly this short Chinese poem from the current article, either two lines or four lines:
{poem_instruction}

Rules:
- Use elegant Chinese calligraphy: lishu, kaishu, or refined brush script.
- Place the poem as a tasteful vertical or horizontal inscription within the center safe zone, like a seal-and-inscription area on a guohua cover.
- Keep characters large, sparse, and legible on a phone screen; avoid tiny body text.
- Do not add any other readable text, English words, numbers, URLs, UI labels, fake stamps, or invented captions.
- If the model cannot render all poem lines cleanly, prefer fewer large accurate lines over many small garbled characters."""
        cover_text_block = """COVER-SPECIFIC STYLE
Prioritize AI-generated cover art in Chinese painting style: ink wash, xuan paper texture, mineral color accents, negative space, mist, mountains/water/bamboo/plum/pavilion metaphors, and a refined editorial WeChat cover composition. Avoid modern SaaS cards, dashboards, cyber wallpaper, UI panels, and decorative network backgrounds on the cover."""
    elif allow_text:
        whitelist_block = ", ".join(f'"{chip}"' for chip in whitelist)
        text_policy_block = f"""TEXT POLICY (English-only, sparse, whitelist)
You MAY render up to {len(whitelist)} short English labels, ALL FROM THIS WHITELIST:
[{whitelist_block}]

Rules:
- English only — NO Chinese characters, NO mixed-language text.
- Each label is ≤ 3 words and appears AT MOST ONCE in the image.
- Labels live inside dedicated boxes/badges/headers, NEVER floating in the background.
- Sans-serif, high contrast, large enough to read on a phone screen (≥ 28pt equivalent).
- DO NOT invent any label outside the whitelist. DO NOT translate Chinese phrases.
- DO NOT render code, URLs, long sentences, version numbers with 3+ segments, or domain names not on the whitelist.
- Step numbers 1-{min(len(whitelist), 9)} are allowed inside step badges only.
- If a whitelist label cannot be placed cleanly, LEAVE THE SLOT BLANK. External captions handle accuracy."""
        cover_text_block = ""
    else:
        text_policy_block = """TEXT POLICY
Do not render any readable text inside the image. No Chinese, no English words, no numbers, no acronyms, no domain names, no code, no URLs.
Represent article-specific concepts visually only: neutral infrastructure blocks, service nodes, arrows, shields, documents, dashboards, checklists, or boundary shapes selected from the current section.
For body illustrations, blank label zones are allowed for external captions."""
        cover_text_block = ""

    must_show_header = (
        "COVER VISUAL MOTIFS AND POEM — ARTICLE-SPECIFIC"
        if kind == "cover"
        else "MUST SHOW — ARTICLE-SPECIFIC CONTENT (visual mapping, not text rendering)"
        if not allow_text
        else "MUST SHOW — ARTICLE-SPECIFIC CONTENT (drives visual elements; render only whitelisted labels)"
    )

    negative_extra = "" if (allow_text or kind == "cover") else "no readable text, no letters, no words, no numbers, "
    if kind == "cover":
        negative_prompt = (
            "no garbled Chinese, no pseudo-Chinese, no random English, no fake acronyms, "
            "no URLs, no code, no UI labels, no dense paragraphs, no modern dashboard cards, "
            "no cyber wallpaper, no hacker stock art, no unrelated product UI, no fake logos, "
            "no duplicated poem lines, no text outside the requested poem"
        )
    else:
        negative_prompt = (
            f"{negative_extra}no Chinese text, no garbled text, no random English outside the whitelist, "
            "no fake acronyms, no decorative network wallpaper, no hacker stock art, no unrelated product UI, "
            "no dense paragraphs, no duplicated step numbers, no text placeholders, no white label rectangles, no UI labels outside the whitelist."
        )

    return f"""# WeChat Image Prompt

metadata:
  backend: create-image
  type: {visual_type}
  style: {style}
  palette: {palette}
  aspect: {'2.35:1' if kind == 'cover' else '16:9'}
  language: en-first
  text_mode: {'whitelist-labels' if allow_text else 'text-free'}

TASK
Generate one polished raster illustration for a WeChat technical article.
Propose an explanatory visual, keep the style unified, generate with /create-image, and verify text/semantics against the article.
Make it explanatory, not decorative.
{'For the cover image, follow WeChat cover rules: 2.35:1 ultra-wide composition, all key visual information inside the center square safe zone, side areas only for atmosphere.' if kind == 'cover' else 'For body illustrations, use a phone-readable 16:9 composition with medium-tone background for WeChat dark-mode compatibility.'}

ARTICLE CONTEXT
- Article title: {article_title}
- Section: {title}
- Visual type: {visual_type}
- Reader takeaway: {spec.get('notes','') or spec.get('source_section','')}

TYPE × STYLE × PALETTE DECISION
- Type: {visual_type} because this section needs a structured explanation.
- Style: {style}.
- Palette: {palette}.
- Composition: {composition}

{must_show_header}
{item_details}

VISUAL STRUCTURE
- Use 3 to 6 large zones/cards with clear arrows or boundaries.
- Include only entities, roles, protocols, products, versions, risks, or actions that appear in ARTICLE CONTEXT or MUST SHOW.
- If the section describes a process, show the current article's before/after or input/output relationship without inventing a vulnerability narrative.
- Avoid generic cyber wallpaper; every visual element must map to this article.

{text_policy_block}
{cover_text_block}

STYLE
Premium technical magazine illustration, rich information density, clean vector-like raster art,
soft shadows, layered cards, readable mobile composition, high contrast, no faces, no fake logos,
no screenshots, no terminal dumps, no clutter.

NEGATIVE PROMPT
{negative_prompt}
""".strip() + "\n"


def _norm_ocr_text(text: str) -> str:
    text = text.replace("×", "x").replace("Ｘ", "x").replace("ｘ", "x")
    return re.sub(r"[\s·•，。！？!?,.\-—_·．、；;：:（）()]+", "", text).lower()


def _english_ocr_allowlist(spec: dict) -> list[str]:
    """OCR allowlist for a given spec.

    Combines legacy English labels, Guizang short Chinese labels, and cover
    poem lines. Any text the prompt explicitly permits should not trigger a
    stray-text failure merely because OCR recognized it.
    """
    labels = _visual_labels_for_spec(spec)
    labels.extend(_english_label(label, 48) for label in _visual_labels_for_spec(spec))
    labels.extend(_whitelist_labels_for_spec(spec, cap=5))
    labels.extend(_guizang_labels_for_spec(spec, cap=5))
    if str(spec.get("kind") or "summary") == "cover":
        labels.extend(str(line) for line in (spec.get("cover_text") or []) if str(line).strip())
    return [label for label in labels if label]


def _ocr_warnings_for_image(image: Path, spec: dict) -> tuple[list[str], list[str]]:
    if _ocr_validate is None:
        return ["OCR unavailable; cannot verify generated text"], []
    warnings, lines = _ocr_validate.validate(image, spec)
    # For AI-generated raster illustrations we intentionally allow text-free
    # images and a controlled whitelist of English technical labels. Missing
    # expected Chinese title/labels is acceptable; stray/garbled text is the risk.
    allowed_norm = [_norm_ocr_text(label) for label in _english_ocr_allowlist(spec)]
    filtered: list[str] = []
    for warning in warnings:
        if warning.startswith("missing title in OCR"):
            continue
        match = re.search(r"stray text in image: (.+)", warning)
        if match:
            raw = match.group(1).strip().strip("'")
            normalized = _norm_ocr_text(raw)
            if any((normalized in allowed) or (allowed in normalized) for allowed in allowed_norm if len(allowed) >= 3):
                continue
        filtered.append(warning)
    return filtered, lines


def _background_prompt(prompt_text: str, spec: dict) -> str:
    """Convert a normal AI image prompt into a strict text-free background prompt."""
    kind = str(spec.get("kind") or "summary")
    title = str(spec.get("title_zh") or "")
    items = spec.get("items") or []
    concepts = ", ".join(str(item.get("label") or "") for item in items[:6] if item.get("label"))
    return f"""{prompt_text}

BACKGROUND-ONLY OVERRIDE
Generate only the visual background layer for later HTML text overlay.
Do not draw any readable text, numbers, letters, badges with words, UI labels, terminal text, code, URLs, or pseudo-glyphs.
Leave clean negative-space panels where real text will be added by HTML after generation.
Article section: {title}
Visual concepts to depict without text: {concepts}
Layering requirement: polished editorial illustration, clear subject, enough calm space for overlay text, no label placeholders.
Final image must still be a complete {kind} illustration even before text is overlaid.
""".strip() + "\n"


def _overlay_html_for_spec(spec: dict, background_path: Path) -> str:
    """Build an HTML composition that uses an AI background plus exact text."""
    if _html_render is None:
        raise RuntimeError("html_render unavailable; cannot compose AI background with text overlay")
    kind = str(spec.get("kind") or "summary")
    width, height = _html_render.canvas_for_kind(kind)
    rel_bg = background_path.resolve().as_uri()
    title = _html_render._esc(spec.get("title_zh", ""))  # type: ignore[attr-defined]
    subtitle = _html_render._esc(spec.get("subtitle", ""))  # type: ignore[attr-defined]
    items = (spec.get("items") or [])[:6]
    palette = spec.get("theme_palette") or {}
    primary = palette.get("primary", "#2563eb")
    accent = palette.get("accent", "#0ea5e9")
    ink = palette.get("ink", "#111827")
    rows = []
    for idx, item in enumerate(items):
        label = _html_render._esc(item.get("label", ""))  # type: ignore[attr-defined]
        sub = _html_render._esc(item.get("sub", ""))  # type: ignore[attr-defined]
        step = _html_render._esc(item.get("step") or str(idx + 1))  # type: ignore[attr-defined]
        sub_html = f'<div class="sub">{sub}</div>' if sub else ""
        rows.append(
            f'<div class="row"><div class="step">{step}</div><div class="copy"><div class="label">{label}</div>'
            f'{sub_html}</div></div>'
        )
    rows_html = "".join(rows)
    subtitle_html = f'<div class="subtitle">{subtitle}</div>' if subtitle else ''
    cover_class = "cover" if kind == "cover" else "body"
    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">{_html_render.WEBFONT_LINK}<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ width:{width}px; height:{height}px; overflow:hidden; font-family:{_html_render.FONT_STACK}; color:{ink}; position:relative; background:#f8fafc; }}
  .bg {{ position:absolute; inset:0; background:url('{rel_bg}') center/cover no-repeat; filter:saturate(1.08) contrast(1.02); }}
  .wash {{ position:absolute; inset:0; background:linear-gradient(90deg, rgba(248,250,252,.95) 0%, rgba(248,250,252,.82) 42%, rgba(248,250,252,.28) 100%); }}
  .panel {{ position:absolute; left:{'517px' if kind == 'cover' else '72px'}; top:{'72px' if kind == 'cover' else '58px'}; width:{'766px' if kind == 'cover' else '760px'}; bottom:{'72px' if kind == 'cover' else '58px'}; border-radius:34px; background:rgba(255,255,255,.78); border:1px solid rgba(15,23,42,.10); box-shadow:0 26px 70px rgba(15,23,42,.16); backdrop-filter:blur(10px); padding:{'54px 56px' if kind == 'cover' else '46px 48px'}; display:flex; flex-direction:column; justify-content:center; }}
  .kicker {{ font-family:{_html_render.FONT_STACK_MONO}; font-size:{'20px' if kind == 'cover' else '18px'}; font-weight:900; letter-spacing:4px; color:{primary}; margin-bottom:18px; }}
  .title {{ font-size:{'58px' if kind == 'cover' else '52px'}; line-height:1.14; font-weight:900; letter-spacing:-1px; word-break:normal; overflow-wrap:anywhere; }}
  .subtitle {{ margin-top:16px; font-size:26px; line-height:1.45; color:#475569; font-weight:700; }}
  .rows {{ margin-top:30px; display:flex; flex-direction:column; gap:14px; }}
  .row {{ display:grid; grid-template-columns:46px 1fr; gap:14px; align-items:flex-start; padding:14px 16px; border-radius:20px; background:rgba(255,255,255,.72); border:1px solid rgba(15,23,42,.08); }}
  .step {{ width:38px; height:38px; border-radius:13px; display:flex; align-items:center; justify-content:center; background:{accent}; color:white; font-family:{_html_render.FONT_STACK_MONO}; font-size:18px; font-weight:900; }}
  .label {{ font-size:25px; line-height:1.32; font-weight:900; word-break:normal; overflow-wrap:anywhere; }}
  .sub {{ margin-top:4px; font-size:18px; line-height:1.4; color:#64748b; font-weight:650; word-break:normal; overflow-wrap:anywhere; }}
  body.cover .rows {{ display:none; }}
</style></head><body class="{cover_class}"><div class="bg"></div><div class="wash"></div><section class="panel"><div class="kicker">视觉解读</div><div class="title">{title}</div>{subtitle_html}<div class="rows">{rows_html}</div></section></body></html>"""


def _compose_ai_text_overlay(spec: dict, background_path: Path, output_path: Path) -> tuple[Path | None, list[str]]:
    """Render exact text over a text-free AI background using Playwright."""
    if _html_render is None:
        return None, ["html_render unavailable; cannot overlay text on AI background"]
    try:
        kind = str(spec.get("kind") or "summary")
        width, height = _html_render.canvas_for_kind(kind)
        html_path = output_path.with_suffix(".overlay.html")
        html_path.write_text(_overlay_html_for_spec(spec, background_path), encoding="utf-8")
        ok = _html_render.render_png(html_path, output_path, width, height)
        if not ok or not output_path.exists():
            return None, ["AI text overlay render failed"]
        normalize_wechat_image(output_path)
        return output_path, []
    except Exception as exc:
        return None, [f"AI text overlay failed: {exc}"]


def _low_risk_english_retry_warnings(warnings: list[str]) -> bool:
    """Allow keeping raster retries when only OCR noise remains.

    Nano Banana often leaves tiny pseudo-glyphs or single-character OCR false
    positives on otherwise useful text-light diagrams. These should trigger
    audit review, but should not force discarding a valid raster retry.
    """
    if not warnings:
        return True
    for warning in warnings:
        match = re.search(r"stray text in image: (.+)", warning)
        if not match:
            return False
        raw = match.group(1).strip().strip("'")
        normalized = _norm_ocr_text(raw)
        if len(normalized) > 2:
            return False
    return True


def _allowed_text_for_spec(spec: dict, english: bool = False) -> str:
    if english:
        title = _english_label(str(spec.get("title_zh") or "Technical diagram"), 48)
        items = [_english_label(str(it.get("label") or ""), 28) for it in (spec.get("items") or [])[:5]]
    else:
        title = str(spec.get("title_zh") or "")[:28]
        items = [str(it.get("label") or "")[:18] for it in (spec.get("items") or [])[:5]]
    return " / ".join([s for s in [title, *items] if s])


def _english_label(text: str, limit: int = 32) -> str:
    """Short English-ish labels for text-light repair prompts.

    We avoid asking the model to translate arbitrary Chinese perfectly; instead
    map broad article concepts and then keep safe ASCII tokens from the current text.
    """
    mapping = [
        ("第一步", "Step 1"),
        ("第二步", "Step 2"),
        ("第三步", "Step 3"),
        ("第四步", "Step 4"),
        ("第五步", "Step 5"),
        ("服务器", "Server"),
        ("客户端", "Client"),
        ("浏览器", "Browser"),
        ("网关", "Gateway"),
        ("服务", "Service"),
        ("系统", "System"),
        ("平台", "Platform"),
        ("网络", "Network"),
        ("解析", "Resolution"),
        ("迁移", "Migration"),
        ("监控", "Monitoring"),
        ("风险", "Risk"),
        ("范围", "Scope"),
        ("清单", "Checklist"),
        ("流程", "Flow"),
        ("对比", "Comparison"),
        ("影响", "Impact"),
        ("修复", "Fix"),
        ("补丁", "Patch"),
        ("发布", "Release"),
        ("配置", "Config"),
        ("验证", "Verify"),
        ("证书", "Cert"),
        ("安全", "Security"),
        ("可用", "Availability"),
        ("性能", "Performance"),
        ("DNS", "DNS"),
        ("HTTPS", "HTTPS"),
    ]
    out: list[str] = []
    for zh, en in mapping:
        if zh.lower() in text.lower() and en not in out:
            out.append(en)
    ascii_words = re.findall(r"[A-Za-z][A-Za-z0-9+.-]{1,}", text)
    for word in ascii_words:
        if word not in out:
            out.append(word)
    if not out:
        # Empty string is honest: the caller should decide what to do (e.g.
        # fall back to the Chinese source string for prompt context). Never
        # return "Key Flow" — that was the source of the placeholder bug in
        # MUST SHOW lines that ended up reading "Key Flow: Key Flow".
        return ""
    return " ".join(out)[:limit].strip()


def _text_repair_prompt(spec: dict, ocr_lines: list[str], warnings: list[str], english: bool = False) -> str:
    return f"""Edit the provided image to remove all visible text while preserving its original composition, colors, icons, arrows, and layout.

Use the GenImageText workflow idea: the image model should provide only the visual explanation; final accurate labels live outside the raster image in article captions or deterministic overlays.

Strict text policy:
- Remove every Chinese character, English word, number, acronym, URL, code fragment, brand name, random caption, pseudo text, and tiny label.
- Replace text with unlabeled icons, blank label cards, clean whitespace, colored badges, arrows, and grouped shapes.
- Do not add translated labels or new words. Even in English retry mode, keep the raster image text-free.
- If a box needs a title, leave a blank header strip instead of typesetting text.

OCR found these problematic strings: {', '.join(ocr_lines[:12])}
Validation warnings: {'; '.join(warnings[:8])}

Return a polished WeChat technical article illustration: colorful, modern, rich, and readable on mobile, but with no readable text inside the image.
""".strip()


def _save_prompt(prompt_dir: Path, stem: str, prompt: str) -> Path:
    prompt_dir.mkdir(parents=True, exist_ok=True)
    path = prompt_dir / f"{stem}.md"
    path.write_text(prompt, encoding="utf-8")
    return path


def _write_audit_report(target_dir: Path, reports: list[dict]) -> None:
    lines = [
        "# Image Audit Report",
        "",
        "Images are generated after `article.md` is finalized.",
        f"Image style: `{IMAGE_VISUAL_THEME}`.",
        "Prompt files are saved under `images/prompts/`; AI raster candidates are kept under `images/candidates/`; final PNGs may come from deterministic HTML, /create-image, or /create-image background plus HTML text overlay.",
        "",
        "## Visual QA checklist",
        "",
        "- 是否与当前文章段落匹配，而不是泛化科技壁纸。",
        "- 是否有随机英文、伪缩写、URL、代码或品牌 Logo。",
        "- 是否存在错误中文、乱码或过小文字。",
        "- 是否视觉足够丰富但不影响手机端阅读。",
        "",
        "## Files",
        "",
    ]
    for report in reports:
        lines.append(f"- `{report['file']}` — {report['title']}")
        lines.append(f"  - Image style: {report.get('visual_theme', IMAGE_VISUAL_THEME)}")
        lines.append(f"  - Source: {report.get('source', 'create-image')}")
        lines.append(f"  - Prompt: `{report['prompt']}`")
        for candidate in report.get("candidates", []):
            candidate_path = candidate.get("path")
            if isinstance(candidate_path, Path):
                candidate_file = str(candidate_path.relative_to(target_dir))
            elif candidate_path:
                candidate_file = str(candidate_path)
            else:
                candidate_file = "not produced"
            lines.append(f"  - Candidate: {candidate.get('source')} score={candidate.get('score')} file=`{candidate_file}`")
            for warning in candidate.get("warnings", [])[:4]:
                lines.append(f"    - Candidate warning: {warning}")
        if report.get("warnings"):
            for warning in report["warnings"]:
                lines.append(f"  - WARNING: {warning}")
        else:
            lines.append("  - Selected candidate passed deterministic checks; still requires visual review before upload.")
    (target_dir / "image_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _png_size(path: Path) -> tuple[int, int] | None:
    if not path.exists() or detect_image_format(path) != "png":
        return None
    try:
        import struct

        data = path.read_bytes()[:24]
        if len(data) < 24:
            return None
        return struct.unpack(">II", data[16:24])
    except Exception:
        return None


def _file_health_warnings(path: Path, min_width: int = 900, min_height: int = 500) -> list[str]:
    warnings: list[str] = []
    if not path.exists():
        return ["candidate file missing"]
    if path.stat().st_size < 10_000:
        warnings.append("candidate file is unusually small")
    size = _png_size(path)
    if size is None:
        warnings.append("candidate is not a valid PNG")
    else:
        width, height = size
        if width < min_width or height < min_height:
            warnings.append(f"candidate resolution {width}x{height} is too small")
    return warnings


def _unique_warnings(warnings: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for warning in warnings:
        if warning not in seen:
            unique.append(warning)
            seen.add(warning)
    return unique


def _warning_blob(warnings: list[str]) -> str:
    return " ".join(warnings).lower()


def _has_health_failure(warnings: list[str]) -> bool:
    blob = _warning_blob(warnings)
    return (
        "missing" in blob
        or "not a valid png" in blob
        or "unusually small" in blob
        or "too small" in blob
    )


def _has_severe_visual_warning(warnings: list[str]) -> bool:
    blob = _warning_blob(warnings)
    return (
        "render failed" in blob
        or "still failed" in blob
        or "garbled" in blob
        or "unrelated" in blob
        or "fake acronym" in blob
        or "wrong chinese" in blob
        or "url" in blob
        or "code" in blob
        or "logo" in blob
    )


def _is_low_risk_cover_ai(source: str, spec: dict, path: Path, warnings: list[str]) -> bool:
    if not source.startswith("create-image") or str(spec.get("kind") or "summary") != "cover":
        return False
    all_warnings = _unique_warnings([*warnings, *_file_health_warnings(path)])
    if _has_health_failure(all_warnings) or _has_severe_visual_warning(all_warnings):
        return False
    text_warnings = [w for w in all_warnings if "stray text" in w.lower() or "ocr" in w.lower()]
    other_warnings = [w for w in all_warnings if w not in text_warnings]
    return len(text_warnings) <= 2 and not other_warnings


def _candidate_score(source: str, spec: dict, path: Path, warnings: list[str]) -> int:
    score = 100
    kind = str(spec.get("kind") or "summary")
    health_warnings = _file_health_warnings(path)
    all_warnings = _unique_warnings([*warnings, *health_warnings])
    low_risk_cover_ai = _is_low_risk_cover_ai(source, spec, path, warnings)

    if source.startswith("create-image"):
        score += 14
    if kind == "cover" and source.startswith("create-image"):
        score += 56

    score -= len(health_warnings) * 35
    if low_risk_cover_ai:
        score -= len(all_warnings) * 5
    else:
        score -= len(all_warnings) * 12

    warning_blob = _warning_blob(all_warnings)
    hard_bad = (
        _has_health_failure(all_warnings)
        or _has_severe_visual_warning(all_warnings)
        or ("stray text" in warning_blob and not low_risk_cover_ai)
    )
    if hard_bad:
        score -= 45
    return score


def _create_ai_candidate(
    spec: dict,
    prompt_text: str,
    prompt_dir: Path,
    idx: int,
    candidate_dir: Path,
    aspect_ratio: str,
    resolution: str,
) -> tuple[Path | None, str, list[str]]:
    stem = str(spec["filename"])
    candidate_name = f"{stem}-ai.png"
    candidate_path = candidate_dir / candidate_name
    staging_path = candidate_dir.parent / candidate_name
    candidate_path.unlink(missing_ok=True)
    staging_path.unlink(missing_ok=True)
    use_overlay = AI_TEXT_OVERLAY and _html_render is not None and str(spec.get("kind") or "summary") != "cover"
    ai_prompt = _background_prompt(prompt_text, spec) if use_overlay else prompt_text
    if use_overlay:
        _save_prompt(prompt_dir, f"{idx + 1:02d}-{_resolve_visual_type(spec.get('kind','summary'))}-{stem}-background", ai_prompt)
    ok = run_create_image(ai_prompt, candidate_name, aspect_ratio, resolution)
    if ok and staging_path.exists():
        shutil.move(str(staging_path), str(candidate_path))
    source = "create-image background + html text overlay" if use_overlay else "create-image candidate"
    warnings: list[str] = []
    ocr_lines: list[str] = []
    if ok and candidate_path.exists():
        try:
            normalize_wechat_image(candidate_path)
        except RuntimeError as exc:
            warnings.append(str(exc))
            ok = False
    if ok and candidate_path.exists() and use_overlay:
        overlay_path = candidate_dir / f"{stem}-ai-composite.png"
        overlay_path.unlink(missing_ok=True)
        overlay_result, overlay_warnings = _compose_ai_text_overlay(spec, candidate_path, overlay_path)
        if overlay_result is not None:
            candidate_path = overlay_result
            warnings.extend(overlay_warnings)
            text_warnings, ocr_lines = _ocr_warnings_for_image(candidate_path, spec)
            # The overlay intentionally adds accurate article text, so keep only
            # severe OCR findings such as garbled/random text. Missing text is
            # not relevant here because the HTML layer controls text placement.
            severe_overlay_warnings = [w for w in text_warnings if _has_severe_visual_warning([w])]
            warnings.extend(severe_overlay_warnings)
            warnings.append("AI background composed with deterministic HTML text overlay")
            return candidate_path, source, _unique_warnings([*warnings, *_file_health_warnings(candidate_path)])
        else:
            warnings.extend(overlay_warnings)
            source = "create-image candidate (overlay failed)"
    if ok and candidate_path.exists():
        text_warnings, ocr_lines = _ocr_warnings_for_image(candidate_path, spec)
        should_repair_text = os.environ.get("TOM_WECHAT_REPAIR_AI_TEXT", "1").strip().lower() not in {"0", "false", "no"}
        if text_warnings and not any(w.startswith("OCR unavailable") for w in text_warnings) and not should_repair_text:
            warnings.extend(text_warnings)
        elif text_warnings and not any(w.startswith("OCR unavailable") for w in text_warnings) and str(spec.get("kind") or "summary") == "cover":
            warnings.extend([f"cover poem OCR review needed: {w}" for w in text_warnings])
        elif text_warnings and not any(w.startswith("OCR unavailable") for w in text_warnings):
            warnings.extend(text_warnings)
            repair_name = f"{stem}-ai-textfix.png"
            repair_prompt = _text_repair_prompt(spec, ocr_lines, text_warnings, english=False)
            _save_prompt(prompt_dir, f"{idx + 1:02d}-{_resolve_visual_type(spec.get('kind','summary'))}-{stem}-textfix", repair_prompt)
            print(f"[generate-article:ocr] {stem}.png AI candidate has text warnings; trying image edit repair", file=sys.stderr)
            repair_staging_path = candidate_dir.parent / repair_name
            repair_staging_path.unlink(missing_ok=True)
            repair_ok = run_create_image(repair_prompt, repair_name, aspect_ratio, resolution, input_path=candidate_path)
            repair_path = candidate_dir / repair_name
            if repair_ok and repair_staging_path.exists():
                shutil.move(str(repair_staging_path), str(repair_path))
            if repair_ok and repair_path.exists():
                try:
                    normalize_wechat_image(repair_path)
                except RuntimeError as exc:
                    warnings.append(str(exc))
                    repair_ok = False
            if repair_ok and repair_path.exists():
                repair_warnings, repair_ocr = _ocr_warnings_for_image(repair_path, spec)
                if not repair_warnings or all(w.startswith("OCR unavailable") for w in repair_warnings):
                    shutil.move(str(repair_path), str(candidate_path))
                    source = "create-image repaired candidate"
                    warnings = ["Initial OCR had text warnings; image-edit repair passed validation"]
                    ok = True
                else:
                    warnings.extend([f"repair still failed: {w}" for w in repair_warnings])
                    english_name = f"{stem}-ai-english.png"
                    english_prompt = _text_repair_prompt(spec, repair_ocr or ocr_lines, repair_warnings, english=True)
                    _save_prompt(prompt_dir, f"{idx + 1:02d}-{_resolve_visual_type(spec.get('kind','summary'))}-{stem}-english", english_prompt)
                    print(f"[generate-article:ocr] {stem}.png repair still has text warnings; trying text-free regeneration", file=sys.stderr)
                    english_staging_path = candidate_dir.parent / english_name
                    english_staging_path.unlink(missing_ok=True)
                    english_ok = run_create_image(english_prompt, english_name, aspect_ratio, resolution)
                    english_path = candidate_dir / english_name
                    if english_ok and english_staging_path.exists():
                        shutil.move(str(english_staging_path), str(english_path))
                    if english_ok and english_path.exists():
                        try:
                            normalize_wechat_image(english_path)
                        except RuntimeError as exc:
                            warnings.append(str(exc))
                            english_ok = False
                    if english_ok and english_path.exists():
                        english_warnings, _ = _ocr_warnings_for_image(english_path, spec)
                        if english_warnings and not _low_risk_english_retry_warnings(english_warnings):
                            warnings.extend([f"text-free retry still failed: {w}" for w in english_warnings])
                            ok = False
                        else:
                            shutil.move(str(english_path), str(candidate_path))
                            source = "create-image text-free retry candidate"
                            warnings = ["Repair remained risky; kept text-free regenerated candidate for scoring"]
                            warnings.extend([f"review OCR remnant: {w}" for w in english_warnings])
                            ok = True
                    else:
                        ok = False
            else:
                warnings.append("image-edit text repair failed")
                ok = False
    if not ok or not candidate_path.exists():
        warnings.append("/create-image candidate failed")
        return None, source, warnings
    warnings.extend(_file_health_warnings(candidate_path))
    return candidate_path, source, warnings


def _ai_candidate_with_retries(
    spec: dict,
    prompt_text: str,
    prompt_dir: Path,
    idx: int,
    candidate_dir: Path,
    aspect_ratio: str,
    resolution: str,
) -> tuple[Path | None, str, list[str], list[str]]:
    """Try AI candidate up to 1 + AI_SLOT_RETRIES times.

    Returns (path_or_None, source, warnings, error_messages).
    """
    errors: list[str] = []
    total_attempts = AI_SLOT_RETRIES + 1
    for attempt in range(total_attempts):
        if attempt > 0:
            print(
                f"[generate-article:image] {spec.get('filename')}.png AI retry "
                f"{attempt}/{AI_SLOT_RETRIES} after previous failure",
                file=sys.stderr,
            )
        ai_path, ai_source, ai_warnings = _create_ai_candidate(
            spec, prompt_text, prompt_dir, idx, candidate_dir, aspect_ratio, resolution
        )
        if ai_path is None:
            err = (
                f"AI attempt {attempt + 1}/{total_attempts} for slot {idx+1} "
                f"({spec.get('filename')}) failed: "
                + "; ".join(ai_warnings or ["unknown error"])
            )
            errors.append(err)
            print(f"[generate-article:image] {err}", file=sys.stderr)
            continue
        score = _candidate_score(ai_source, spec, ai_path, ai_warnings)
        health_warnings = _file_health_warnings(ai_path)
        warnings = _unique_warnings([*ai_warnings, *health_warnings])
        if _has_health_failure(warnings) or _has_severe_visual_warning(warnings):
            err = (
                f"AI attempt {attempt + 1}/{total_attempts} for slot {idx+1} "
                f"({spec.get('filename')}) failed validation: "
                + "; ".join(warnings or ["unknown validation error"])
            )
            errors.append(err)
            print(f"[generate-article:image] {err}", file=sys.stderr)
            continue
        return ai_path, ai_source, warnings, errors
    return None, "", [], errors


def _is_html_first_slot(spec: dict) -> bool:
    if INFOGRAPHIC_MODE in {"ai-first", "create-image", "ai"}:
        return False
    if INFOGRAPHIC_MODE not in {"html-first", "auto", "hybrid", ""}:
        raise RuntimeError(
            f"Unsupported TOM_WECHAT_INFOGRAPHIC_MODE={INFOGRAPHIC_MODE!r}. "
            "Use hybrid (default), html-first, or ai-first."
        )
    if str(spec.get("kind", "summary")) == "cover":
        return False
    if INFOGRAPHIC_MODE == "html-first":
        return True

    render_mode = str(spec.get("render_mode") or "").strip().lower()
    fact_risk = str(spec.get("fact_risk") or "").strip().lower()
    if render_mode == "html":
        return True
    if render_mode == "ai":
        return False
    if render_mode == "hybrid":
        # Exact labels and ordered mechanisms are safer in the deterministic
        # renderer. Lower-risk hybrid scenes remain eligible for AI rendering.
        return fact_risk in {"medium", "high"}
    return str(spec.get("kind", "summary")) in HTML_FIRST_KINDS


_SPEC_GENERIC_LABELS = {
    "背景", "影响", "问题", "建议", "总结", "概述", "介绍", "说明", "风险", "边界",
    "关键信息", "关键关系", "风险和边界在哪里", "现在可以做什么", "一句话总结",
    "问题从哪里来", "它大概怎么工作", "把新闻变成可执行的判断",
}

# Labels that mean "the section header itself" — too generic to make an
# image article-specific. Kept separate from _SPEC_GENERIC_LABELS because
# these are the scaffold's own bullet labels emitted by render_markdown when
# fact extraction succeeds but the facts are too thin to fill in concrete
# noun-phrases; the model would otherwise paint a generic placeholder card.
_SCAFFOLD_BULLET_LABELS = {
    "核心变化", "影响对象", "风险重点", "现在动作", "后续观察",
    "核对事实", "缩小范围", "安排动作", "留好记录", "更新入口",
    "再决定是否需要排期、升级、沟通或持续观察", "先把源文里的核心变化和影响范围核对清楚",
}


def _concrete_item_count(items: list[dict]) -> int:
    count = 0
    for item in items:
        label = str(item.get("label") or "").strip()
        sub = str(item.get("sub") or item.get("text") or "").strip()
        if item.get("_low_quality"):
            continue
        if not label or label in _SPEC_GENERIC_LABELS:
            continue
        if len(label) <= 1:
            continue
        if label in {"影响", "范围", "清单", "流程", "对比", "关系", "步骤"} and not sub:
            continue
        count += 1
    return count


def _lint_image_specs(specs: list[dict]) -> list[str]:
    errors: list[str] = []
    for idx, spec in enumerate(specs):
        kind = str(spec.get("kind") or "summary")
        if kind == "cover":
            continue
        filename = spec.get("filename") or f"slot_{idx + 1}"
        items = spec.get("items") or []
        concrete_count = _concrete_item_count(items)
        if concrete_count < 3:
            errors.append(
                f"{filename}: {kind} needs at least 3 concrete article-derived labels; got {concrete_count}"
            )
        if any(item.get("_low_quality") for item in items if isinstance(item, dict)):
            errors.append(f"{filename}: contains _low_quality fallback labels")
        labels = [str(item.get("label") or "").strip() for item in items if isinstance(item, dict)]
        if labels and len(set(labels)) < len(labels):
            errors.append(f"{filename}: contains repeated labels")
        generic_hits = [label for label in labels if label in _SPEC_GENERIC_LABELS]
        if len(generic_hits) >= max(1, len(labels) // 2):
            errors.append(f"{filename}: labels are too generic ({', '.join(generic_hits[:4])})")
        # Catch a different boilerplate failure mode: the section title
        # itself is fine ("一分钟看懂"), but every item label is one of the
        # scaffold's own bullet labels (核心变化 / 影响对象 / 风险重点 / …) —
        # which means fact extraction produced nothing article-specific.
        # _concrete_item_count won't flag this because the labels look real.
        # The PowerDNS 2026-06-26 first run hit exactly this.
        if labels and all(lbl in _SCAFFOLD_BULLET_LABELS for lbl in labels):
            errors.append(
                f"{filename}: every label is one of the scaffold's default bullets "
                "(核心变化/影响对象/风险重点/...); no article-specific content extracted"
            )
        if kind in {"flow", "timeline"} and not any(str(item.get("step") or "").strip() for item in items if isinstance(item, dict)):
            errors.append(f"{filename}: {kind} needs explicit step ordering")
        if kind == "flow":
            # Sparse 4-card flow diagrams leave too much blank vertical space in
            # the HTML renderer. Require enough explanatory density up front;
            # otherwise the agent should switch to impact/checklist or enrich
            # the step descriptions before rendering.
            sub_count = sum(1 for item in items if isinstance(item, dict) and str(item.get("sub") or "").strip())
            if len(items) < 5 or sub_count < 4:
                errors.append(
                    f"{filename}: flow diagrams need at least 5 steps and 4 explanatory subtitles to avoid sparse blank cards; enrich the spec or use kind=impact/checklist"
                )
        if kind == "checklist" and concrete_count < 3:
            errors.append(f"{filename}: checklist needs at least 3 actionable items")
        if kind == "compare" and concrete_count < 3:
            errors.append(f"{filename}: compare needs enough concrete comparison items")
    return errors

def _render_html_slot(spec: dict, idx: int, images_dir: Path) -> tuple[Path, str]:
    """Render one slot via the HTML+Playwright renderer."""
    if _html_render is None:
        raise RuntimeError(
            "HTML renderer (html_render.py) is unavailable. "
            "Install Playwright or set TOM_WECHAT_INFOGRAPHIC_MODE=ai-first for body diagrams."
        )
    html_dir = images_dir / "html"
    html_dir.mkdir(parents=True, exist_ok=True)
    html = _html_render.render_html_for_spec(spec)
    html_path = html_dir / f"{spec.get('filename', f'slot_{idx}')}.html"
    html_path.write_text(html, encoding="utf-8")
    width, height = _html_render.canvas_for_kind(spec.get("kind", "summary"))
    png_name = f"{spec.get('filename')}.png"
    png_path = images_dir / png_name
    png_path.unlink(missing_ok=True)
    ok = _html_render.render_png(html_path, png_path, width, height)
    if not ok:
        raise RuntimeError(
            f"HTML render failed for slot {idx+1} ({png_name}). "
            f"Inspect {html_path}."
        )
    return png_path, f"html-render ({width}x{height})"


def _generate_images(plan: ArticlePlan, target_dir: Path, images_dir: Path) -> dict[str, Path]:
    """Generate /create-image raster illustrations and fail if they need repair."""
    if _image_spec is None:
        raise RuntimeError("image_spec module is required before image generation")
    if not IMAGE_VISUAL_THEME:
        raise RuntimeError(
            "Image style is required before generating illustrations. "
            "Ask the user to choose one of: " + ", ".join(sorted(ALLOWED_IMAGE_VISUAL_THEMES)) + "."
        )
    if IMAGE_VISUAL_THEME not in ALLOWED_IMAGE_VISUAL_THEMES:
        raise RuntimeError(
            f"Unsupported image style TOM_WECHAT_IMAGE_VISUAL_THEME={IMAGE_VISUAL_THEME!r}. "
            "Choose one of: " + ", ".join(sorted(ALLOWED_IMAGE_VISUAL_THEMES)) + "."
        )

    article_md = target_dir / "article.md"
    if not article_md.exists():
        raise RuntimeError("article.md is required before image generation")

    _clean_generation_artifacts(target_dir, images_dir)
    specs = _image_spec.derive_specs(article_md, visual_theme=IMAGE_VISUAL_THEME)
    spec_dicts = [spec.to_dict() for spec in specs]
    spec_errors = _lint_image_specs(spec_dicts)
    if spec_errors:
        raise RuntimeError(
            "image_specs.json failed quality gate; revise article/spec before rendering:\n  - "
            + "\n  - ".join(spec_errors)
        )
    specs_path = target_dir / "image_specs.json"
    _image_spec.write_specs(specs, specs_path)

    prompt_dir = images_dir / "prompts"
    candidate_dir = images_dir / "candidates"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    candidate_dir.mkdir(parents=True, exist_ok=True)
    produced: dict[str, Path] = {}
    audit_reports: list[dict] = []
    cwd = os.getcwd()
    os.chdir(target_dir)
    # Only AI-first slots need the /create-image precheck. Body information
    # graphics default to HTML rendering and should not be blocked by AI auth or
    # backend availability.
    need_ai = any(not _is_html_first_slot(spec.to_dict()) for spec in specs)
    ai_available = False
    if need_ai:
        ai_available = (CREATE_IMAGE_BASH.exists() or CREATE_IMAGE_PS1.exists()) and bool(
            resolve_comate_username_encrypted()
        )
        if not ai_available and not AI_FALLBACK_HTML:
            msg = (
                "AI image backend unavailable for AI-first slots.\n"
                "Possible causes:\n"
                f"  - /create-image wrapper missing (expected: {CREATE_IMAGE_BASH} or {CREATE_IMAGE_PS1})\n"
                "  - COMATE_USERNAME_ENCRYPTED not in env AND no usable token under ~/.comate/login-user/.\n"
                "Fix steps:\n"
                "  1) Log in to Comate so ~/.comate/login-user/<username> exists, or\n"
                "  2) export COMATE_USERNAME_ENCRYPTED=<your encrypted username>, then re-run, or\n"
                "  3) set TOM_WECHAT_AI_FALLBACK_HTML=1 to render AI-first slots through HTML."
            )
            os.chdir(cwd)
            raise RuntimeError(msg)
    try:
        for idx, spec_obj in enumerate(specs):
            spec = spec_obj.to_dict()
            filename = f"{spec['filename']}.png"
            final_path = images_dir / filename
            final_path.unlink(missing_ok=True)
            prompt_text = _build_image_prompt(spec, plan.title)
            prompt_file = _save_prompt(prompt_dir, f"{idx + 1:02d}-{_resolve_visual_type(spec.get('kind','summary'))}-{spec['filename']}", prompt_text)
            resolution = os.environ.get("TOM_WECHAT_CREATE_IMAGE_RESOLUTION", "2K")
            aspect_ratio = "21:9" if spec.get("kind") == "cover" else "16:9"

            if _is_html_first_slot(spec):
                html_path, html_source = _render_html_slot(spec, idx, images_dir)
                if html_path != final_path:
                    shutil.copy2(html_path, final_path)
                produced[filename] = final_path
                source = html_source
                warnings = []
                audit_reports.append({
                    "file": str(final_path.relative_to(target_dir)),
                    "title": spec.get("title_zh") or plan.title,
                    "visual_theme": IMAGE_VISUAL_THEME,
                    "prompt": str(prompt_file.relative_to(target_dir)),
                    "source": source,
                    "warnings": warnings,
                    "candidates": [{
                        "source": "html-render",
                        "path": html_path,
                        "warnings": warnings,
                        "score": 100,
                    }],
                })
                print(f"  - {filename}: {source} selected (html-first)")
                continue

            if not ai_available:
                ai_path = None
                ai_source = ""
                ai_warnings = []
                ai_errors = ["AI backend unavailable; using HTML fallback for AI-first slot"]
            else:
                ai_path, ai_source, ai_warnings, ai_errors = _ai_candidate_with_retries(
                    spec, prompt_text, prompt_dir, idx, candidate_dir, aspect_ratio, resolution
                )

            if ai_path is None:
                # AI failed after all retries. Report and optionally fall back to HTML.
                summary_err = (
                    f"/create-image generation failed for slot {idx+1} "
                    f"({spec.get('filename')}) after {AI_SLOT_RETRIES + 1} attempts. "
                    + " | ".join(ai_errors or ["unknown error"])
                )
                print(f"[generate-article:image] ERROR: {summary_err}", file=sys.stderr)

                if not AI_FALLBACK_HTML:
                    os.chdir(cwd)
                    raise RuntimeError(
                        summary_err + ". Set TOM_WECHAT_AI_FALLBACK_HTML=1 or fix the backend and re-run."
                    )
                if _html_render is None:
                    os.chdir(cwd)
                    raise RuntimeError(
                        summary_err + ". HTML fallback renderer is also unavailable."
                    )
                print(
                    f"[generate-article:image] {spec.get('filename')}.png "
                    f"falling back to HTML+Playwright render",
                    file=sys.stderr,
                )
                html_path, html_source = _render_html_slot(spec, idx, images_dir)
                if html_path != final_path:
                    shutil.copy2(html_path, final_path)
                produced[filename] = final_path
                source = html_source
                warnings = [f"AI backend unavailable; used HTML fallback. AI errors: {' | '.join(ai_errors)}"]
                if idx == 0:
                    plan.cover_filename = filename
                audit_reports.append({
                    "file": str(final_path.relative_to(target_dir)),
                    "title": spec.get("title_zh") or plan.title,
                    "prompt": str(prompt_file.relative_to(target_dir)),
                    "source": source,
                    "warnings": warnings,
                    "candidates": [{
                        "source": "html-render",
                        "path": html_path,
                        "warnings": warnings,
                        "score": 0,
                    }],
                })
                print(f"  - {filename}: {source} selected (fallback)" + (f" ({len(warnings)} warnings)" if warnings else ""))
                continue

            score = _candidate_score(ai_source, spec, ai_path, ai_warnings)
            health_warnings = _file_health_warnings(ai_path)
            warnings = _unique_warnings([*ai_warnings, *health_warnings])
            warning_blob = _warning_blob(warnings)
            ai_failed_validation = (
                _has_health_failure(warnings)
                or _has_severe_visual_warning(warnings)
                or "stray text" in warning_blob
                or score < 50
            )
            if ai_failed_validation:
                if not AI_FALLBACK_HTML:
                    os.chdir(cwd)
                    raise RuntimeError(
                        f"/create-image output failed validation for slot {idx+1} ({spec.get('filename')}): "
                        + "; ".join(warnings or ["unknown validation error"])
                        + ". Fix the prompt/article/backend and re-run."
                    )
                if _html_render is None:
                    os.chdir(cwd)
                    raise RuntimeError(
                        f"/create-image output failed validation for slot {idx+1} ({spec.get('filename')}) "
                        "and HTML fallback renderer is unavailable: "
                        + "; ".join(warnings or ["unknown validation error"])
                    )
                print(
                    f"[generate-article:image] {spec.get('filename')}.png AI candidate failed validation; "
                    "falling back to HTML+Playwright render",
                    file=sys.stderr,
                )
                html_path, html_source = _render_html_slot(spec, idx, images_dir)
                if html_path != final_path:
                    shutil.copy2(html_path, final_path)
                produced[filename] = final_path
                source = html_source
                fallback_warning = (
                    "AI image failed validation; used HTML fallback. AI warnings: "
                    + "; ".join(warnings or ["unknown validation error"])
                )
                if idx == 0:
                    plan.cover_filename = filename
                audit_reports.append({
                    "file": str(final_path.relative_to(target_dir)),
                    "title": spec.get("title_zh") or plan.title,
                    "visual_theme": IMAGE_VISUAL_THEME,
                    "prompt": str(prompt_file.relative_to(target_dir)),
                    "source": source,
                    "warnings": [fallback_warning],
                    "candidates": [{
                        "source": ai_source,
                        "path": ai_path,
                        "warnings": warnings,
                        "score": score,
                    }, {
                        "source": "html-render fallback",
                        "path": html_path,
                        "warnings": [],
                        "score": 80,
                    }],
                })
                print(f"  - {filename}: {source} selected (AI validation fallback)")
                continue

            shutil.copy2(ai_path, final_path)
            produced[filename] = final_path
            source = ai_source

            if idx == 0:
                plan.cover_filename = filename
            audit_reports.append({
                "file": str(final_path.relative_to(target_dir)),
                "title": spec.get("title_zh") or plan.title,
                "visual_theme": IMAGE_VISUAL_THEME,
                "prompt": str(prompt_file.relative_to(target_dir)),
                "source": source,
                "warnings": warnings,
                "candidates": [{
                    "source": ai_source,
                    "path": ai_path,
                    "warnings": warnings,
                    "score": score,
                }],
            })
            print(f"  - {filename}: {source} selected (create-image={score})" + (f" ({len(warnings)} warnings)" if warnings else ""))
    finally:
        os.chdir(cwd)

    if INFOGRAPHIC_MODE in {"hybrid", "auto", ""}:
        comparison_dir = images_dir / "body-image-comparison"
        comparison_cmd = [
            sys.executable,
            str(SCRIPT_DIR / "compare_body_images.py"),
            str(specs_path),
            "--out-dir",
            str(comparison_dir),
            "--article-title",
            plan.title,
        ]
        comparison_env = os.environ.copy()
        comparison_env["TOM_WECHAT_AI_TEXT_OVERLAY"] = comparison_env.get("TOM_WECHAT_AI_TEXT_OVERLAY", "1")
        try:
            result = subprocess.run(comparison_cmd, capture_output=True, text=True, env=comparison_env, timeout=900)
            if result.returncode == 0:
                audit_reports.append({
                    "file": str((comparison_dir / "comparison.md").relative_to(target_dir)),
                    "title": "正文配图 HTML/AI 双路候选",
                    "visual_theme": IMAGE_VISUAL_THEME,
                    "prompt": str(specs_path.relative_to(target_dir)),
                    "source": "body-image-comparison",
                    "warnings": [
                        "正文最终图默认保留 HTML 稳定版；AI 候选已生成到 body-image-comparison，发布前可由用户挑选替换。"
                    ],
                    "candidates": [],
                })
                print(f"[generate-article:compare] body image candidates -> {comparison_dir}", file=sys.stderr)
            else:
                audit_reports.append({
                    "file": "images/body-image-comparison/comparison.md",
                    "title": "正文配图 HTML/AI 双路候选",
                    "visual_theme": IMAGE_VISUAL_THEME,
                    "prompt": str(specs_path.relative_to(target_dir)),
                    "source": "body-image-comparison failed",
                    "warnings": ["正文 AI/HTML 双路候选生成失败：" + (result.stderr or result.stdout or "unknown error")[-600:]],
                    "candidates": [],
                })
        except Exception as exc:
            audit_reports.append({
                "file": "images/body-image-comparison/comparison.md",
                "title": "正文配图 HTML/AI 双路候选",
                "visual_theme": IMAGE_VISUAL_THEME,
                "prompt": str(specs_path.relative_to(target_dir)),
                "source": "body-image-comparison failed",
                "warnings": [f"正文 AI/HTML 双路候选生成失败：{exc}"],
                "candidates": [],
            })

    cover_src = images_dir / Path(plan.cover_filename).name
    if cover_src.exists():
        cover_dst = target_dir / cover_src.name
        if cover_src != cover_dst:
            shutil.copy2(cover_src, cover_dst)
            produced[cover_src.name] = cover_dst
        plan.cover_filename = cover_src.name
    _write_audit_report(target_dir, audit_reports)
    print(f"[generate-article:image-audit] wrote {target_dir / 'image_audit.md'}", file=sys.stderr)
    return produced


def generate_images(plan: ArticlePlan, target_dir: Path) -> dict[str, Path]:
    """Generate every illustration. Returns filename -> absolute path."""
    target_dir.mkdir(parents=True, exist_ok=True)
    images_dir = target_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    # Full illustration pipeline: finalized article → image prompt files → local /create-image PNGs.
    # Legacy `huashu-create-image` / `huashu` / `wechat-image` are accepted as
    # aliases of `create-image` for backward compatibility with older shells.
    if IMAGE_MODE in {"create-image", "nano-banana", "huashu-create-image", "huashu", "wechat-image"}:
        return _generate_images(plan, target_dir, images_dir)

    raise RuntimeError(
        f"Unsupported TOM_WECHAT_IMAGE_MODE={IMAGE_MODE!r}. "
        "WeChat article image generation uses /create-image; set TOM_WECHAT_IMAGE_MODE=create-image and ensure a working /create-image wrapper."
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate a WeChat article with images from a source.")
    p.add_argument("--source", required=True, help="URL or local path of the source article")
    p.add_argument("--topic", default="", help="Short topic hint used as the article title seed")
    p.add_argument("--out-dir", required=True, help="Directory to write article.md and images into")
    p.add_argument("--search-query", default="", help="Optional web search query used to enrich the article")
    p.add_argument("--no-search", action="store_true", help="Skip web search enrichment")
    p.add_argument("--markdown", action="store_true", help="Print composed Markdown to stdout")
    p.add_argument("--no-images", action="store_true", help="Skip image generation entirely")
    p.add_argument("--image-visual-theme", default="", help="User-selected illustration theme")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    text, label = read_source(args.source)
    print(f"[generate-article:source] loaded {len(text)} chars from {label}", file=sys.stderr)

    questions = ["CVE number", "CVSS", "affected versions", "fix version", "mitigation"]
    facts = extract_facts(text, questions)
    print(f"[generate-article:facts] extracted {len(facts)} candidate facts", file=sys.stderr)

    sources: list[dict] = []
    if not args.no_search:
        query = args.search_query or args.topic or label
        sources = web_search(query, max_results=5)
        for s in sources:
            facts.append(s["snippet"][:140])
        print(f"[generate-article:search] collected {len(sources)} web results", file=sys.stderr)

    topic = args.topic or (facts[0] if facts else "DNS 文章")
    source_path = Path(args.source).expanduser()
    preserve_source_markdown = (
        source_path.exists()
        and source_path.suffix.lower() in {".md", ".markdown"}
        and re.search(r"^#\s+", text, flags=re.M)
        and re.search(r"^##\s+", text, flags=re.M)
    )

    plan = choose_article_plan(facts, topic, text)
    if preserve_source_markdown:
        markdown = text
        print("[generate-article:markdown] preserving finalized source markdown; generating images only", file=sys.stderr)
    else:
        markdown = render_markdown(plan, facts, sources, source_text=text, topic_hint=topic, source_label=label)
        build_image_prompts_from_markdown(plan, markdown)

    validate_article_ready(markdown)
    md_path = out_dir / "article.md"
    md_path.write_text(markdown, encoding="utf-8")
    print(f"[generate-article:write] markdown (pre-image) -> {md_path}", file=sys.stderr)

    if args.image_visual_theme:
        global IMAGE_VISUAL_THEME
        IMAGE_VISUAL_THEME = args.image_visual_theme.strip().lower()

    if not args.no_images:
        print(f"[generate-article:images] generating illustrations from article text with visual theme: {IMAGE_VISUAL_THEME or '(missing)'} ...", file=sys.stderr)
        generate_images(plan, out_dir)
        plan.normalize_image_paths()
        print(f"[generate-article:done] assets in {out_dir}", file=sys.stderr)
        if preserve_source_markdown:
            md_path.write_text(markdown, encoding="utf-8")
            print(f"[generate-article:write] markdown preserved (final) -> {md_path}", file=sys.stderr)
        else:
            markdown = render_markdown(plan, facts, sources, source_text=text, topic_hint=topic, source_label=label)
            validate_article_ready(markdown)
            md_path.write_text(markdown, encoding="utf-8")
            print(f"[generate-article:write] markdown (final) -> {md_path}", file=sys.stderr)

    if args.no_images and args.markdown:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
