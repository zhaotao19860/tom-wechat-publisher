#!/usr/bin/env python3
"""Check article shape and speaking stance that check_prose.py does not cover.

Standard library only; run with ``python3 -I``. check_prose.py blocks banned
words and sentence templates. This script counts what it cannot see: stock
frames the model grew out of human-writing's own advice, conclusion-style H2
headings, first-person experiences with no source, and the even rhythm of
sentences, sections and paragraph endings.

Usage::

    voice_check.py article.md [--facts voice-facts.md] [--json] [--mode warn|block]

Findings are either *fails* (stock frames, template H2, unsourced first-person
claims) or *warns* (everything statistical, plus first-person claims that no
line of the facts file covers). Exit codes:

* ``block`` (default): 1 when there is at least one fail, else 0.
* ``warn``: always 0; findings are still printed.
* 2 when the article cannot be read (either mode).

The facts file is the account's list of first-person facts the author has
confirmed, one per line (Markdown bullets, headings and ``>`` notes are
ignored). A claim sentence is covered when one fact line contains it, or when
the claim clause shares enough keywords with one fact line.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Thresholds. Calibrated on the author's 2021 blog (0 fails required) and the
# 2026-10/11 WeChat articles; see CHANGELOG.md 2026-11-22.
# ---------------------------------------------------------------------------
SENTENCE_CV_MIN = 0.55        # author blog 0.85, chat 0.90; AI drafts 0.38-0.52
SENTENCE_MIN_COUNT = 30       # fewer sentences make the CV meaningless
SECTION_CV_MIN = 0.45         # human median 0.83, AI 0.31 (lieflat paired data)
SECTION_MIN_COUNT = 3
H2_MAX = 4
QUESTION_MIN_HAN = 1200       # only long articles must ask at least once
JUDGMENT_END_RATIO_MAX = 0.5  # share of paragraphs closing on a short verdict
JUDGMENT_END_MAX_HAN = 18
JUDGMENT_MIN_PARAGRAPHS = 6   # needs this many multi-sentence paragraphs
ENUMERATION_PER_K_MAX = 12.0  # 、 per 1000 Han characters; author blog 9.6
GLOSS_MIN = 2
FACT_COVERAGE_MIN = 0.6       # share of claim keywords found in one fact line
FACT_SHARED_MIN = 2

HAN = re.compile(r"[一-鿿]")
STOCK = re.compile(
    r"写得(?:很|特别)(?:硬|直接|直白|实在|清楚)|说得(?:很|也很)?直白|也很直白|到这里为止|分得清清楚楚"
    r"|记一句就够了|一次讲清|我原本以为|我原先|原先没想到|我一开始以为|我以前以为|手痒|说到底"
    r"|这是我想要的结果|再往下想一步|反过来看"
)
TEMPLATE_H2 = re.compile(r"^#{2,3}\s+[^\n，]{2,14}，[^\n]{0,16}(?:才|就|还得|也|再)[^\n]*$", re.M)
CLAIM_FAIL = re.compile(r"我(?:见过的人|第一件事就是|自己长期|长期固定|第一个打开的就是|每次都先)")
CLAIM_CHECK = re.compile(
    r"我(?:在本机|在公司|用本机|找了|翻了|试了|测了|跑了|查了|对过|做了十年|做了 ?\d+ ?年|做四层|当时其实|九月写过)"
)
GLOSS = re.compile(r"这种做法叫|全称是|简称|指的是|也就是(?:说)?[^，。]{0,6}(?:信息|协议|记录)")
JUDGMENT_MARKERS = frozenset("就才不没都算只也得")
REFERENCE_HEADING = re.compile(r"^#{2,4}\s*(?:参考资料|参考文档|参考链接|References)\s*$", re.M)
CLAUSE_END = re.compile(r"[，。；！？,;!?\n]")
ASCII_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_.+-]*|\d+(?:\.\d+)*")
# Function characters that carry no fact; they must not count as overlap.
STOP_HAN = frozenset("我你他她它们的了在是有和与及或也都就才还又再很太这那个一不没把被着过地得之其")
LIST_MARKER = re.compile(r"^(?:[-*+]|\d+[.、)])\s*")

STAT_LABELS = {
    "han": "汉字",
    "sentences": "句数",
    "sentence_cv": "句长CV",
    "section_cv": "节长CV",
    "h2": "H2",
    "questions": "问号",
    "enumeration_per_k": "顿号每千字",
    "judgment_endings": "段尾短判断",
}


def han_count(text: str) -> int:
    return len(HAN.findall(text))


def clean(text: str) -> str:
    """Drop front matter, references, code, images, HTML, URLs and link targets."""
    text = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S)
    text = REFERENCE_HEADING.split(text)[0]
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)|<[^>]+>|`[^`]*`|https?://\S+", "", text)
    return re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)


def paragraphs(text: str) -> list[str]:
    """Prose paragraphs: no headings, tables or lists; quotes are unwrapped."""
    out = []
    for block in re.split(r"\n\s*\n", text):
        block = re.sub(r"^>\s?", "", block.strip(), flags=re.M).strip()
        if not block or block.startswith(("#", "|")) or re.match(r"^(?:[-*+]|\d+[.、])\s", block):
            continue
        if han_count(block) >= 8:
            out.append(block)
    return out


def sentences(paragraph: str) -> list[str]:
    return [s for s in re.findall(r"[^。！？?!\n]+[。！？?!]?", paragraph) if han_count(s) >= 2]


def prose_lines(text: str) -> list[str]:
    """Every non-heading, non-table line, with list and quote markers removed."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "|")):
            continue
        line = LIST_MARKER.sub("", re.sub(r"^>\s?", "", line)).strip()
        if line:
            out.append(line)
    return out


