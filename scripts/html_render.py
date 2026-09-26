#!/usr/bin/env python3
"""
html_render.py — HTML-based fallback renderer for WeChat article images.

When the /create-image AI backend is unavailable (500 errors, auth
failures, timeouts), this module produces the same set of PNGs by rendering
HTML with Playwright. It consumes the exact ImageSpec structure produced by
image_spec.derive_specs(), so the Markdown image references stay identical.

Design goals:
  - Text accuracy 100%: every label comes from article-derived specs, no
    image-model hallucination.
  - Pixel-precise layout: fixed canvas sizes per WeChat image type.
  - Dark-mode friendly: medium-tone backgrounds, no pure white/black.
  - Self-contained: only stdlib + Playwright CLI; no extra Python deps.

Canvas mapping:
  - cover       → 1800x766 (2.35:1), center safe zone 766x766
  - summary     → 1920x1080 (16:9)
  - flow        → 1920x1080 (16:9)
  - compare     → 1920x1080 (16:9)
  - checklist   → 1920x1080 (16:9)
  - impact      → 1920x1080 (16:9)
  - timeline    → 1920x1080 (16:9)
  - relations   → 1920x1080 (16:9)
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

# Import palettes from image_spec for color consistency.
PALETTES = [
    {"primary": "#2563eb", "accent": "#0ea5e9", "soft": "#eff6ff", "ink": "#10243f"},
    {"primary": "#0f766e", "accent": "#14b8a6", "soft": "#ecfdf5", "ink": "#0b3b36"},
    {"primary": "#7c3aed", "accent": "#a855f7", "soft": "#f5f3ff", "ink": "#2e1065"},
    {"primary": "#f59e0b", "accent": "#f97316", "soft": "#fff7ed", "ink": "#3f2208"},
    {"primary": "#dc2626", "accent": "#ef4444", "soft": "#fef2f2", "ink": "#3b0a0a"},
]

CANVAS = {
    "cover": (1800, 766),
    "summary": (1920, 1080),
    "flow": (1920, 1080),
    "compare": (1920, 1080),
    "checklist": (1920, 1080),
    "impact": (1920, 1080),
    "timeline": (1920, 1080),
    "relations": (1920, 1080),
}

FONT_STACK = '"Inter", "Noto Sans SC", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Source Han Sans SC", sans-serif'
FONT_STACK_SERIF = '"Noto Serif SC", "Source Han Serif SC", "Songti SC", "STSong", "PingFang SC", serif'
FONT_STACK_MONO = '"JetBrains Mono", "Fira Code", "SF Mono", "Menlo", "Consolas", monospace'

# Webfonts loaded into every template's <head>. Playwright caches them after
# the first article, so this only pays a one-time download cost. Fallback
# chains above keep templates legible if the fetch fails (offline / CI).
WEBFONT_LINK = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?'
    'family=Inter:wght@400;600;700;800;900'
    '&family=Noto+Sans+SC:wght@400;500;700;900'
    '&family=Noto+Serif+SC:wght@600;700;900'
    '&family=JetBrains+Mono:wght@400;500;700'
    '&display=swap" rel="stylesheet">'
)


def _palette(spec: dict) -> dict:
    theme_palette = spec.get("theme_palette")
    if isinstance(theme_palette, dict) and {"primary", "accent", "soft", "ink"}.issubset(theme_palette):
        return theme_palette
    idx = int(spec.get("palette_index", 0)) % len(PALETTES)
    return PALETTES[idx]


def _esc(text: str) -> str:
    if text is None:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _item_text(item: dict, key: str) -> str:
    return _esc(item.get(key, "") or "")


def _shape_svg(kind: str, primary: str, accent: str, index: int = 0) -> str:
    """Return a compact inline SVG motif used by premium HTML templates."""
    colors = [primary, accent, "#111827", "#ffffff"]
    c1 = colors[index % len(colors)]
    c2 = colors[(index + 1) % len(colors)]
    if kind == "flow":
        return f'''<svg viewBox="0 0 160 92" aria-hidden="true"><defs><linearGradient id="g{index}" x1="0" x2="1"><stop stop-color="{c1}"/><stop offset="1" stop-color="{c2}"/></linearGradient></defs><rect x="8" y="18" width="46" height="32" rx="10" fill="url(#g{index})" opacity=".95"/><rect x="92" y="18" width="54" height="32" rx="10" fill="{c1}" opacity=".18"/><path d="M56 34h31" stroke="{c2}" stroke-width="7" stroke-linecap="round"/><path d="M82 24l14 10-14 10" fill="none" stroke="{c2}" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/><circle cx="42" cy="68" r="8" fill="{c2}"/><circle cx="82" cy="68" r="8" fill="{c1}" opacity=".35"/><circle cx="122" cy="68" r="8" fill="{c2}" opacity=".55"/></svg>'''
    if kind == "shield":
        return f'''<svg viewBox="0 0 160 92" aria-hidden="true"><path d="M80 9l50 18v23c0 30-20 49-50 58-30-9-50-28-50-58V27L80 9z" fill="{c1}" opacity=".12"/><path d="M80 18l35 13v17c0 21-13 35-35 43-22-8-35-22-35-43V31L80 18z" fill="none" stroke="{c1}" stroke-width="7"/><path d="M63 52l12 12 27-30" fill="none" stroke="{c2}" stroke-width="8" stroke-linecap="round" stroke-linejoin="round"/></svg>'''
    if kind == "network":
        return f'''<svg viewBox="0 0 160 92" aria-hidden="true"><path d="M34 45h92M80 24v44M34 45l46-21 46 21-46 23z" fill="none" stroke="{c1}" stroke-width="5" opacity=".55"/><circle cx="34" cy="45" r="14" fill="{c1}"/><circle cx="80" cy="24" r="14" fill="{c2}"/><circle cx="126" cy="45" r="14" fill="{c1}" opacity=".75"/><circle cx="80" cy="68" r="14" fill="{c2}" opacity=".75"/></svg>'''
    return f'''<svg viewBox="0 0 160 92" aria-hidden="true"><rect x="22" y="14" width="116" height="64" rx="18" fill="{c1}" opacity=".12"/><rect x="38" y="30" width="84" height="10" rx="5" fill="{c1}"/><rect x="38" y="52" width="54" height="10" rx="5" fill="{c2}"/><circle cx="118" cy="58" r="17" fill="{c2}" opacity=".28"/></svg>'''


def _kind(spec: dict) -> str:
    return str(spec.get("kind", "summary"))


def _render_cover_dark_gold(spec: dict) -> str:
    # Style B: 暗夜金 — #1A1A2E background, #E2B714 gold accent
    w, h = CANVAS["cover"]
    title = _esc(spec.get("title_zh", ""))
    subtitle = _esc(spec.get("subtitle", ""))
    notes = _esc(spec.get("notes", ""))
    items = spec.get("items", [])[:3]
    chips = "".join(
        f'<span class="chip">{_esc(it.get("label", ""))}</span>' for it in items
    )
    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    width: {w}px; height: {h}px;
    background: #1A1A2E;
    display: flex; flex-direction: column;
    justify-content: center; align-items: center;
    padding: 60px 480px;
    font-family: {FONT_STACK};
    color: #ffffff;
    overflow: hidden;
    position: relative;
  }}
  /* geometric decorative circles */
  .deco-circle-1 {{
    position: absolute; top: -120px; right: -80px;
    width: 500px; height: 500px; border-radius: 50%;
    border: 2px solid rgba(226,183,20,0.15);
    pointer-events: none;
  }}
  .deco-circle-2 {{
    position: absolute; bottom: -160px; left: -100px;
    width: 600px; height: 600px; border-radius: 50%;
    border: 2px solid rgba(226,183,20,0.10);
    pointer-events: none;
  }}
  .deco-dot {{
    position: absolute; top: 60px; left: 60px;
    width: 12px; height: 12px; border-radius: 50%;
    background: #E2B714; opacity: 0.7;
  }}
  .deco-line {{
    position: absolute; bottom: 80px; right: 80px;
    width: 120px; height: 3px; background: #E2B714; opacity: 0.4;
  }}
  .kicker {{
    font-size: 28px; font-weight: 700;
    color: #E2B714;
    letter-spacing: 8px;
    margin-bottom: 32px;
    text-transform: uppercase;
  }}
  .title {{
    font-size: 72px; font-weight: 900;
    color: #ffffff; text-align: center;
    line-height: 1.3; letter-spacing: 2px;
    text-shadow: 0 4px 32px rgba(0,0,0,0.5);
    word-break: normal; overflow-wrap: anywhere;
  }}
  .rule {{
    width: 200px; height: 4px;
    background: linear-gradient(90deg, #E2B714, rgba(226,183,20,0.2));
    margin: 36px auto 28px;
    border-radius: 2px;
  }}
  .subtitle {{
    font-size: 30px; font-weight: 400;
    color: rgba(255,255,255,0.75);
    text-align: center;
    line-height: 1.6;
    letter-spacing: 2px;
  }}
  .chips {{
    display: flex; gap: 16px; flex-wrap: wrap;
    justify-content: center;
    margin-top: 42px;
  }}
  .chip {{
    background: rgba(226,183,20,0.12);
    border: 1.5px solid rgba(226,183,20,0.45);
    color: #E2B714;
    font-size: 24px; font-weight: 600;
    padding: 10px 22px; border-radius: 999px;
    word-break: keep-all;
  }}
</style>
</head>
<body>
  <div class="deco-circle-1"></div>
  <div class="deco-circle-2"></div>
  <div class="deco-dot"></div>
  <div class="deco-line"></div>
  <div class="kicker">DNS Daily · 安全观察</div>
  <div class="title">{title}</div>
  <div class="rule"></div>
  <div class="subtitle">{subtitle or notes}</div>
  {f'<div class="chips">{chips}</div>' if chips else ''}
</body>
</html>"""


def _render_summary_cards(spec: dict) -> str:
    # Style A: 极简专业 — #F5F5F5 bg, #2563eb primary, #0ea5e9 accent
    w, h = CANVAS["summary"]
    title = _esc(spec.get("title_zh", ""))
    subtitle = _esc(spec.get("subtitle", ""))
    items = spec.get("items", [])[:4]
    cards = []
    for i, it in enumerate(items):
        label = _esc(it.get("label", ""))
        sub = _esc(it.get("sub", ""))
        sub_html = f'<div class="card-sub">{sub}</div>' if sub else ""
        accent = "#2563eb" if i % 2 == 0 else "#0ea5e9"
        cards.append(
            f'<div class="card" style="border-left-color:{accent}">'
            f'<div class="card-label">{label}</div>{sub_html}</div>'
        )
    cards_html = "".join(cards)
    subtitle_html = f'<div class="subtitle">{subtitle}</div>' if subtitle else ""
    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    width: {w}px; height: {h}px;
    background: #F5F5F5;
    padding: 72px 96px;
    font-family: {FONT_STACK};
    color: #10243f;
    display: flex; flex-direction: column;
    overflow: hidden;
  }}
  .accent-bar {{
    width: 96px; height: 7px;
    background: #2563eb;
    border-radius: 4px;
    margin-bottom: 24px;
  }}
  .title {{
    font-size: 62px; font-weight: 800;
    color: #10243f;
    line-height: 1.3;
    letter-spacing: 1px;
    margin-bottom: 16px;
    word-break: normal; overflow-wrap: anywhere;
  }}
  .subtitle {{
    font-size: 30px; font-weight: 500;
    color: #2563eb;
    margin-bottom: 40px;
    word-break: normal; overflow-wrap: anywhere;
  }}
  .grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 28px;
    flex: 1;
    min-height: 0;
  }}
  .card {{
    background: #ffffff;
    border-left: 8px solid #2563eb;
    border-radius: 16px;
    padding: 32px 30px;
    display: flex; flex-direction: column;
    justify-content: center;
    box-shadow: 0 4px 18px rgba(37,99,235,0.08);
    min-height: 0; overflow: hidden;
  }}
  .card-label {{
    font-size: 32px; font-weight: 700;
    color: #10243f;
    line-height: 1.45;
    margin-bottom: 10px;
    word-break: normal; overflow-wrap: anywhere;
  }}
  .card-sub {{
    font-size: 26px; font-weight: 500;
    color: #2563eb;
    line-height: 1.5;
    word-break: normal; overflow-wrap: anywhere;
  }}
