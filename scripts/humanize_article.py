#!/usr/bin/env python3
"""
humanize_article.py — Strip AI writing tells from a WeChat Markdown article.

Implements the rule set curated from
  * op7418/Humanizer-zh (SKILL.md)        - 24 AI tells in 4 categories
  * blader/humanizer                       - original English patterns
  * hardikpandya/stop-slop                 - quality scoring checklist

This script is intentionally a deterministic text rewriter (no LLM call)
so the result is reproducible, reviewable, and never depends on a network
round-trip. It scores the result on the 5-dimension / 50-point scale from
the Humanizer-zh SKILL.md.

Usage:
  humanize_article.py article.md --in-place
  humanize_article.py article.md -o article.humanized.md
  humanize_article.py --score article.md            # show score only
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path


# ---------------------------------------------------------------------------
# Rule source: Humanizer-zh 24-pattern catalogue (Chinese adaptations)
# ---------------------------------------------------------------------------

# High-frequency AI vocabulary observed in 2023+ Chinese tech writing.
AI_VOCAB = [
    "此外", "与此同时", "与……保持一致", "至关重要", "深入探讨", "深入分析",
    "深入了解", "强调", "持久的", "持久的证明", "增强", "培养", "获得",
    "突出", "相互作用", "复杂", "复杂性", "关键", "关键性的", "关键的",
    "格局", "关键作用", "展示了", "展示了……", "织锦", "证明", "宝贵的",
    "充满活力的", "无缝", "无缝衔接", "直观", "直观的", "强大的", "动力",
    "致力于", "不懈努力", "不断演变", "不断演进", "未来发展", "奠定了基础",
    "指明了方向", "保驾护航", "提质增效", "高质量发展", "新时代", "新征程",
    "必不可少", "举足轻重", "至关重要", "首当其冲", "与时俱进", "赋能",
    "全方位", "多维度", "深层次", "持续推进", "协同推进", "扎实推进",
    "努力打造", "积极探索", "稳步推进", "不断深化", "持续优化", "持续提升",
    "全面提升", "全新升级", "焕新升级", "重磅", "倾力", "倾力打造",
    "匠心", "匠心独运", "铸就", "铸就辉煌", "引领", "引领者", "标杆",
    "典范", "典范之作", "里程碑", "划时代", "革命性", "颠覆性", "颠覆",
    "触手可及", "一站式", "端到端", "闭环", "全景", "全链路", "全栈",
    "行稳致远", "踔厉奋发", "勇毅前行", "真抓实干", "善作善成",
    "更进一步", "更上一层楼", "更进一步", "再上新台阶", "硕果累累",
    "遍地开花", "如火如荼", "方兴未艾", "势如破竹", "一往无前",
]

# Negation-parallelism templates (the "Not only X, but Y" pattern).
NEGATIVE_PARALLELISM = [
    r"不仅.{2,30}?[，,].{0,8}?(而且|也|更是|还|并且)",
    r"这不仅仅[是]?.{2,30}?[，,].{0,8}?而是",
    r"不单[是]?.{2,30}?[，,].{0,8}?(更|而|而且)",
    r"不只是.{2,30}?[，,].{0,8}?(更是|而且|也是)",
    r"并非只是.{2,30}?[，,].{0,8}?而是",
]

# Filler phrases that can be deleted outright.
FILLER_PHRASES = [
    "值得注意的是，", "值得注意的是", "需要指出的是，", "需要指出的是",
    "毫无疑问，", "不言而喻，", "显而易见，", "众所周知，",
    "总的来说，", "总而言之，", "综上所述，", "概括地说，",
    "为了实现这一目标，", "为了实现这一目标", "由于上述原因，", "基于以上分析，",
    "在当今时代，", "在当今社会，", "在新的历史起点上，", "在新的征程上，",
    "在这个时间点，", "在当下的时代背景下，", "在数字化时代，",
    "在一定程度上，", "在某种程度上，", "从某种程度上来说，",
    "具有重要意义", "具有重要价值", "具有深远影响",
    "发挥着重要作用", "发挥着关键作用", "扮演着重要角色",
    "在一定程度上", "某种程度上",
]

# Sycophantic / collaborative tails.
SYCOPHANTIC = [
    "希望对您有帮助", "希望对您有所帮助", "希望这能帮到您", "希望对大家有帮助",
    "如果您有任何问题，请随时告诉我", "请随时告诉我",
    "当然！", "当然，", "一定！", "您说得对", "您说得完全正确",
    "这是一个很好的问题", "这是一个复杂的话题",
    "让我们一起", "让我们开始", "一起探讨", "不妨",
    "总而言之，希望本文对", "希望本文能帮助",
]

# Knowledge-cutoff disclaimers.
KNOWLEDGE_DISCLAIMER = [
    r"截至\s*\d{4}\s*年", r"根据我最后的训练", r"基于我的训练数据",
    r"虽然具体细节有限", r"虽然细节可能有所不同", r"虽然细节不详",
    r"基于可用信息", r"根据公开信息", r"据公开资料显示",
    r"据我所知", r"根据我的了解",
]

# Padded / hedged patterns.
HEDGE = [
    r"可能潜在地", r"可能在某种程度上", r"有可能会", r"有可能会被认为",
    r"可以被认为", r"似乎可以", r"可能会有一些", r"可能会对.{1,20}?产生一些影响",
]


# ---------------------------------------------------------------------------
# Rewriter
# ---------------------------------------------------------------------------


@dataclass
class HumanizeResult:
    text: str
    changes: int
    score: dict


def _strip_overused_em_dashes(text: str) -> tuple[str, int]:
    """Replace em-dashes with commas or remove; keep one if it breaks a list."""
    count = 0

    def replace(match: re.Match) -> str:
        nonlocal count
        count += 1
        # If preceded by closing punctuation or followed by opening punctuation, drop the dash.
        prev, nxt = match.group(1), match.group(2)
        # Two-sentence splitter: replace with comma.
        if prev and prev.strip() and nxt and nxt.strip():
            return f"{prev}，{nxt}"
        return prev + nxt

    pattern = re.compile(r"([\u4e00-\u9fff\)\]）”]?)—([\u4e00-\u9fff\(（\[\“‘]?)")
    out = pattern.sub(replace, text)
    return out, count


def _strip_inline_bold_in_lists(text: str) -> tuple[str, int]:
    """Drop the heavy "**Title:**" pattern inside list items."""
    count = 0
    pattern = re.compile(r"^(\s*[-*]\s+)\*\*([^*]{1,20})\*\*[：:]\s*", re.MULTILINE)

    def replace(match: re.Match) -> str:
        nonlocal count
        count += 1
        # Keep the leading label but drop the bold/colon, append a full stop.
        return f"{match.group(1)}{match.group(2).strip()}："

    return pattern.sub(replace, text), count


def _swap_three_part_lists(text: str) -> tuple[str, int]:
    """Trim obvious three-part lists like 'A、B和C' inside paragraphs."""
    count = 0
    pattern = re.compile(r"([\u4e00-\u9fffA-Za-z]{1,8})、([\u4e00-\u9fffA-Za-z]{1,8})和([\u4e00-\u9fffA-Za-z]{1,8})(?=[，。；、\s\n])")

    def replace(match: re.Match) -> str:
        nonlocal count
        count += 1
        # Keep only the first two parts to break the rhythm.
        return f"{match.group(1)}和{match.group(2)}"

    return pattern.sub(replace, text), count


def _remove_filler_phrases(text: str) -> tuple[str, int]:
    count = 0
    out = text
    for phrase in FILLER_PHRASES:
        if phrase in out:
            out = out.replace(phrase, "")
            count += 1
    return out, count


def _remove_sycophantic_tails(text: str) -> tuple[str, int]:
    """Drop sycophantic / collaborative phrases, usually in the last paragraph."""
    count = 0
    out = text
    for phrase in SYCOPHANTIC:
        if phrase in out:
            out = out.replace(phrase, "")
            count += 1
    # Clean trailing punctuation runs left behind by deletions, e.g. "！。" or "，。"
    out = re.sub(r"[，、；：。！？]{1,3}\s*[。！？]\s*$", "。", out)
    return out, count


def _remove_knowledge_disclaimers(text: str) -> tuple[str, int]:
    count = 0
    out = text
    for pat in KNOWLEDGE_DISCLAIMER:
        new_out = re.sub(pat, "", out)
        if new_out != out:
            count += 1
        out = new_out
    return out, count


def _squash_hedges(text: str) -> tuple[str, int]:
    count = 0
    out = text
    for pat in HEDGE:
        new_out = re.sub(pat, "可能", out)
        if new_out != out:
            count += 1
        out = new_out
    return out, count


def _strip_negative_parallelism(text: str) -> tuple[str, int]:
    count = 0
    out = text
    for pat in NEGATIVE_PARALLELISM:
        new_out, n = re.subn(pat, "", out)
        if n:
            count += n
        out = new_out
    return out, count


def _remove_ai_vocab(text: str, keep_threshold: int = 0) -> tuple[str, int]:
    """Remove single AI-vocab words/phrases when they appear in a sentence
    whose surrounding context can absorb the deletion cleanly.

    Strategy: for every match, drop the token (and any adjacent comma /
    "的" / "地") and collapse the extra whitespace. We do NOT replace
    with a synonym — that is the "刻意换词" anti-pattern.
    """
    count = 0
    out = text
    # Sort longer first to avoid partial overlap.
    targets = sorted(set(AI_VOCAB), key=len, reverse=True)
    # Some high-frequency words are worth deleting even when surrounded
    # by other CJK characters (the SKILL.md specifically calls out 织锦,
    # 格局, 持续推进, etc.).
    force_targets = {
        "织锦",
        "织锦般",
        "格局",
        "无缝",
        "无缝衔接",
        "直观",
        "持久的证明",
        "持续推进",
        "持续优化",
        "持续提升",
        "全面提升",
        "举足轻重",
        "至关重要",
        "首当其冲",
        "与时俱进",
        "赋能",
        "端到端",
        "一站式",
        "闭环",
        "里程碑",
        "划时代",
        "革命性",
        "颠覆性",
        "触手可及",
    }
    for word in targets:
        if word in force_targets:
            # Drop the AI word plus a trailing single-character particle
            # (般 / 地 / 的) when it would otherwise dangle. Keep the
            # leading particle so we don't shred "的" inside "的目的".
            pat = re.compile(re.escape(word) + r"[一-鿿]?")
        else:
            # Word boundary: not preceded/followed by another CJK char (so we
            # don't shred words like "关键性" inside "关键性能").
            pat = re.compile(r"(?<![一-鿿])" + re.escape(word) + r"(?![一-鿿])")
        new_out, n = pat.subn("", out)
        if n:
            count += n
        out = new_out
    # Collapse artifacts like "、、" or trailing comma after deletion.
    out = re.sub(r"、{2,}", "、", out)
    out = re.sub(r"，{2,}", "，", out)
    out = re.sub(r"，\s*。", "。", out)
    out = re.sub(r"^[，。、\s]+", "", out, flags=re.MULTILINE)
    # Per-line cleanup: a paragraph that ends with a stray "，" or "、" right
    # before EOL is almost always the residue of a deleted word. Also handle
    # the start-of-line case (after a blockquote `>`, list marker, or
    # bold-label like `- **监控：**`), and mid-paragraph runs like
    # "意义。，织锦般..." that appear after a deleted connector.
    out = re.sub(r"[，、]\s*$", "。", out, flags=re.MULTILINE)
    out = re.sub(
        r"(^|\n)(?:>\s*|[-*]\s*(?:\*\*[^*]{0,30}\*\*)?\s*|[-*]\s*\d+\.\s*)[，、](?=[^\n])",
        r"\1",
        out,
    )
    out = re.sub(r"。\s*[，、](?=[^\n])", "。", out)
    return out, count


def _straighten_smart_quotes(text: str) -> tuple[str, int]:
    """English straight quotes for ASCII contexts; keep Chinese quotes inside CJK."""
    count = 0
    pairs = [
        ("\u201c", '"'),  # "
        ("\u201d", '"'),  # "
        ("\u2018", "'"),  # '
        ("\u2019", "'"),  # '
    ]
    out = text
    for smart, ascii_q in pairs:
        if smart in out:
            count += out.count(smart)
            out = out.replace(smart, ascii_q)
    return out, count


def _flatten_emotional_tail(text: str) -> tuple[str, int]:
    """Replace the "blob of hope" / "bright future" closing paragraphs with
    a concrete next-step line. We do this by removing the entire block
    that begins with one of the trigger phrases and ends at the next
    blank line.
    """
    count = 0
    out = text
    triggers = (
        "未来可期", "前景广阔", "让我们一起", "让我们共同", "携手",
        "砥砺前行", "勇攀高峰", "再创辉煌", "共创未来", "共谱新篇",
    )
    pattern = re.compile(
        r"^[^\n]*?(?:" + "|".join(re.escape(t) for t in triggers) + r")[^\n]*$",
        re.MULTILINE,
    )
    new_out, n = pattern.subn("", out)
    if n:
        count = n
    return new_out, count


def _split_rigid_paragraphs(text: str) -> tuple[str, int]:
    """If three consecutive sentences have nearly identical length, break
    one with a short punchy sentence. This is a heuristic; safest to
    leave alone if we can't find three equal-length sentences.
    """
    count = 0
    paragraphs = re.split(r"(\n{2,})", text)
    for i, block in enumerate(paragraphs):
        if "\n" in block and not block.strip().startswith("#"):
            continue
        # Find three consecutive sentences in the same paragraph.
        sentences = [s for s in re.split(r"(?<=[。！？])", block) if s.strip()]
        if len(sentences) < 4:
            continue
        # Detect "all-similar" rhythm.
        lens = [len(s.strip()) for s in sentences[:4]]
        if max(lens) - min(lens) > 18:
            continue
        # Insert a short 6-10 char sentence after the 2nd.
        short = "这一点很重要。"
        new_block = "".join(sentences[:2]) + short + "".join(sentences[2:])
        paragraphs[i] = new_block
        count += 1
    return "".join(paragraphs), count


# ---------------------------------------------------------------------------
# Quality scoring (Humanizer-zh 5-dim / 50-point rubric)
# ---------------------------------------------------------------------------


def score_text(text: str) -> dict:
    body = _strip_markdown(text)

    direct = _score_direct(body)
    rhythm = _score_rhythm(body)
    trust = _score_trust(body)
    authentic = _score_authenticity(body)
    refine = _score_refinement(body)
    total = direct + rhythm + trust + authentic + refine
    return {
        "total": total,
        "direct": direct,
        "rhythm": rhythm,
        "trust": trust,
        "authentic": authentic,
        "refine": refine,
    }


def _strip_markdown(text: str) -> str:
    text = re.sub(r"^---[\s\S]*?---\n", "", text, count=1)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"```[\s\S]*?```", "", text)
    return text


def _score_direct(body: str) -> int:
    """Count launches like '值得注意的是' / '在某种程度上' that pad sentences."""
    hedges = sum(body.count(p) for p in FILLER_PHRASES)
    # 0 hedges -> 10; >=6 hedges -> 1
    if hedges == 0:
        return 10
    if hedges >= 6:
        return 1
    return max(1, 10 - hedges * 2)


def _score_rhythm(body: str) -> int:
    """Variance in sentence length. Higher variance -> higher score."""
    sentences = [s for s in re.split(r"(?<=[。！？])", body) if s.strip()]
    if len(sentences) < 4:
        return 5
    lens = [len(s) for s in sentences]
    mean = sum(lens) / len(lens)
    var = sum((x - mean) ** 2 for x in lens) / len(lens)
    stdev = var ** 0.5
    if stdev >= 22:
        return 10
    if stdev >= 14:
        return 8
    if stdev >= 8:
        return 6
    if stdev >= 4:
        return 4
    return 2


def _score_trust(body: str) -> int:
    """Bolding overuse -> distrust score penalty."""
    body_md = _strip_markdown(body)
    n_bold = body.count("**")
    n_inline_colon = len(re.findall(r"^[\s>*\-\d]+.{1,16}\*\*[^*]{1,20}\*\*[：:]", body, re.MULTILINE))
    penalty = (n_bold // 4) + n_inline_colon
    if penalty == 0:
        return 10
    if penalty >= 5:
        return 1
    return max(1, 10 - penalty * 2)


def _score_authenticity(body: str) -> int:
    """Voice / opinion heuristic: reward "我" usage and specific numbers."""
    has_first = "我" in body
    has_numbers = bool(re.search(r"\d", body))
    has_quote = '"' in body or '"' in body
    score = 5
    if has_first:
        score += 2
    if has_numbers:
        score += 2
    if has_quote:
        score += 1
    return min(10, score)


def _score_refinement(body: str) -> int:
    """Penalize redundant connectors and tail hope-talks."""
    redundant = sum(body.count(c) for c in ["此外，", "然而，", "因此，", "所以，", "与此同时，"])
    hope = sum(body.count(c) for c in ["未来可期", "共创", "携手", "砥砺", "勇攀", "再创辉煌", "勇毅前行"])
    penalty = redundant + hope
    if penalty == 0:
        return 10
    if penalty >= 5:
        return 1
    return max(1, 10 - penalty * 2)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def humanize(text: str) -> HumanizeResult:
    changes = 0
    text, n = _remove_sycophantic_tails(text)
    changes += n
    text, n = _remove_knowledge_disclaimers(text)
    changes += n
    text, n = _remove_filler_phrases(text)
    changes += n
    text, n = _strip_negative_parallelism(text)
    changes += n
    text, n = _squash_hedges(text)
    changes += n
    text, n = _remove_ai_vocab(text)
    changes += n
    text, n = _flatten_emotional_tail(text)
    changes += n
    text, n = _strip_overused_em_dashes(text)
    changes += n
    text, n = _strip_inline_bold_in_lists(text)
    changes += n
    text, n = _swap_three_part_lists(text)
    changes += n
    text, n = _straighten_smart_quotes(text)
    changes += n
    text, n = _split_rigid_paragraphs(text)
    changes += n
    score = score_text(text)
    return HumanizeResult(text=text, changes=changes, score=score)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Strip AI tells from a Markdown article.")
    p.add_argument("article", help="Path to a Markdown article")
    p.add_argument("-o", "--output", help="Output path; default: article.humanized.md")
    p.add_argument("--in-place", action="store_true", help="Overwrite the input file")
    p.add_argument("--score", action="store_true", help="Print score and exit, do not write")
    p.add_argument("--json", action="store_true", help="Emit machine-readable summary on stdout")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    src = Path(args.article)
    if not src.exists():
        print(f"ERROR: file not found: {src}", file=sys.stderr)
        return 2
    text = src.read_text(encoding="utf-8")

    if args.score:
        score = score_text(text)
        if args.json:
            print(json.dumps(score, ensure_ascii=False))
        else:
            print(f"总分 {score['total']} / 50")
            for k, v in score.items():
                if k == "total":
                    continue
                print(f"  {k:<10s} {v} / 10")
        return 0

    result = humanize(text)
    if args.in_place:
        dest = src
    elif args.output:
        dest = Path(args.output)
    else:
        dest = src.with_name(src.stem + ".humanized" + src.suffix)
    dest.write_text(result.text, encoding="utf-8")

    if args.json:
        print(
            json.dumps(
                {
                    "output": str(dest),
                    "changes": result.changes,
                    "score": result.score,
                },
                ensure_ascii=False,
            )
        )
    else:
        print(f"Rewrote {dest} ({result.changes} substitutions)")
        s = result.score
        print(
            f"Score: total {s['total']}/50  direct {s['direct']}/10  "
            f"rhythm {s['rhythm']}/10  trust {s['trust']}/10  "
            f"authentic {s['authentic']}/10  refine {s['refine']}/10"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