def cv(values: list[int]) -> float:
    mean = statistics.mean(values)
    return statistics.pstdev(values) / mean if mean else 0.0


def keywords(text: str) -> set[str]:
    words = {m.group().lower().strip(".-+") for m in ASCII_TOKEN.finditer(text)}
    words |= {c for c in HAN.findall(text) if c not in STOP_HAN}
    words.discard("")
    return words


def load_facts(path: Path) -> list[str]:
    facts = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ">")):
            continue
        line = LIST_MARKER.sub("", line).strip()
        if line:
            facts.append(line)
    return facts


def claim_clause(sentence: str, start: int) -> str:
    end = CLAUSE_END.search(sentence, start + 1)
    return sentence[start : end.start() if end else len(sentence)]


def fact_covers(sentence: str, clause: str, facts: list[str]) -> bool:
    if any(fact in sentence for fact in facts):
        return True
    wanted = keywords(clause)
    if not wanted:
        return False
    for fact in facts:
        shared = wanted & keywords(fact)
        if len(shared) >= FACT_SHARED_MIN and len(shared) / len(wanted) >= FACT_COVERAGE_MIN:
            return True
    return False


def is_judgment_ending(sentence: str) -> bool:
    return han_count(sentence) <= JUDGMENT_END_MAX_HAN and any(c in JUDGMENT_MARKERS for c in sentence)


def _finding(rule: str, message: str) -> dict[str, str]:
    return {"rule": rule, "message": message}