</style>
</head>
<body>
  <div class="accent-bar"></div>
  <div class="title">{title}</div>
  {subtitle_html}
  <div class="grid">{cards_html}</div>
</body>
</html>"""


def _render_flow_vertical(spec: dict) -> str:
    # Style A: vertical step sequence with connecting lines
    w, h = CANVAS["flow"]
    title = _esc(spec.get("title_zh", ""))
    items = spec.get("items", [])[:5]
    steps = []
    for i, it in enumerate(items):
        step_num = it.get("step") or str(i + 1)
        label = _esc(it.get("label", ""))
        sub = _esc(it.get("sub", ""))
        sub_html = f'<div class="step-sub">{sub}</div>' if sub else ""
        connector = '<div class="connector"><div class="connector-line"></div></div>' if i < len(items) - 1 else ""
        steps.append(
            f'<div class="step-wrap">'
            f'<div class="step">'
            f'<div class="step-num">{step_num}</div>'
            f'<div class="step-body"><div class="step-label">{label}</div>{sub_html}</div>'
            f'</div>{connector}</div>'
        )
    steps_html = "".join(steps)
    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    width: {w}px; height: {h}px;
    background: #F5F5F5;
    padding: 64px 80px;
    font-family: {FONT_STACK};
    color: #10243f;
    display: flex; flex-direction: column;
    overflow: hidden;
  }}
  .accent-bar {{
    width: 96px; height: 7px;
    background: #2563eb;
    border-radius: 4px;
    margin-bottom: 20px;
  }}
  .title {{
    font-size: 56px; font-weight: 800;
    color: #10243f;
    line-height: 1.3;
    margin-bottom: 32px;
    word-break: normal; overflow-wrap: anywhere;
  }}
  .flow-track {{
    display: flex; align-items: flex-start;
    gap: 0;
    flex: 1;
    min-height: 0;
  }}
  .step-wrap {{
    display: flex; align-items: center;
    flex: 1;
  }}
  .step {{
    flex: 1;
    background: #ffffff;
    border-radius: 16px;
    padding: 28px 22px;
    display: flex; flex-direction: column;
    box-shadow: 0 4px 18px rgba(37,99,235,0.09);
    border-top: 6px solid #2563eb;
    min-height: 0; overflow: hidden;
    height: 100%;
    align-self: stretch;
  }}
  .step-num {{
    font-size: 48px; font-weight: 900;
    color: #2563eb;
    margin-bottom: 14px;
    line-height: 1;
  }}
  .step-label {{
    font-size: 27px; font-weight: 700;
    color: #10243f;
    line-height: 1.45;
    margin-bottom: 8px;
    word-break: normal; overflow-wrap: anywhere;
  }}
  .step-sub {{
    font-size: 22px; font-weight: 500;
    color: #2563eb;
    line-height: 1.5;
    word-break: normal; overflow-wrap: anywhere;
  }}
  .connector {{
    width: 36px; flex-shrink: 0;
    display: flex; align-items: center; justify-content: center;
    align-self: center;
  }}
  .connector-line {{
    width: 28px; height: 3px;
    background: linear-gradient(90deg, #2563eb, #0ea5e9);
    position: relative;
  }}
  .connector-line::after {{
    content: '';
    position: absolute; right: -8px; top: -5px;
    border: 6px solid transparent;
    border-left: 10px solid #0ea5e9;
  }}
</style>
</head>
<body>
  <div class="accent-bar"></div>
  <div class="title">{title}</div>
  <div class="flow-track">{steps_html}</div>
</body>
</html>"""


def _render_checklist_minimal(spec: dict) -> str:
    # Style A: 极简专业
    w, h = CANVAS["checklist"]
    title = _esc(spec.get("title_zh", ""))
    items = spec.get("items", [])[:6]
    rows = []
    for i, it in enumerate(items):
        step = it.get("step") or "•"
        label = _esc(it.get("label", ""))
        sub = _esc(it.get("sub", ""))
        sub_html = f'<div class="row-sub">{sub}</div>' if sub else ""
        accent = "#2563eb" if i % 2 == 0 else "#0ea5e9"
        rows.append(
            f'<div class="row">'
            f'<div class="row-num" style="color:{accent}">{step}</div>'
            f'<div class="row-body"><div class="row-label">{label}</div>{sub_html}</div></div>'
        )
    rows_html = "".join(rows)
    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    width: {w}px; height: {h}px;
    background: #F5F5F5;
    padding: 64px 96px;
    font-family: {FONT_STACK};
    color: #10243f;
    display: flex; flex-direction: column;
    overflow: hidden;
  }}
  .accent-bar {{
    width: 96px; height: 7px;
    background: #2563eb;
    border-radius: 4px;
    margin-bottom: 22px;
  }}
  .title {{
    font-size: 58px; font-weight: 800;
    color: #10243f;
    line-height: 1.3;
    margin-bottom: 32px;
    word-break: normal; overflow-wrap: anywhere;
  }}
  .list {{
    display: flex; flex-direction: column;
    gap: 16px;
    flex: 1;
    min-height: 0;
  }}
  .row {{
    display: flex; align-items: flex-start;
    gap: 24px;
    background: #ffffff;
    border-radius: 14px;
    padding: 22px 30px;
    box-shadow: 0 3px 14px rgba(37,99,235,0.07);
    flex-shrink: 0;
  }}
  .row-num {{
    font-size: 40px; font-weight: 900;
    color: #2563eb;
    min-width: 52px;
    line-height: 1;
    padding-top: 2px;
  }}
  .row-label {{
    font-size: 30px; font-weight: 700;
    color: #10243f;
    line-height: 1.45;
    margin-bottom: 4px;
    word-break: normal; overflow-wrap: anywhere;
  }}
  .row-sub {{
    font-size: 24px; font-weight: 500;
    color: #2563eb;
    line-height: 1.5;
    word-break: normal; overflow-wrap: anywhere;
  }}
</style>
</head>
<body>
  <div class="accent-bar"></div>
  <div class="title">{title}</div>
  <div class="list">{rows_html}</div>
</body>
</html>"""


def _render_compare_tagged(spec: dict) -> str:
    """Impact / compare: Style A with 直接影响/隐性危害 tags."""
    w, h = CANVAS["impact"]
    title = _esc(spec.get("title_zh", ""))
    items = spec.get("items", [])[:4]
    cards = []
    for i, it in enumerate(items):
        label = _esc(it.get("label", ""))
        sub = _esc(it.get("sub", ""))
        sub_html = f'<div class="cmp-sub">{sub}</div>' if sub else ""
        _even_colors = ("rgba(37,99,235,0.1)", "#2563eb", "#2563eb")
        _odd_colors  = ("rgba(220,38,38,0.1)", "#dc2626", "#dc2626")
        tag_bg, tag_color, border_color = _even_colors if i % 2 == 0 else _odd_colors
        # Use spec-provided tag if present; otherwise no label text (colour-only distinction).
        tag = _esc(it.get("tag", ""))
        cards.append(
            f'<div class="cmp-card" style="border-top-color:{border_color}">'
            f'<div class="cmp-tag" style="background:{tag_bg};color:{tag_color}">{tag}</div>'
            f'<div class="cmp-label">{label}</div>{sub_html}</div>'
        )
    cards_html = "".join(cards)
    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    width: {w}px; height: {h}px;
    background: #F5F5F5;
    padding: 64px 96px;
    font-family: {FONT_STACK};
    color: #10243f;
    display: flex; flex-direction: column;
    overflow: hidden;
  }}
  .accent-bar {{
    width: 96px; height: 7px;
    background: #2563eb;
    border-radius: 4px;
    margin-bottom: 22px;
  }}
  .title {{
    font-size: 58px; font-weight: 800;
    color: #10243f;
    line-height: 1.3;
    margin-bottom: 36px;
    word-break: normal; overflow-wrap: anywhere;
  }}
  .grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 28px;
    flex: 1;
    min-height: 0;
  }}
  .cmp-card {{
    background: #ffffff;
    border-radius: 16px;
    padding: 30px 28px;
    display: flex; flex-direction: column;
    box-shadow: 0 4px 18px rgba(37,99,235,0.08);
    border-top: 8px solid #2563eb;
    min-height: 0; overflow: hidden;
  }}
  .cmp-tag {{
    display: inline-block;
    font-size: 22px; font-weight: 700;
    padding: 5px 16px; border-radius: 999px;
    align-self: flex-start;
    margin-bottom: 16px;
  }}
  .cmp-label {{
    font-size: 32px; font-weight: 700;
    color: #10243f;
    line-height: 1.45;
    margin-bottom: 10px;
    word-break: normal; overflow-wrap: anywhere;
  }}
  .cmp-sub {{
    font-size: 24px; font-weight: 500;
    color: #2563eb;
    line-height: 1.5;
    word-break: normal; overflow-wrap: anywhere;
  }}
</style>
</head>
<body>
  <div class="accent-bar"></div>
  <div class="title">{title}</div>
  <div class="grid">{cards_html}</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Additional templates (multi-skeleton mix per article)
# ---------------------------------------------------------------------------
# Each template renders the same ImageSpec dict produced by image_spec.py,
# but presents a different visual skeleton. _select_template() below picks
# one per spec based on visual_theme and title content clues, so a 7-image
# WeChat article ends up with 3-4 distinct visual moods instead of 7 copies
# of the same boilerplate.

# === COVER VARIANTS ========================================================


def _render_cover_terminal(spec: dict) -> str:
    """Pseudo-terminal cover for CVE / incident / dig-style DNS articles.

    Renders a chrome-less terminal card with a $ prompt, the article title
    as the typed command, and 2-4 short result lines drawn from spec.items.
    DNS is the canonical example: a `dig` or `whois` output framing makes
    the article feel operational rather than abstract.
    """
    w, h = CANVAS["cover"]
    title = _esc(spec.get("title_zh", ""))
    subtitle = _esc(spec.get("subtitle", "") or spec.get("notes", ""))
    items = spec.get("items", [])[:4]
    out_lines = []
    for it in items:
        label = _esc(it.get("label", ""))
        sub = _esc(it.get("sub", ""))
        if not label and not sub:
            continue
        if sub:
            out_lines.append(f'<div class="ln"><span class="k">›</span> {label} <span class="muted">{sub}</span></div>')
        else:
            out_lines.append(f'<div class="ln"><span class="k">›</span> {label}</div>')
    out_html = "".join(out_lines)
    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">{WEBFONT_LINK}<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{
    width:{w}px; height:{h}px;
    background:#0b1320;
    font-family:{FONT_STACK_MONO};
    color:#e2e8f0;
    overflow:hidden; position:relative;
    padding:0 360px;
    display:flex; align-items:center;
  }}
  body::before {{
    content:''; position:absolute; inset:0;
    background:
      radial-gradient(at 18% 28%, rgba(34,211,238,.18), transparent 45%),
      radial-gradient(at 82% 78%, rgba(239,68,68,.10), transparent 50%);
  }}
  .card {{
    position:relative; width:100%;
    background:rgba(15,23,42,.92);
    border:1px solid rgba(148,163,184,.25);
    border-radius:18px;
    box-shadow:0 30px 80px rgba(0,0,0,.5);
    padding:32px 48px 40px;
  }}
  .chrome {{
    display:flex; gap:8px; margin-bottom:24px;
  }}
  .chrome span {{
    width:14px; height:14px; border-radius:50%;
  }}
  .chrome span:nth-child(1) {{ background:#ef4444; }}
  .chrome span:nth-child(2) {{ background:#f59e0b; }}
  .chrome span:nth-child(3) {{ background:#22c55e; }}
  .prompt {{
    font-size:32px; color:#22d3ee; opacity:.85;
    margin-bottom:10px;
  }}
  .cmd {{
    font-family:{FONT_STACK};
    font-size:56px; font-weight:800;
    color:#f8fafc;
    line-height:1.2; letter-spacing:-.5px;
    word-break: normal; overflow-wrap: anywhere;
    margin-bottom:24px;
  }}
  .out {{
    border-top:1px dashed rgba(148,163,184,.3);
    padding-top:22px;
  }}
  .ln {{
    font-size:24px; line-height:1.7;
    color:#cbd5e1;
    word-break: normal; overflow-wrap: anywhere;
  }}
  .k {{ color:#22d3ee; margin-right:10px; }}
  .muted {{ color:#94a3b8; }}
  .sub {{
    font-family:{FONT_STACK};
    font-size:22px; color:#22d3ee;
    margin-top:24px; letter-spacing:1px;
    text-transform:uppercase;
  }}
</style></head>
<body>
  <div class="card">
    <div class="chrome"><span></span><span></span><span></span></div>
    <div class="prompt">$ dns-daily --query</div>
    <div class="cmd">{title}</div>
    <div class="out">{out_html}</div>
    {f'<div class="sub">{subtitle}</div>' if subtitle else ''}
  </div>
</body></html>"""


def _render_cover_magazine(spec: dict) -> str:
    """Editorial magazine cover: serif headline, kicker, accent rule.

    Reads as a print-style feature spread. Best for deep-analysis, industry
    observation, and warm-narrative articles.
    """
    w, h = CANVAS["cover"]
    title = _esc(spec.get("title_zh", ""))
    subtitle = _esc(spec.get("subtitle", "") or spec.get("notes", ""))
    items = spec.get("items", [])[:3]
    chips = "".join(
        f'<span class="chip">{_esc(it.get("label",""))}</span>' for it in items if it.get("label")
    )
    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">{WEBFONT_LINK}<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{
    width:{w}px; height:{h}px;
    background:#f4ede0;
    color:#1f1b16;
    overflow:hidden; position:relative;
    display:flex; flex-direction:column; justify-content:center;
    padding:0 420px;
    font-family:{FONT_STACK};
  }}
  body::before {{
    content:''; position:absolute; left:60px; top:60px; bottom:60px;
    width:6px; background:#b45309;
  }}
  body::after {{
    content:'';
    position:absolute; right:60px; top:60px; bottom:60px;
    width:6px; background:#b45309; opacity:.4;
  }}
  .kicker {{
    font-size:22px; font-weight:700; letter-spacing:6px;
    color:#b45309; text-transform:uppercase;
    margin-bottom:24px;
  }}
  .title {{
    font-family:{FONT_STACK_SERIF};
    font-size:78px; font-weight:900;
    color:#1f1b16; line-height:1.18;
    letter-spacing:-1px;
    word-break: normal; overflow-wrap: anywhere;
    margin-bottom:24px;
  }}
  .rule {{
    width:140px; height:3px; background:#1f1b16; opacity:.7;
    margin-bottom:24px;
  }}
  .sub {{
    font-size:26px; color:#5c4a2e;
    line-height:1.6; max-width:900px;
    word-break: normal; overflow-wrap: anywhere;
  }}
  .chips {{
    margin-top:28px; display:flex; gap:14px; flex-wrap:wrap;
  }}
  .chip {{
    font-size:20px; font-weight:600;
    color:#b45309;
    border:1.5px solid #b45309;
    padding:6px 16px; border-radius:999px;
    background:rgba(180,83,9,.06);
  }}
</style></head>
<body>
  <div class="kicker">DNS DAILY · FEATURE</div>
  <div class="title">{title}</div>
  <div class="rule"></div>
  {f'<div class="sub">{subtitle}</div>' if subtitle else ''}
  {f'<div class="chips">{chips}</div>' if chips else ''}
</body></html>"""


def _render_cover_grid(spec: dict) -> str:
    """Light grid cover: data-infographic / clean-professional default.

    SVG dot-grid background, oversized Inter title, accent bar, optional
    metric badges built from spec.items[].label.
    """
    w, h = CANVAS["cover"]
    title = _esc(spec.get("title_zh", ""))
    subtitle = _esc(spec.get("subtitle", "") or spec.get("notes", ""))
    items = spec.get("items", [])[:3]
    palette = _palette(spec)
    primary = palette.get("primary", "#2563eb")
    accent = palette.get("accent", "#0ea5e9")
    badges = "".join(
        f'<div class="badge"><div class="b-num">0{i+1}</div><div class="b-lbl">{_esc(it.get("label",""))}</div></div>'
        for i, it in enumerate(items) if it.get("label")
    )
    grid_svg = (
        f"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='40' height='40'%3E"
        f"%3Ccircle cx='1' cy='1' r='1' fill='%23{primary.lstrip('#')}' fill-opacity='0.12'/%3E%3C/svg%3E"
    )
    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">{WEBFONT_LINK}<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{
    width:{w}px; height:{h}px;
    background:#f7f9fc;
    background-image:url("{grid_svg}");
    background-size:40px 40px;
    color:#0f172a;
    font-family:{FONT_STACK};
    overflow:hidden; position:relative;
    padding:0 360px;
    display:flex; flex-direction:column; justify-content:center;
  }}
  body::before {{
    content:''; position:absolute; left:0; top:0; width:8px; height:100%;
    background:linear-gradient(180deg, {primary}, {accent});
  }}
  .kicker {{
    display:flex; align-items:center; gap:14px;
    font-size:22px; font-weight:700; letter-spacing:4px;
    color:{primary}; text-transform:uppercase;
    margin-bottom:22px;
  }}
  .kicker::before {{
    content:''; width:36px; height:3px; background:{primary};
  }}
  .title {{
    font-size:84px; font-weight:900;
    color:#0f172a; line-height:1.16;
    letter-spacing:-1.5px;
    word-break: normal; overflow-wrap: anywhere;
    margin-bottom:22px;
  }}
  .sub {{
    font-size:26px; color:#475569;
    line-height:1.5; max-width:980px;
    word-break: normal; overflow-wrap: anywhere;
    margin-bottom:28px;
  }}
  .badges {{ display:flex; gap:18px; flex-wrap:wrap; }}
  .badge {{
    background:#ffffff;
    border:1px solid rgba(15,23,42,.08);
    border-radius:14px;
    padding:14px 22px;
    box-shadow:0 4px 14px rgba(15,23,42,.06);
    min-width:160px;
  }}
  .b-num {{
    font-family:{FONT_STACK_MONO};
    font-size:18px; font-weight:700;
    color:{accent}; letter-spacing:2px;
    margin-bottom:6px;
  }}
  .b-lbl {{
    font-size:22px; font-weight:700;
    color:#0f172a;
    word-break: normal; overflow-wrap: anywhere;
  }}
</style></head>
<body>
  <div class="kicker">DNS DAILY</div>
  <div class="title">{title}</div>
  {f'<div class="sub">{subtitle}</div>' if subtitle else ''}
  {f'<div class="badges">{badges}</div>' if badges else ''}
</body></html>"""


def _render_cover_whiteboard(spec: dict) -> str:
    """Hand-drawn whiteboard cover for whiteboard-sketch theme.

    Off-white background, single ink accent, dashed underline, single-anchor
    arrow doodle. Best for architecture explanations.
    """
    w, h = CANVAS["cover"]
    title = _esc(spec.get("title_zh", ""))
    subtitle = _esc(spec.get("subtitle", "") or spec.get("notes", ""))
    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">{WEBFONT_LINK}<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{
    width:{w}px; height:{h}px;
    background:#fbfaf5;
    color:#111827;
    font-family:{FONT_STACK};
    overflow:hidden; position:relative;
    padding:0 420px;
    display:flex; flex-direction:column; justify-content:center;
  }}
  body::before {{
    content:''; position:absolute; inset:30px;
    border:3px solid #111827;
    border-radius:24px;
    opacity:.08;
  }}
  .tag {{
    display:inline-block;
    font-family:{FONT_STACK_MONO};
    font-size:20px; font-weight:700;
    background:#dc2626; color:#fff;
    padding:6px 14px; border-radius:6px;
    transform:rotate(-1.5deg);
    align-self:flex-start;
    margin-bottom:24px;
  }}
  .title {{
    font-size:78px; font-weight:900;
    color:#111827; line-height:1.18;
    letter-spacing:-1px;
    word-break: normal; overflow-wrap: anywhere;
    margin-bottom:18px;
  }}
  .underline {{
    width:380px; height:8px;
    background:transparent;
    border-bottom:4px dashed #dc2626;
    margin-bottom:24px;
  }}
  .sub {{
    font-size:28px; color:#374151;
    line-height:1.5; max-width:900px;
    word-break: normal; overflow-wrap: anywhere;
  }}
  .arrow {{
    position:absolute; right:140px; bottom:120px;
    width:160px; height:auto;
  }}
</style></head>
<body>
  <div class="tag">手绘解读</div>
  <div class="title">{title}</div>
  <div class="underline"></div>
  {f'<div class="sub">{subtitle}</div>' if subtitle else ''}
  <svg class="arrow" viewBox="0 0 160 120">
    <path d="M10 90 Q 60 20 130 70" stroke="#111827" stroke-width="4" fill="none" stroke-linecap="round"/>
    <path d="M115 60 L 132 72 L 118 82" stroke="#111827" stroke-width="4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
  </svg>