def check(raw: str, facts: list[str] | None = None) -> tuple[list[dict], list[dict], dict]:
    """Return (fails, warns, stats) for one Markdown article."""
    facts = facts or []
    text = clean(raw)
    paras = paragraphs(text)
    sents = [s for p in paras for s in sentences(p)]
    han = han_count(text)
    fails: list[dict] = []
    warns: list[dict] = []
    stats: dict[str, object] = {"han": han, "sentences": len(sents)}

    lengths = [han_count(s) for s in sents]
    if len(lengths) >= SENTENCE_MIN_COUNT:
        value = round(cv(lengths), 2)
        stats["sentence_cv"] = value
        if value < SENTENCE_CV_MIN:
            warns.append(_finding("sentence_cv", f"句长CV {value}，低于 {SENTENCE_CV_MIN}，句子长度太匀。作者博客 0.85，会话 0.90。"))

    section_lengths = [han_count(x) for x in re.split(r"^## .*$", text, flags=re.M) if han_count(x) > 0]
    if len(section_lengths) >= SECTION_MIN_COUNT:
        value = round(cv(section_lengths), 2)
        stats["section_cv"] = value
        if value < SECTION_CV_MIN:
            warns.append(_finding("section_cv", f"节长CV {value}，低于 {SECTION_CV_MIN}，各节差不多长。"))

    headings = re.findall(r"^## (.*)$", text, flags=re.M)
    stats["h2"] = len(headings)
    if len(headings) > H2_MAX:
        warns.append(_finding("h2_count", f"H2 {len(headings)} 个，多于 {H2_MAX} 个。"))

    for match in STOCK.finditer(text):
        fails.append(_finding("stock_frame", f"套话框架「{match.group()}」"))
    for match in TEMPLATE_H2.finditer(text):
        fails.append(_finding("template_h2", f"结论句小标题「{match.group().strip('# ')}」"))

    for line in prose_lines(text):
        for sentence in sentences(line):
            snippet = sentence.strip()[:40]
            if CLAIM_FAIL.search(sentence):
                fails.append(_finding("claim_unsourced", f"无出处亲历「{snippet}」"))
                continue
            match = CLAIM_CHECK.search(sentence)
            if match and not fact_covers(sentence, claim_clause(sentence, match.start()), facts):
                warns.append(_finding("claim_unverified", f"第一人称经历待核对 voice_facts「{snippet}」"))

    multi = [sentences(p) for p in paras if len(sentences(p)) >= 2]
    endings = sum(1 for ss in multi if is_judgment_ending(ss[-1]))
    stats["judgment_endings"] = f"{endings}/{len(multi)}"
    if len(multi) >= JUDGMENT_MIN_PARAGRAPHS and endings / len(multi) > JUDGMENT_END_RATIO_MAX:
        warns.append(_finding(
            "judgment_endings",
            f"{endings}/{len(multi)} 段用一句 {JUDGMENT_END_MAX_HAN} 字以内的短判断收尾，超过一半。只留最有材料的一两处。",
        ))

    density = round(text.count("、") * 1000 / max(han, 1), 1)
    stats["enumeration_per_k"] = density
    if density > ENUMERATION_PER_K_MAX:
        warns.append(_finding("enumeration_density", f"顿号每千字 {density}，并列清单偏密（作者博客 9.6）。"))

    questions = text.count("？") + text.count("?")
    stats["questions"] = questions
    if han >= QUESTION_MIN_HAN and questions == 0:
        warns.append(_finding("no_question", f"全文 {han} 汉字没有一个问号。作者说话每千字约 12 个问号。"))

    glosses = GLOSS.findall(text)
    if len(glosses) >= GLOSS_MIN:
        warns.append(_finding("gloss", f"给同行解释术语 {len(glosses)} 处（{'、'.join(glosses)}）。"))

    return fails, warns, stats


def exit_code(fails: list[dict], mode: str) -> int:
    return 1 if fails and mode == "block" else 0


def render_text(fails: list[dict], warns: list[dict], stats: dict, mode: str) -> str:
    lines = ["  ".join(f"{STAT_LABELS.get(k, k)} {v}" for k, v in stats.items())]
    if fails:
        lines += ["", "需要修改"] + [f"- {item['message']}" for item in fails]
    if warns:
        lines += ["", "需要人工判断"] + [f"- {item['message']}" for item in warns]
    if not fails and not warns:
        lines += ["", "未发现这份检查器覆盖的问题。"]
    elif fails and mode == "warn":
        lines += ["", "warn 模式：上面的失败项只提醒，不拦截。"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check article shape and first-person claims (stdlib only).")
    parser.add_argument("path", help="article.md")
    parser.add_argument("--facts", help="voice-facts.md: one confirmed first-person fact per line")
    parser.add_argument("--json", action="store_true", help="print a JSON report")
    parser.add_argument("--mode", choices=("warn", "block"), default="block",
                        help="block exits 1 on fails (default); warn always exits 0")
    args = parser.parse_args(argv)

    try:
        raw = Path(args.path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        print(f"ERROR: cannot read article {args.path}: {exc}", file=sys.stderr)
        return 2

    facts: list[str] = []
    if args.facts:
        try:
            facts = load_facts(Path(args.facts))
        except (OSError, UnicodeError) as exc:
            # A missing facts file only makes claim checks stricter, so keep going.
            print(f"WARNING: cannot read facts file {args.facts}: {exc}; first-person claims are unmatched.",
                  file=sys.stderr)

    fails, warns, stats = check(raw, facts)
    code = exit_code(fails, args.mode)
    if args.json:
        report = {"path": args.path, "mode": args.mode, "stats": stats, "fails": fails, "warns": warns,
                  "exit_code": code}
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_text(fails, warns, stats, args.mode))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