</body></html>"""


# === BODY VARIANTS =========================================================


def _render_summary_stat_grid(spec: dict) -> str:
    """The Economist-style stat grid: 4 large numbers with captions.

    Used when the data-infographic theme is active or the items carry CVE /
    percentage / count signals.
    """
    w, h = CANVAS["summary"]
    title = _esc(spec.get("title_zh", ""))
    subtitle = _esc(spec.get("subtitle", ""))
    items = spec.get("items", [])[:4]
    palette = _palette(spec)
    primary = palette.get("primary", "#2563eb")
    accent = palette.get("accent", "#dc2626")
    cards = []
    # Stat extraction: percent / count / dotted-version, with explicit
    # exclusion of standalone 4-digit years like 2026 and of digit sequences
    # embedded inside CVE-IDs ("CVE-2026-42005"). We do the year filter
    # post-match because Python regex doesn't support variable-width
    # lookbehinds for the "is this preceded by a hyphen-letter prefix" check.
    _STAT_PATTERN = re.compile(
        r"(\d{1,3}(?:[,.]\d{3})+(?:[\-–]\d+)?%?"   # 58,768 or 12,345.6
        r"|\d{1,3}[\-–]\d{1,3}%"                    # 77-86%
        r"|\d{1,3}%"                                # 50%
        r"|\d+(?:\.\d+){2,}"                        # 4.9.16, 5.2.11 (3+ parts)
        r"|\d+\.\d+"                                # 1.0, 2.5 (2 parts)
        r"|\d{3,})"                                 # 296, 1024 (bare 3+ digit)
    )

    def _pick_stat(text: str) -> str | None:
        for m in _STAT_PATTERN.finditer(text):
            tok = m.group(1)
            # Reject standalone 4-digit years (2000-2099). Allow them inside
            # bigger numbers like "20261" (which matches \d{3,}).
            if re.fullmatch(r"20\d{2}", tok):
                continue
            # Reject digits that look like a CVE-id segment or any other
            # hyphenated id chain ("CVE-2026-42005", "RFC-9167-2"): if the
            # character immediately before the match is "-" and the chars
            # before that are digits/letters, treat this as a continuation
            # token rather than a standalone statistic.
            start = m.start()
            if start > 0 and text[start - 1] in "-–":
                prefix = text[max(0, start - 10):start - 1]
                if re.search(r"[A-Za-z0-9]$", prefix):
                    continue
            return tok
        return None

    for i, it in enumerate(items):
        label = it.get("label", "") or ""
        sub = it.get("sub", "") or ""
        stat = _pick_stat(label) or _pick_stat(sub)
        if stat:
            # Strip the stat token from the label only if it actually appeared
            # there, so we don't end up with truncated body text.
            rest = label.replace(stat, "").strip(" -—:：") if stat in label else label
        else:
            stat = f"{i+1:02d}"
            rest = label
        rest = _esc(rest)
        color = primary if i % 2 == 0 else accent
        stat_sub = f'<div class="stat-sub">{_esc(sub)}</div>' if sub else ""
        cards.append(
            f'<div class="stat-card">'
            f'<div class="stat-num" style="color:{color}">{_esc(stat)}</div>'
            f'<div class="stat-lbl">{rest}</div>'
            f'{stat_sub}</div>'
        )
    cards_html = "".join(cards)
    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">{WEBFONT_LINK}<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{
    width:{w}px; height:{h}px;
    background:#fafafa;
    color:#111827;
    font-family:{FONT_STACK};
    padding:64px 80px;
    display:flex; flex-direction:column; overflow:hidden;
  }}
  .kicker {{
    font-family:{FONT_STACK_MONO};
    font-size:20px; font-weight:700;
    color:{primary}; letter-spacing:3px;
    text-transform:uppercase;
    margin-bottom:14px;
  }}
  .title {{
    font-size:54px; font-weight:900;
    color:#111827; line-height:1.25;
    letter-spacing:-.5px;
    word-break: normal; overflow-wrap: anywhere;
    margin-bottom:8px;
    border-bottom:2px solid #111827;
    padding-bottom:18px;
  }}
  .subtitle {{
    font-size:26px; color:#4b5563;
    margin:18px 0 28px;
    word-break: normal; overflow-wrap: anywhere;
  }}
  .grid {{
    display:grid; grid-template-columns:1fr 1fr; gap:24px;
    flex:1; min-height:0;
  }}
  .stat-card {{
    background:#ffffff;
    border:1px solid rgba(17,24,39,.08);
    border-radius:10px;
    padding:32px 32px;
    display:flex; flex-direction:column;
    box-shadow:0 2px 8px rgba(17,24,39,.04);
    min-height:0; overflow:hidden;
  }}
  .stat-num {{
    font-family:{FONT_STACK_MONO};
    font-size:88px; font-weight:800;
    line-height:1; margin-bottom:14px;
    letter-spacing:-1px;
  }}
  .stat-lbl {{
    font-size:28px; font-weight:700;
    color:#111827; line-height:1.4;
    margin-bottom:8px;
    word-break: normal; overflow-wrap: anywhere;
  }}
  .stat-sub {{
    font-size:22px; color:#6b7280;
    line-height:1.5;
    word-break: normal; overflow-wrap: anywhere;
  }}
</style></head>
<body>
  <div class="kicker">DATA · {(spec.get('source_section') or '').upper()[:32]}</div>
  <div class="title">{title}</div>
  {f'<div class="subtitle">{subtitle}</div>' if subtitle else ''}
  <div class="grid">{cards_html}</div>
</body></html>"""


def _render_summary_left_callout(spec: dict) -> str:
    """Left big-number callout + right body list. Editorial layout."""
    w, h = CANVAS["summary"]
    title = _esc(spec.get("title_zh", ""))
    subtitle = _esc(spec.get("subtitle", ""))
    items = spec.get("items", [])[:4]
    palette = _palette(spec)
    primary = palette.get("primary", "#b45309")
    accent = palette.get("accent", "#1f2937")
    rows = []
    for i, it in enumerate(items):
        label = _esc(it.get("label", ""))
        sub = _esc(it.get("sub", ""))
        sub_html = f'<div class="r-sub">{sub}</div>' if sub else ""
        rows.append(
            f'<div class="row">'
            f'<div class="dot"></div>'
            f'<div class="row-body"><div class="r-label">{label}</div>'
            f'{sub_html}</div></div>'
        )
    rows_html = "".join(rows)
    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">{WEBFONT_LINK}<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{
    width:{w}px; height:{h}px;
    background:#faf6ee;
    color:#1f1b16;
    font-family:{FONT_STACK};
    display:grid; grid-template-columns:38% 1fr;
    overflow:hidden;
  }}
  .left {{
    background:{primary}; color:#fff;
    padding:72px 56px;
    display:flex; flex-direction:column; justify-content:center;
  }}
  .left-kicker {{
    font-family:{FONT_STACK_MONO};
    font-size:18px; letter-spacing:4px;
    text-transform:uppercase;
    opacity:.85;
    margin-bottom:18px;
  }}
  .left-title {{
    font-family:{FONT_STACK_SERIF};
    font-size:56px; font-weight:900;
    line-height:1.2; letter-spacing:-.5px;
    word-break: normal; overflow-wrap: anywhere;
  }}
  .left-sub {{
    font-size:24px; line-height:1.55;
    opacity:.9; margin-top:22px;
    word-break: normal; overflow-wrap: anywhere;
  }}
  .right {{
    padding:72px 64px;
    display:flex; flex-direction:column; justify-content:center;
    gap:24px;
  }}
  .row {{
    display:flex; gap:24px; align-items:flex-start;
  }}
  .dot {{
    width:18px; height:18px; flex-shrink:0;
    background:{primary}; border-radius:50%;
    margin-top:14px;
  }}
  .row-body {{ flex:1; min-width:0; }}
  .r-label {{
    font-size:30px; font-weight:700;
    color:#1f1b16; line-height:1.4;
    word-break: normal; overflow-wrap: anywhere;
    margin-bottom:6px;
  }}
  .r-sub {{
    font-size:22px; color:#5c4a2e;
    line-height:1.55;
    word-break: normal; overflow-wrap: anywhere;
  }}
</style></head>
<body>
  <div class="left">
    <div class="left-kicker">{(spec.get('kind') or 'note').upper()}</div>
    <div class="left-title">{title}</div>
    {f'<div class="left-sub">{subtitle}</div>' if subtitle else ''}
  </div>
  <div class="right">{rows_html}</div>
</body></html>"""


def _render_flow_horizontal(spec: dict) -> str:
    """Horizontal left-to-right step ribbon with chevron connectors."""
    w, h = CANVAS["flow"]
    title = _esc(spec.get("title_zh", ""))
    items = spec.get("items", [])[:5]
    palette = _palette(spec)
    primary = palette.get("primary", "#2563eb")
    accent = palette.get("accent", "#0ea5e9")
    steps = []
    for i, it in enumerate(items):
        step_num = _esc(it.get("step") or str(i + 1))
        label = _esc(it.get("label", ""))
        sub = _esc(it.get("sub", ""))
        connector = '<div class="chev"></div>' if i < len(items) - 1 else ""
        sub_html = f'<div class="step-sub">{sub}</div>' if sub else ""
        steps.append(
            f'<div class="step">'
            f'<div class="step-pill" style="background:{primary}">{step_num}</div>'
            f'<div class="step-label">{label}</div>'
            f'{sub_html}</div>'
            f'{connector}'
        )
    steps_html = "".join(steps)
    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">{WEBFONT_LINK}<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{
    width:{w}px; height:{h}px;
    background:#f5f7fb;
    color:#0f172a;
    font-family:{FONT_STACK};
    padding:80px 80px;
    display:flex; flex-direction:column; overflow:hidden;
  }}
  .title {{
    font-size:54px; font-weight:900;
    color:#0f172a; line-height:1.25;
    letter-spacing:-.5px;
    word-break: normal; overflow-wrap: anywhere;
    margin-bottom:52px;
  }}
  .ribbon {{
    display:flex; align-items:stretch; gap:0;
    flex:1; min-height:0;
  }}
  .step {{
    flex:1;
    background:#fff;
    border-radius:18px;
    padding:36px 28px;
    box-shadow:0 8px 24px rgba(15,23,42,.06);
    display:flex; flex-direction:column;
    min-width:0; overflow:hidden;
  }}
  .step-pill {{
    font-family:{FONT_STACK_MONO};
    font-size:24px; font-weight:800; color:#fff;
    width:54px; height:54px; border-radius:14px;
    display:flex; align-items:center; justify-content:center;
    margin-bottom:24px;
  }}
  .step-label {{
    font-size:28px; font-weight:700;
    color:#0f172a; line-height:1.4;
    margin-bottom:10px;
    word-break: normal; overflow-wrap: anywhere;
  }}
  .step-sub {{
    font-size:22px; color:#475569;
    line-height:1.5;
    word-break: normal; overflow-wrap: anywhere;
  }}
  .chev {{
    width:36px; flex-shrink:0;
    background:linear-gradient(90deg, transparent, {accent});
    clip-path:polygon(0 40%, 60% 40%, 60% 0, 100% 50%, 60% 100%, 60% 60%, 0 60%);
    align-self:center; height:30px;
  }}
</style></head>
<body>
  <div class="title">{title}</div>
  <div class="ribbon">{steps_html}</div>
</body></html>"""


def _render_premium_cards(spec: dict) -> str:
    """Magazine-grade card grid with stronger hierarchy and visual motifs."""
    w, h = CANVAS.get(_kind(spec), CANVAS["summary"])
    title = _esc(spec.get("title_zh", ""))
    subtitle = _esc(spec.get("subtitle", ""))
    items = spec.get("items", [])[:6]
    palette = _palette(spec)
    primary = palette.get("primary", "#2563eb")
    accent = palette.get("accent", "#0ea5e9")
    soft = palette.get("soft", "#eff6ff")
    ink = palette.get("ink", "#10243f")
    cards = []
    motifs = ["flow", "shield", "network", "doc", "flow", "shield"]
    for i, it in enumerate(items):
        label = _item_text(it, "label")
        sub = _item_text(it, "sub")
        number = _esc(it.get("step") or f"{i + 1:02d}")
        motif = _shape_svg(motifs[i % len(motifs)], primary, accent, i)
        sub_html = f'<p>{sub}</p>' if sub else ""
        cards.append(
            f'<article class="card card-{i % 3}">'
            f'<div class="card-top"><div class="num">{number}</div><div class="motif">{motif}</div></div>'
            f'<h2>{label}</h2>'
            f'{sub_html}'
            f'</article>'
        )
    cards_html = "".join(cards)
    subtitle_html = f'<div class="subtitle">{subtitle}</div>' if subtitle else ''
    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">{WEBFONT_LINK}<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{
    width:{w}px; height:{h}px;
    font-family:{FONT_STACK}; color:{ink}; overflow:hidden;
    padding:64px 72px;
    background:
      radial-gradient(circle at 12% 15%, {soft} 0 18%, transparent 36%),
      radial-gradient(circle at 88% 18%, rgba(14,165,233,.16) 0 16%, transparent 34%),
      linear-gradient(135deg, #f8fafc 0%, #eef2f7 46%, #f7efe8 100%);
    position:relative;
  }}
  body:before {{
    content:""; position:absolute; inset:24px;
    border:1px solid rgba(15,23,42,.08); border-radius:34px;
    pointer-events:none;
  }}
  .header {{ display:grid; grid-template-columns:1fr 360px; gap:44px; align-items:end; margin-bottom:34px; }}
  .kicker {{ font-family:{FONT_STACK_MONO}; color:{primary}; font-size:22px; font-weight:800; letter-spacing:4px; margin-bottom:14px; }}
  .title {{ font-size:62px; line-height:1.12; font-weight:900; letter-spacing:-1.2px; max-width:1120px; }}
  .subtitle {{ font-size:28px; line-height:1.45; color:#64748b; margin-top:14px; }}
  .legend {{ justify-self:end; width:320px; height:110px; border-radius:26px; background:rgba(255,255,255,.72); box-shadow:0 18px 45px rgba(15,23,42,.09); display:flex; align-items:center; justify-content:center; }}
  .legend svg {{ width:220px; height:126px; }}
  .grid {{ display:grid; grid-template-columns:repeat(3, 1fr); gap:24px; height:760px; }}
  .card {{
    background:rgba(255,255,255,.86); border:1px solid rgba(15,23,42,.08);
    border-radius:30px; padding:28px 28px 26px;
    box-shadow:0 22px 55px rgba(15,23,42,.10);
    display:flex; flex-direction:column; min-width:0; overflow:hidden;
    position:relative;
  }}
  .card:after {{ content:""; position:absolute; left:0; top:0; bottom:0; width:8px; background:{primary}; opacity:.85; }}
  .card-1:after {{ background:{accent}; }} .card-2:after {{ background:#111827; opacity:.55; }}
  .card-top {{ display:flex; align-items:flex-start; justify-content:space-between; gap:14px; margin-bottom:18px; }}
  .num {{ font-family:{FONT_STACK_MONO}; font-size:24px; font-weight:900; color:white; background:{primary}; border-radius:16px; padding:10px 14px; line-height:1; box-shadow:0 10px 24px rgba(37,99,235,.22); }}
  .card-1 .num {{ background:{accent}; }} .card-2 .num {{ background:#111827; }}
  .motif {{ width:138px; height:82px; opacity:.95; }} .motif svg {{ width:100%; height:100%; display:block; }}
  h2 {{ font-size:34px; line-height:1.28; font-weight:900; letter-spacing:-.2px; margin-bottom:14px; word-break:normal; overflow-wrap:anywhere; }}
  p {{ font-size:24px; line-height:1.48; color:#475569; font-weight:600; word-break:normal; overflow-wrap:anywhere; }}
</style></head>
<body>
  <section class="header"><div><div class="kicker">图解要点</div><div class="title">{title}</div>{subtitle_html}</div><div class="legend">{_shape_svg('network', primary, accent, 7)}</div></section>
  <section class="grid">{cards_html}</section>
</body></html>"""


def _render_premium_flow(spec: dict) -> str:
    """Dense but readable process map for attack chains and workflows."""
    w, h = CANVAS["flow"]
    title = _esc(spec.get("title_zh", ""))
    items = spec.get("items", [])[:6]
    palette = _palette(spec)
    primary = palette.get("primary", "#2563eb")
    accent = palette.get("accent", "#0ea5e9")
    ink = palette.get("ink", "#10243f")
    steps = []
    for i, it in enumerate(items):
        label = _item_text(it, "label")
        sub = _item_text(it, "sub")
        connector = '<div class="connector"></div>' if i < len(items) - 1 else ''
        steps.append(
            f'<div class="step"><div class="badge">{_esc(it.get("step") or str(i + 1))}</div>'
            f'<div class="step-text"><h2>{label}</h2>{f"<p>{sub}</p>" if sub else ""}</div>'
            f'<div class="icon">{_shape_svg("flow", primary, accent, i)}</div></div>{connector}'
        )
    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">{WEBFONT_LINK}<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ width:{w}px; height:{h}px; padding:58px 68px; overflow:hidden; font-family:{FONT_STACK}; color:{ink}; background:linear-gradient(135deg,#0f172a 0%,#172554 42%,#f8fafc 42%,#f1f5f9 100%); }}
  .shell {{ height:100%; border-radius:36px; background:rgba(255,255,255,.92); box-shadow:0 28px 80px rgba(2,6,23,.28); overflow:hidden; display:grid; grid-template-columns:470px 1fr; }}
  .side {{ background:linear-gradient(160deg,#0f172a,{primary}); color:white; padding:58px 46px; display:flex; flex-direction:column; justify-content:space-between; }}
  .kicker {{ font-family:{FONT_STACK_MONO}; font-size:20px; letter-spacing:4px; opacity:.72; font-weight:800; }}
  .title {{ font-size:58px; line-height:1.16; font-weight:900; letter-spacing:-1px; word-break:normal; overflow-wrap:anywhere; }}
  .side svg {{ width:300px; height:172px; filter:drop-shadow(0 12px 24px rgba(0,0,0,.22)); }}
  .main {{ padding:44px 52px; display:flex; flex-direction:column; gap:12px; }}
  .step {{ flex:1; min-height:0; border-radius:24px; background:white; border:1px solid rgba(15,23,42,.08); box-shadow:0 14px 34px rgba(15,23,42,.07); padding:20px 24px; display:grid; grid-template-columns:62px 1fr 136px; gap:20px; align-items:center; }}
  .badge {{ width:54px; height:54px; border-radius:17px; display:flex; align-items:center; justify-content:center; color:white; background:{accent}; font-family:{FONT_STACK_MONO}; font-size:24px; font-weight:900; }}
  h2 {{ font-size:30px; line-height:1.28; font-weight:900; margin-bottom:6px; word-break:normal; overflow-wrap:anywhere; }}
  p {{ font-size:21px; line-height:1.42; color:#64748b; font-weight:600; word-break:normal; overflow-wrap:anywhere; }}
  .icon svg {{ width:136px; height:78px; }}
  .connector {{ width:5px; height:18px; margin-left:25px; background:linear-gradient({accent}, transparent); border-radius:99px; }}
</style></head><body><section class="shell"><aside class="side"><div class="kicker">流程图解</div><div class="title">{title}</div><div>{_shape_svg('shield', '#ffffff', accent, 9)}</div></aside><main class="main">{''.join(steps)}</main></section></body></html>"""


def _render_cover_ink_poem(spec: dict) -> str:
    """Safe Chinese-painting cover fallback for AI cover validation failures."""
    w, h = CANVAS["cover"]
    poem_lines = [_esc(str(line).strip()) for line in (spec.get("cover_text") or []) if str(line).strip()]
    if not poem_lines:
        poem_lines = ["山水开卷见其要", "清风入画读其微"]
    poem = "".join(f"<div>{line}</div>" for line in poem_lines[:4])
    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">{WEBFONT_LINK}<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{
    width:{w}px; height:{h}px; overflow:hidden; position:relative;
    font-family:{FONT_STACK_SERIF}; color:#1f2933;
    background:
      radial-gradient(ellipse at 20% 22%, rgba(122, 92, 60, .18), transparent 34%),
      radial-gradient(ellipse at 78% 18%, rgba(42, 93, 78, .18), transparent 30%),
      linear-gradient(180deg, #f8f2e4 0%, #efe5d0 100%);
  }}
  body::before {{
    content:''; position:absolute; inset:0;
    background:
      linear-gradient(110deg, transparent 0 54%, rgba(255,255,255,.34) 54% 100%),
      repeating-linear-gradient(0deg, rgba(92,70,43,.045) 0 1px, transparent 1px 7px);
    opacity:.8;
  }}
  .mountains {{ position:absolute; left:0; right:0; bottom:-8px; width:100%; height:360px; }}
  .sun {{ position:absolute; left:210px; top:118px; width:118px; height:118px; border-radius:50%; background:#b9573f; opacity:.78; filter:blur(.2px); }}
  .mist {{ position:absolute; left:320px; right:260px; bottom:184px; height:28px; border-radius:999px; background:rgba(255,255,255,.62); filter:blur(8px); }}
  .poem {{
    position:absolute; right:300px; top:108px;
    writing-mode:vertical-rl; text-orientation:upright;
    display:flex; gap:24px; align-items:flex-start;
    font-size:52px; line-height:1.35; font-weight:700; letter-spacing:0;
    color:#27312d; text-shadow:0 1px 0 rgba(255,255,255,.4);
  }}
  .seal {{
    position:absolute; right:182px; bottom:122px; width:74px; height:74px;
    border:5px solid #a64235; color:#a64235;
    display:flex; align-items:center; justify-content:center;
    font-family:{FONT_STACK_SERIF}; font-size:26px; font-weight:900;
  }}
</style></head><body>
  <div class="sun"></div>
  <svg class="mountains" viewBox="0 0 1800 360" preserveAspectRatio="none" aria-hidden="true">
    <path d="M0 260 C160 215 230 155 360 196 C470 230 560 100 700 150 C835 198 930 58 1090 126 C1240 190 1360 126 1490 172 C1600 210 1690 226 1800 190 L1800 360 L0 360 Z" fill="#273f38" opacity=".34"/>
    <path d="M0 304 C210 246 310 234 470 260 C650 292 770 186 930 214 C1100 244 1240 196 1400 236 C1540 272 1660 252 1800 226 L1800 360 L0 360 Z" fill="#1f2f2a" opacity=".48"/>
    <path d="M0 322 C260 298 420 326 650 308 C840 292 980 326 1180 302 C1410 274 1600 304 1800 286 L1800 360 L0 360 Z" fill="#111827" opacity=".16"/>
  </svg>
  <div class="mist"></div>
  <div class="poem">{poem}</div>
  <div class="seal">證</div>
</body></html>"""


# === STATIC SWISS ARTICLE LAYOUTS ==========================================
# Adapted from guizang-ppt-skill's registered Swiss layouts. These renderers
# intentionally keep only the static grid and typography language: no deck
# runtime, WebGL, animation, CDN fonts, or external skill dependency.

_SWISS_LAYOUTS = {"S03", "S05", "S07", "S08", "S11", "S17"}


def _numeric_item(item: dict) -> tuple[float, str, str] | None:
    """Return a comparable numeric value without inventing missing data."""
    raw = item.get("value")
    display = str(item.get("display_value") or "").strip()
    unit = str(item.get("unit") or "").strip()
    if isinstance(raw, (int, float)):
        return float(raw), display or f"{raw:g}", unit
    text = " ".join(str(item.get(key) or "") for key in ("label", "sub", "text"))
    match = re.search(r"(?<![A-Za-z0-9])(-?\d+(?:\.\d+)?)\s*(%|ms|s|秒|毫秒|倍|GB|MB|KB)?", text, re.I)
    if not match:
        return None
    value = float(match.group(1))
    return value, display or match.group(1), unit or (match.group(2) or "")


def _requested_swiss_layout(spec: dict) -> str:
    explicit = str(spec.get("swiss_layout") or "").upper()
    if explicit in _SWISS_LAYOUTS:
        return explicit

    kind = _kind(spec)
    structure = str(spec.get("structure") or "")
    items = [item for item in spec.get("items", []) if isinstance(item, dict)]
    numeric = [_numeric_item(item) for item in items]

    if structure == "data-first" and 5 <= len(items) <= 10 and all(numeric):
        return "S07"
    if structure == "layer-stack" and len(items) == 3:
        return "S05"
    if structure == "hub-and-spoke" and len(items) == 3:
        return "S17"
    if structure == "before-after" and len(items) == 2:
        return "S08"
    if structure == "pipeline" and 4 <= len(items) <= 7:
        return "S11"
    if kind == "compare" and len(items) == 2:
        return "S08"
    if kind in {"flow", "timeline"} and 4 <= len(items) <= 7:
        return "S11"
    if kind in {"summary", "impact"} and items:
        return "S03"
    return ""


def _swiss_shell(spec: dict, layout: str, body: str) -> str:
    w, h = CANVAS.get(_kind(spec), CANVAS["summary"])
    title = _esc(spec.get("title_zh", ""))
    kicker = _esc(spec.get("source_section", "") or spec.get("kind", "ARTICLE VISUAL"))
    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><style>
  :root {{ --paper:#fafaf8; --ink:#0a0a0a; --grey-1:#f0f0ee; --grey-2:#d4d4d2;
    --grey-3:#737373; --accent:#002FA7; --accent-on:#fff; --secondary:#525252; }}
  *,*::before,*::after {{ box-sizing:border-box; margin:0; padding:0; animation:none!important; transition:none!important; }}
  html,body {{ width:{w}px; height:{h}px; overflow:hidden; }}
  body {{ background:var(--paper); color:var(--ink); font-family:{FONT_STACK}; letter-spacing:0; }}
  .page {{ width:100%; height:100%; padding:58px 88px 54px; display:flex; flex-direction:column; overflow:hidden; }}
  .chrome {{ display:flex; justify-content:space-between; align-items:center; min-height:28px; margin-bottom:30px;
    color:var(--grey-3); font:600 15px/1.4 {FONT_STACK_MONO}; text-transform:uppercase; }}
  .chrome .mark {{ width:10px; height:10px; background:var(--accent); margin-right:14px; display:inline-block; }}
  .head {{ display:grid; grid-template-columns:minmax(0,1fr) minmax(280px,520px); gap:64px; align-items:end; margin-bottom:44px; }}
  .kicker,.meta {{ color:var(--grey-3); font:600 15px/1.4 {FONT_STACK_MONO}; text-transform:uppercase; }}
  h1 {{ margin-top:12px; max-width:1200px; font-size:68px; line-height:1.12; font-weight:300; overflow-wrap:anywhere; }}
  .lead {{ color:var(--secondary); font-size:25px; line-height:1.55; }}
  .accent {{ color:var(--accent); }}
  .fill-accent {{ background:var(--accent)!important; color:var(--accent-on)!important; }}
  .fill-ink {{ background:var(--ink)!important; color:#fff!important; }}
  .fill-grey {{ background:var(--grey-1)!important; }}
  .split {{ display:grid; grid-template-columns:1fr 1fr; flex:1; min-height:0; }}
  .split-panel {{ padding:64px; display:flex; flex-direction:column; justify-content:space-between; overflow:hidden; }}
  .statement {{ font-size:82px; line-height:1.08; font-weight:250; overflow-wrap:anywhere; }}
  .statement-copy {{ font-size:31px; line-height:1.55; color:var(--secondary); overflow-wrap:anywhere; }}
  .stack {{ display:grid; grid-template-columns:repeat(3,1fr); gap:0; flex:1; min-height:0; }}
  .layer {{ padding:42px; display:flex; flex-direction:column; justify-content:space-between; border-left:1px solid var(--grey-2); overflow:hidden; }}
  .layer:first-child {{ border-left:0; }} .layer-nb {{ font:500 24px/1 {FONT_STACK_MONO}; }}
  .layer h2 {{ font-size:46px; line-height:1.15; font-weight:300; overflow-wrap:anywhere; }}
  .layer p {{ font-size:23px; line-height:1.5; overflow-wrap:anywhere; }}
  .bars {{ display:grid; grid-template-columns:270px minmax(0,1fr) 160px; gap:18px 28px; align-items:center; flex:1; min-height:0; }}
  .bar-label {{ font-size:23px; font-weight:500; overflow-wrap:anywhere; }} .track {{ height:38px; background:var(--grey-1); overflow:hidden; }}
  .bar {{ height:100%; background:var(--ink); }} .bar-value {{ font-size:31px; font-weight:300; font-variant-numeric:tabular-nums; }}
  .duo {{ display:grid; grid-template-columns:1fr 1px 1fr; gap:0 60px; flex:1; min-height:0; }}
  .vrule {{ background:var(--grey-2); }} .duo-col {{ display:flex; flex-direction:column; min-width:0; overflow:hidden; }}
  .duo-col h2 {{ margin:22px 0; font-size:58px; line-height:1.05; font-weight:250; overflow-wrap:anywhere; }}
  .duo-col p {{ font-size:25px; line-height:1.52; color:var(--secondary); overflow-wrap:anywhere; }}
  .duo-list {{ list-style:none; margin-top:auto; padding-top:22px; border-top:1px solid var(--grey-2); }}
  .duo-list li {{ font-size:21px; line-height:1.45; padding:8px 0 8px 24px; position:relative; }}
  .duo-list li::before {{ content:''; position:absolute; left:0; top:23px; width:10px; height:2px; background:currentColor; }}
  .timeline {{ position:relative; display:flex; align-items:center; flex:1; min-height:0; }}
  .timeline::before {{ content:''; position:absolute; left:4%; right:4%; top:50%; height:1px; background:var(--grey-2); }}
  .nodes {{ width:100%; display:grid; align-items:center; position:relative; }}
  .node {{ position:relative; display:flex; justify-content:center; }} .dot {{ width:12px; height:12px; background:var(--ink); z-index:1; }}
  .node-label {{ position:absolute; left:50%; width:210px; transform:translateX(-50%); text-align:center; }}
  .node.up .node-label {{ bottom:30px; }} .node.down .node-label {{ top:30px; }}
  .node-step {{ display:block; color:var(--grey-3); font:600 15px/1.3 {FONT_STACK_MONO}; }}
  .node-name {{ display:block; margin-top:8px; font-size:22px; line-height:1.3; overflow-wrap:anywhere; }}
  .node-sub {{ display:block; margin-top:6px; color:var(--secondary); font-size:17px; line-height:1.35; overflow-wrap:anywhere; }}
  .system {{ display:grid; grid-template-columns:1.05fr .95fr; gap:70px; flex:1; min-height:0; }}
  .system-notes {{ display:grid; grid-template-columns:repeat(3,1fr); gap:20px; align-content:end; }}
  .system-note {{ border-top:4px solid var(--ink); padding-top:20px; }} .system-note h2 {{ font-size:30px; font-weight:400; margin-bottom:12px; }}
  .system-note p {{ color:var(--secondary); font-size:19px; line-height:1.45; }}
  .rings {{ position:relative; min-height:0; }} .ring {{ position:absolute; border:2px solid var(--ink); left:50%; top:50%; transform:translate(-50%,-50%); display:grid; place-items:start center; padding-top:24px; }}
  .ring.outer {{ width:570px; height:570px; }} .ring.middle {{ width:390px; height:390px; border-color:var(--accent); }}
  .ring.core {{ width:210px; height:210px; background:var(--accent); color:#fff; border:0; place-items:center; padding:24px; text-align:center; }}
  .ring-label {{ font-size:22px; font-weight:600; }}
</style></head><body><main class="page" data-layout="{layout}">
<header class="chrome"><span><i class="mark"></i>{kicker}</span><span>{layout} / WECHAT INFOGRAPHIC</span></header>
{body}</main></body></html>"""


def _render_swiss_s03(spec: dict) -> str:
    items = spec.get("items", [])
    first = items[0] if items else {}
    statement = _esc(first.get("label") or spec.get("title_zh", ""))
    explanation = _esc(first.get("sub") or spec.get("subtitle") or spec.get("notes", ""))
    body = f'''<div class="split"><section class="split-panel fill-ink"><div class="kicker">CORE STATEMENT</div><div class="statement">{statement}</div></section><section class="split-panel fill-grey"><div class="kicker">WHY IT MATTERS</div><div class="statement-copy">{explanation}</div><div class="meta">{_esc(spec.get("title_zh", ""))}</div></section></div>'''
    return _swiss_shell(spec, "S03", body)


def _render_swiss_s05(spec: dict) -> str:
    items = spec.get("items", [])[:3]
    if len(items) != 3:
        return _render_summary_cards(spec)
    layers = []
    tones = ["fill-grey", "fill-accent", "fill-ink"]
    for i, item in enumerate(items):
        layers.append(f'''<article class="layer {tones[i]}"><span class="layer-nb">0{i+1}</span><h2>{_item_text(item,"label")}</h2><p>{_item_text(item,"sub")}</p><span class="meta">LAYER {i+1}</span></article>''')
    head = f'''<div class="head"><div><div class="kicker">THREE LAYERS</div><h1>{_esc(spec.get("title_zh", ""))}</h1></div><p class="lead">{_esc(spec.get("subtitle", ""))}</p></div>'''
    return _swiss_shell(spec, "S05", head + f'<div class="stack">{"".join(layers)}</div>')


def _render_swiss_s07(spec: dict) -> str:
    parsed = [(item, _numeric_item(item)) for item in spec.get("items", [])[:10]]
    parsed = [(item, value) for item, value in parsed if value is not None]
    if len(parsed) < 5:
        return _render_summary_stat_grid(spec)
    maximum = max(abs(value[0]) for _, value in parsed) or 1
    rows = []
    for i, (item, value) in enumerate(parsed):
        width = max(2, min(100, abs(value[0]) / maximum * 100))
        tone = " fill-accent" if i == 0 else ""
        rows.append(f'<div class="bar-label">{_item_text(item,"label")}</div><div class="track"><div class="bar{tone}" style="width:{width:.2f}%"></div></div><div class="bar-value">{_esc(value[1])}<small>{_esc(value[2])}</small></div>')
    head = f'''<div class="head"><div><div class="kicker">DATA COMPARISON</div><h1>{_esc(spec.get("title_zh", ""))}</h1></div><p class="lead">{_esc(spec.get("subtitle", ""))}</p></div>'''
    return _swiss_shell(spec, "S07", head + f'<div class="bars">{"".join(rows)}</div>')


def _render_swiss_s08(spec: dict) -> str:
    items = spec.get("items", [])[:2]
    if len(items) != 2:
        return _render_compare_tagged(spec)
    cols = []
    for i, item in enumerate(items):
        label = _item_text(item, "label")
        sub = _item_text(item, "sub")
        bullets = item.get("bullets") if isinstance(item.get("bullets"), list) else []
        if not bullets and sub:
            bullets = [part.strip() for part in re.split(r"[；;。]", str(item.get("sub"))) if part.strip()][:3]
        list_html = "".join(f"<li>{_esc(value)}</li>" for value in bullets)
        cols.append(f'''<section class="duo-col{' accent' if i else ''}"><div class="kicker">{'AFTER' if i else 'BEFORE'} / 0{i+1}</div><h2>{label}</h2><p>{sub}</p><ul class="duo-list">{list_html}</ul></section>''')
    head = f'''<div class="head"><div><div class="kicker">DUO COMPARE</div><h1>{_esc(spec.get("title_zh", ""))}</h1></div><p class="lead">{_esc(spec.get("subtitle", ""))}</p></div>'''
    return _swiss_shell(spec, "S08", head + f'<div class="duo">{cols[0]}<span class="vrule"></span>{cols[1]}</div>')


def _render_swiss_s11(spec: dict) -> str:
    items = spec.get("items", [])[:7]
    if not 4 <= len(items) <= 7:
        return _render_flow_vertical(spec)
    nodes = []
    for i, item in enumerate(items):
        nodes.append(f'''<div class="node {'up' if i % 2 == 0 else 'down'}"><span class="dot{' fill-accent' if i == len(items)-1 else ''}"></span><div class="node-label"><span class="node-step">{_esc(item.get("step") or str(i+1).zfill(2))}</span><span class="node-name">{_item_text(item,"label")}</span><span class="node-sub">{_item_text(item,"sub")}</span></div></div>''')
    head = f'''<div class="head"><div><div class="kicker">PROCESS PATH</div><h1>{_esc(spec.get("title_zh", ""))}</h1></div><p class="lead">{_esc(spec.get("subtitle", ""))}</p></div>'''
    return _swiss_shell(spec, "S11", head + f'<div class="timeline"><div class="nodes" style="grid-template-columns:repeat({len(nodes)},1fr)">{"".join(nodes)}</div></div>')


def _render_swiss_s17(spec: dict) -> str:
    items = spec.get("items", [])[:3]
    if len(items) != 3:
        return _render_premium_cards(spec)
    notes = []
    for i, item in enumerate(items):
        notes.append(f'''<article class="system-note"><div class="meta">{'CORE' if i == 0 else 'MIDDLE' if i == 1 else 'OUTER'}</div><h2>{_item_text(item,"label")}</h2><p>{_item_text(item,"sub")}</p></article>''')
    labels = [_item_text(item, "label") for item in items]
    rings = f'''<div class="rings"><div class="ring outer"><span class="ring-label">{labels[2]}</span></div><div class="ring middle"><span class="ring-label accent">{labels[1]}</span></div><div class="ring core"><span class="ring-label">{labels[0]}</span></div></div>'''
    head = f'''<div class="head"><div><div class="kicker">SYSTEM LAYERS</div><h1>{_esc(spec.get("title_zh", ""))}</h1></div><p class="lead">{_esc(spec.get("subtitle", ""))}</p></div>'''
    return _swiss_shell(spec, "S17", head + f'<div class="system"><div class="system-notes">{"".join(notes)}</div>{rings}</div>')


# === DISPATCH ==============================================================


TEMPLATE_REGISTRY: dict = {}


def _register() -> None:
    """Populate TEMPLATE_REGISTRY. Done at import time."""
    TEMPLATE_REGISTRY.update({
        "cover_dark_gold": _render_cover_dark_gold,
        "cover_terminal": _render_cover_terminal,
        "cover_magazine": _render_cover_magazine,
        "cover_grid": _render_cover_grid,
        "cover_whiteboard": _render_cover_whiteboard,
        "cover_ink_poem": _render_cover_ink_poem,
        "premium_cards": _render_premium_cards,
        "premium_flow": _render_premium_flow,
        "summary_cards": _render_summary_cards,
        "summary_stat_grid": _render_summary_stat_grid,
        "summary_left_callout": _render_summary_left_callout,
        "flow_vertical": _render_flow_vertical,
        "flow_horizontal": _render_flow_horizontal,
        "checklist_minimal": _render_checklist_minimal,
        "compare_tagged": _render_compare_tagged,
        "swiss_s03": _render_swiss_s03,
        "swiss_s05": _render_swiss_s05,
        "swiss_s07": _render_swiss_s07,
        "swiss_s08": _render_swiss_s08,
        "swiss_s11": _render_swiss_s11,
        "swiss_s17": _render_swiss_s17,
    })


_register()


# Title keyword clues that override the visual_theme default.
_COVER_TERMINAL_PATTERNS = re.compile(
    r"CVE-\d|故障|事件|宕机|dig\b|whois\b|查询|攻击|入侵|崩溃|0day",
    flags=re.I,
)


def _select_template(spec: dict) -> str:
    """Pick a template id for a given spec.

    Priority: title content clues > visual_theme default > kind fallback.
    The goal is that within one article, 5-7 specs use 3-4 distinct
    templates instead of 7 copies of the same boilerplate.
    """
    kind = _kind(spec)
    theme = str(spec.get("visual_theme") or "").strip().lower()
    title = str(spec.get("title_zh") or "")

    if kind == "cover":
        if str(spec.get("text_policy") or "") == "cover-poem-calligraphy" and spec.get("cover_text"):
            return "cover_ink_poem"
        if _COVER_TERMINAL_PATTERNS.search(title):
            return "cover_terminal"
        return {
            "bold-statement": "cover_dark_gold",
            "editorial-magazine": "cover_magazine",
            "warm-narrative": "cover_magazine",
            "clean-professional": "cover_grid",
            "data-infographic": "cover_grid",
            "whiteboard-sketch": "cover_whiteboard",
            "snoopy-comic": "cover_magazine",
        }.get(theme, "cover_dark_gold")

    swiss_layout = _requested_swiss_layout(spec)
    if swiss_layout:
        return f"swiss_{swiss_layout.lower()}"

    filename = str(spec.get("filename", ""))
    if kind in ("summary", "relations", "timeline"):
        if theme == "editorial-magazine":
            if "one_screen" in filename:
                return "summary_stat_grid"
            if "indirection" in filename or "resolver" in title or "公共 DNS" in title:
                return "premium_cards"
            return "summary_left_callout"
        candidates = {
            "data-infographic": ["summary_stat_grid", "premium_cards", "summary_left_callout"],
            "warm-narrative": ["summary_left_callout", "premium_cards"],
            "bold-statement": ["summary_left_callout", "premium_cards"],
            "clean-professional": ["premium_cards", "summary_stat_grid", "summary_left_callout"],
            "whiteboard-sketch": ["premium_cards"],
            "snoopy-comic": ["summary_left_callout", "premium_cards"],
        }.get(theme, ["premium_cards", "summary_stat_grid"])
        seed = sum(ord(c) for c in filename) + len(filename)
        return candidates[seed % len(candidates)]

    if kind == "flow":
        # Prefer dense flow templates for technical WeChat diagrams. The older
        # horizontal ribbon leaves large empty card bottoms when steps are short,
        # which looks sparse on mobile preview even when the labels are correct.
        return "premium_flow" if theme in {"editorial-magazine", "whiteboard-sketch", "data-infographic", "clean-professional"} else {
            "warm-narrative": "flow_vertical",
            "bold-statement": "flow_vertical",
            "snoopy-comic": "flow_vertical",
        }.get(theme, "premium_flow")

    if kind == "checklist":
        return "premium_cards" if theme in {"editorial-magazine", "whiteboard-sketch", "data-infographic"} else "checklist_minimal"
    if kind in ("compare", "impact"):
        return "premium_cards" if theme in {"editorial-magazine", "whiteboard-sketch", "data-infographic"} else "compare_tagged"

    return "summary_cards"


def render_html_for_spec(spec: dict) -> str:
    """Render an HTML page for one spec.

    Dispatches by (kind, visual_theme, title-content clues) into one of
    several templates so the same WeChat article can mix multiple visual
    skeletons instead of always falling back to a single boilerplate.
    """
    template_id = _select_template(spec)
    renderer = TEMPLATE_REGISTRY.get(template_id)
    if renderer is not None:
        return renderer(spec)
    # Legacy fallback if the dispatcher returns an unknown id.
    kind = _kind(spec)
    if kind == "cover":
        return _render_cover_dark_gold(spec)
    if kind == "flow":
        return _render_flow_vertical(spec)
    if kind == "checklist":
        return _render_checklist_minimal(spec)
    if kind in ("compare", "impact"):
        return _render_compare_tagged(spec)
    return _render_summary_cards(spec)


def canvas_for_kind(kind: str) -> tuple[int, int]:
    return CANVAS.get(kind, CANVAS["summary"])


# === MERMAID (structural / relational diagrams) ==========================
# Flow, hub-and-spoke, before-after, layer-stack and timeline diagrams are
# routed here instead of the fixed 16:9 Swiss layouts. Mermaid auto-lays-out
# the graph and we capture it full-page at WeChat width, so there is no fixed
# canvas and therefore no large blank band. All visible text comes straight
# from the spec, so exact facts stay verbatim (deterministic, not AI-guessed).

_SKILL_ROOT = Path(__file__).resolve().parent.parent
MERMAID_JS = _SKILL_ROOT / "node_modules" / "mermaid" / "dist" / "mermaid.min.js"
MERMAID_WIDTH = 1100  # WeChat-friendly body width; height is captured full-page.
_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫"


def mermaid_available() -> bool:
    return MERMAID_JS.is_file()


def _mm(text: str) -> str:
    """Sanitize text for use inside a quoted mermaid label."""
    s = str(text or "").strip().replace("\n", " ")
    # Double quotes and backslashes break mermaid label parsing; swap for
    # visually-close full-width glyphs so exact meaning survives.
    s = s.replace('"', "＂").replace("\\", "／")
    return re.sub(r"\s+", " ", s)


def _mm_label(label: str, sub: str = "") -> str:
    """A quoted mermaid label with an optional smaller second line."""
    head = _mm(label)
    tail = _mm(sub)
    if tail:
        return f'"{head}<br/><small>{tail}</small>"'
    return f'"{head}"'


def _step_prefix(step: object, index: int) -> str:
    digits = re.sub(r"\D", "", str(step or ""))
    try:
        n = int(digits) if digits else index + 1
    except ValueError:
        n = index + 1
    return f"{_CIRCLED[n - 1]} " if 1 <= n <= len(_CIRCLED) else f"{n}. "


def _mm_classdefs(spec: dict) -> list[str]:
    p = _palette(spec)
    return [
        f"classDef sideA fill:{p['soft']},stroke:{p['primary']},color:{p['ink']},rx:10,ry:10;",
        f"classDef sideB fill:#f8fafc,stroke:#64748b,color:#0f172a,rx:10,ry:10;",
        f"classDef last fill:{p['primary']},stroke:{p['primary']},color:#ffffff,rx:10,ry:10;",
        f"classDef hub fill:{p['ink']},stroke:{p['ink']},color:#ffffff;",
    ]


def _mermaid_pipeline(spec: dict, direction: str = "TB") -> str:
    items = [i for i in (spec.get("items") or []) if isinstance(i, dict)]
    lines = [f"flowchart {direction}"]
    ids: list[str] = []
    for i, item in enumerate(items):
        nid = f"n{i}"
        ids.append(nid)
        label = _step_prefix(item.get("step"), i) + str(item.get("label") or item.get("text") or "")
        lines.append(f"  {nid}[{_mm_label(label, item.get('sub', ''))}]")
    for a, b in zip(ids, ids[1:]):
        lines.append(f"  {a} --> {b}")
    if ids:
        lines.append(f"  class {ids[-1]} last;")
    lines += _mm_classdefs(spec)
    return "\n".join(lines)


def _mermaid_hub(spec: dict) -> str:
    items = [i for i in (spec.get("items") or []) if isinstance(i, dict)]
    center = _mm(spec.get("title_zh") or spec.get("subtitle") or "核心")
    lines = ["flowchart LR", f'  H(("{center}"))', "  class H hub;"]
    for i, item in enumerate(items):
        nid = f"s{i}"
        lines.append(f"  {nid}[{_mm_label(item.get('label', ''), item.get('sub', ''))}]")
        lines.append(f"  H --- {nid}")
    lines += _mm_classdefs(spec)
    return "\n".join(lines)


def _mermaid_before_after(spec: dict) -> str:
    items = [i for i in (spec.get("items") or []) if isinstance(i, dict)][:2]
    if len(items) != 2:
        return _mermaid_pipeline(spec, "TB")
    a, b = items
    middle = _mm(spec.get("subtitle") or spec.get("title_zh") or "同一对象")
    lines = [
        "flowchart LR",
        f"  L[{_mm_label(a.get('label', ''), a.get('sub', ''))}]",
        f'  M(["{middle}"])',
        f"  R[{_mm_label(b.get('label', ''), b.get('sub', ''))}]",
        "  L --- M --- R",
        "  class L sideA;",
        "  class M hub;",
        "  class R sideB;",
    ]
    lines += _mm_classdefs(spec)
    return "\n".join(lines)


def build_mermaid_source(spec: dict) -> str:
    """Map a body-image spec to mermaid diagram source by its structure."""
    structure = str(spec.get("structure") or "").strip().lower()
    kind = str(spec.get("kind") or "").strip().lower()
    if structure == "hub-and-spoke" or kind == "relations":
        return _mermaid_hub(spec)
    if structure == "before-after" or kind == "compare":
        return _mermaid_before_after(spec)
    if structure == "timeline" or kind == "timeline":
        return _mermaid_pipeline(spec, "LR")
    return _mermaid_pipeline(spec, "TB")


def _mermaid_html(source: str, spec: dict) -> str:
    p = _palette(spec)
    kicker = _esc(spec.get("source_section") or spec.get("kind") or "ARTICLE VISUAL")
    title = _esc(spec.get("title_zh") or "")
    subtitle = _esc(spec.get("subtitle") or "")
    header = ""
    if title:
        sub_html = f'<p class="mm-sub">{subtitle}</p>' if subtitle else ""
        header = (
            f'<header class="mm-head"><span class="mm-kicker">{kicker}</span>'
            f"<h1>{title}</h1>{sub_html}</header>"
        )
    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><style>
  *{{margin:0;padding:0;box-sizing:border-box}}
  body{{background:#ffffff;font-family:{FONT_STACK}}}
  .wrap{{width:{MERMAID_WIDTH}px;padding:52px 60px 60px;background:#ffffff}}
  .mm-head{{margin-bottom:36px;border-left:6px solid {p['primary']};padding-left:22px}}
  .mm-kicker{{display:inline-block;font:700 15px/1.4 {FONT_STACK_MONO};letter-spacing:2px;
    text-transform:uppercase;color:{p['primary']};margin-bottom:12px}}
  .mm-head h1{{font-size:42px;line-height:1.25;font-weight:800;color:{p['ink']};overflow-wrap:anywhere}}
  .mm-head .mm-sub{{margin-top:12px;font-size:22px;line-height:1.5;color:#475569}}
  .mermaid{{display:flex;justify-content:center}}
  .mermaid svg{{max-width:100%;height:auto}}
  .mermaid small{{font-size:.8em;color:inherit;opacity:.72;font-weight:400}}
</style></head><body><div class="wrap">{header}
<pre class="mermaid">
{source}
</pre></div>
<script src="{MERMAID_JS.resolve().as_uri()}"></script>
<script>
  mermaid.initialize({{startOnLoad:false, theme:'base', securityLevel:'loose',
    flowchart:{{htmlLabels:true, curve:'basis', nodeSpacing:46, rankSpacing:52, padding:14}},
    themeVariables:{{fontFamily:{FONT_STACK!r}, fontSize:'20px',
      primaryColor:{p['soft']!r}, primaryBorderColor:{p['primary']!r},
      primaryTextColor:{p['ink']!r}, lineColor:'#94a3b8', edgeLabelBackground:'#ffffff'}}}});
  mermaid.run().then(function(){{document.body.setAttribute('data-mm','done');}});
</script>
</body></html>"""


def render_png_fullpage(html_path: Path, png_path: Path, width: int, wait_ms: int = 1800) -> bool:
    """Screenshot an HTML file full-page (auto height) to PNG via Playwright."""
    png_path.parent.mkdir(parents=True, exist_ok=True)
    png_path.unlink(missing_ok=True)
    url = f"file://{html_path.resolve()}"
    cmd = [
        "npx", "playwright", "screenshot", url, str(png_path),
        "--viewport-size", f"{width},200", "--full-page",
        "--wait-for-timeout", str(wait_ms),
    ]
    for attempt in range(2):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            print(f"[html_render] mermaid screenshot attempt {attempt+1}/2 failed: {exc}", file=sys.stderr)
            if attempt == 1:
                return False
            continue
        if proc.returncode != 0:
            print(
                f"[html_render] mermaid screenshot attempt {attempt+1}/2 failed for {png_path.name}: "
                f"{proc.stderr.strip()[:300]}",
                file=sys.stderr,
            )
            if attempt == 1:
                return False
            continue
        if not png_path.exists() or png_path.stat().st_size < 5000:
            if attempt == 1:
                return False
            continue
        return True
    return False


def render_mermaid_png(spec: dict, png_path: Path, html_dir: Path | None = None) -> bool:
    """Render one structural spec to PNG through mermaid + Playwright full-page."""
    if not mermaid_available():
        raise RuntimeError(
            "mermaid renderer unavailable: mermaid.min.js not found at "
            f"{MERMAID_JS}. Install it once with: cd {_SKILL_ROOT} && npm install mermaid"
        )
    source = build_mermaid_source(spec)
    html = _mermaid_html(source, spec)
    if html_dir is None:
        html_dir = png_path.parent / "html"
    html_dir.mkdir(parents=True, exist_ok=True)
    stem = png_path.stem
    (html_dir / f"{stem}.mmd").write_text(source, encoding="utf-8")
    html_path = html_dir / f"{stem}.mermaid.html"
    html_path.write_text(html, encoding="utf-8")
    return render_png_fullpage(html_path, png_path, MERMAID_WIDTH)


def render_png(html_path: Path, png_path: Path, width: int, height: int) -> bool:
    """Render an HTML file to a PNG via Playwright. Returns True on success."""
    png_path.parent.mkdir(parents=True, exist_ok=True)
    png_path.unlink(missing_ok=True)
    url = f"file://{html_path.resolve()}"
    cmd = [
        "npx",
        "playwright",
        "screenshot",
        url,
        str(png_path),
        "--viewport-size",
        f"{width},{height}",
        "--wait-for-timeout",
        "800",
    ]
    for attempt in range(2):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            print(
                f"[html_render] playwright attempt {attempt+1}/2 invocation failed for {png_path.name}: {exc}",
                file=sys.stderr,
            )
            if attempt == 1:
                return False
            continue
        if proc.returncode != 0:
            print(
                f"[html_render] playwright attempt {attempt+1}/2 failed for {png_path.name}: "
                f"{proc.stderr.strip()[:300]}",
                file=sys.stderr,
            )
            if attempt == 1:
                return False
            continue
        if not png_path.exists() or png_path.stat().st_size < 5000:
            print(
                f"[html_render] attempt {attempt+1}/2 output missing or too small for {png_path.name}",
                file=sys.stderr,
            )
            if attempt == 1:
                return False
            continue
        return True
    return False


def render_specs(specs: list[dict], target_dir: Path, images_dir: Path, *, allow_cover_fallback: bool = False) -> dict[str, Path]:
    target_dir.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(parents=True, exist_ok=True)
    html_dir = images_dir / "html"
    html_dir.mkdir(parents=True, exist_ok=True)
    produced: dict[str, Path] = {}
    for idx, spec in enumerate(specs):
        kind = _kind(spec)
        filename = f"{spec.get('filename', f'image_{idx}')}.png"
        mode = str(spec.get("render_mode") or "").strip().lower()
        backend = str(spec.get("illustration_backend") or "").strip().lower()
        if kind == "cover" and not allow_cover_fallback:
            # Covers are AI-generated by /create-image, not HTML-rendered. When a
            # full specs file (cover + body) is passed to the deterministic
            # renderer, skip the cover instead of failing so the body diagrams
            # still render. --allow-cover-fallback is the only path that renders
            # a cover here, and only for an audited AI-failure fallback.
            print(f"  - {filename}: skipped (cover is generated by /create-image)", file=sys.stderr)
            continue

        # AI slots (Ian scene / create-image) are produced by their own backends;
        # the deterministic renderer only handles mermaid + HTML and skips them.
        if kind != "cover" and (mode == "ai" or backend in {"ian-xiaohei-illustrations", "create-image"}):
            print(f"  - {filename}: skipped (AI backend '{backend or mode}'; generate separately)", file=sys.stderr)
            continue

        # Mermaid path: structural / relational diagrams, captured full-page so
        # there is no fixed 16:9 canvas and no large blank band.
        if kind != "cover" and (mode == "mermaid" or backend == "mermaid-render"):
            final_path = images_dir / filename
            final_path.unlink(missing_ok=True)
            ok = render_mermaid_png(spec, final_path, html_dir=html_dir)
            if not ok:
                raise RuntimeError(
                    f"Mermaid render failed for slot {idx + 1} ({filename}). "
                    f"Inspect {html_dir / (spec.get('filename', f'image_{idx}') + '.mermaid.html')}."
                )
            produced[filename] = final_path
            print(f"  - {filename}: mermaid-render (width {MERMAID_WIDTH}, full-page)", file=sys.stderr)
            continue

        html = render_html_for_spec(spec)
        html_path = html_dir / f"{spec.get('filename', f'image_{idx}')}.html"
        html_path.write_text(html, encoding="utf-8")
        width, height = canvas_for_kind(kind)
        final_path = images_dir / filename
        final_path.unlink(missing_ok=True)
        ok = render_png(html_path, final_path, width, height)
        if not ok:
            raise RuntimeError(
                f"HTML render failed for slot {idx + 1} ({filename}). "
                f"Inspect {html_path}."
            )
        produced[filename] = final_path
        print(
            f"  - {filename}: html-render ({width}x{height})",
            file=sys.stderr,
        )
    # Copy the cover next to article.md so relative frontmatter paths remain valid.
    cover_name = f"{specs[0].get('filename', 'cover_article')}.png" if specs else None
    if cover_name:
        cover_src = images_dir / cover_name
        cover_dst = target_dir / cover_name
        if cover_src.exists():
            shutil.copy2(cover_src, cover_dst)
            produced[cover_name] = cover_dst
    return produced


def cli(argv: list[str]) -> int:
    if len(argv) < 2:
        print(
            "usage: html_render.py <image_specs.json> [--out-dir DIR] [--allow-cover-fallback]",
            file=sys.stderr,
        )
        return 2
    specs_path = Path(argv[1])
    out_dir = specs_path.parent
    allow_cover_fallback = False
    i = 2
    while i < len(argv):
        if argv[i] == "--out-dir":
            if i + 1 >= len(argv):
                print("ERROR: --out-dir requires a value", file=sys.stderr)
                return 2
            out_dir = Path(argv[i + 1])
            i += 2
        elif argv[i] == "--allow-cover-fallback":
            allow_cover_fallback = True
            i += 1
        else:
            print(f"ERROR: unknown option: {argv[i]}", file=sys.stderr)
            return 2
    specs = json.loads(specs_path.read_text(encoding="utf-8"))
    images_dir = out_dir / "images"
    try:
        produced = render_specs(specs, out_dir, images_dir, allow_cover_fallback=allow_cover_fallback)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"rendered {len(produced)} images into {images_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(cli(sys.argv))
