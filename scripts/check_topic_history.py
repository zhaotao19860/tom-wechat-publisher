#!/usr/bin/env python3
"""Check a proposed WeChat topic against previously written Markdown articles."""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


DUPLICATE_EXIT = 3
RELATED_EXIT = 4
DEFAULT_DUPLICATE_THRESHOLD = 0.75
DEFAULT_RELATED_THRESHOLD = 0.34
GENERIC_TERMS = {
    "dns",
    "wechat",
    "微信",
    "公众号",
    "文章",
    "分析",
    "解析",
    "风险",
    "安全",
    "如何",
    "为什么",
}


@dataclass(frozen=True)
class Article:
    path: Path
    title: str
    source: str


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1].strip()
    return value


def read_article(path: Path) -> Optional[Article]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None

    title = ""
    source = ""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end >= 0:
            frontmatter = text[3:end]
            title_match = re.search(r"(?mi)^title\s*:\s*(.+?)\s*$", frontmatter)
            source_match = re.search(r"(?mi)^source\s*:\s*(.+?)\s*$", frontmatter)
            if title_match:
                title = _unquote(title_match.group(1))
            if source_match:
                source = _unquote(source_match.group(1))
    if not title:
        heading_match = re.search(r"(?m)^#\s+(.+?)\s*$", text)
        if heading_match:
            title = heading_match.group(1).strip()
    if not title:
        return None
    return Article(path=path.resolve(), title=title, source=source)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def iter_articles(history_roots: Iterable[Path], excludes: Iterable[Path]) -> Iterable[Article]:
    resolved_excludes = [path.resolve() for path in excludes]
    seen = set()
    for root in history_roots:
        root = root.expanduser().resolve()
        if not root.is_dir():
            raise FileNotFoundError("history root is not a directory: {}".format(root))
        for path in sorted(root.rglob("article*.md")):
            resolved = path.resolve()
            if resolved in seen or any(_is_within(resolved, excluded) for excluded in resolved_excludes):
                continue
            seen.add(resolved)
            article = read_article(resolved)
            if article is not None:
                yield article


def canonical_source(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme and parsed.netloc:
        filtered_query = urllib.parse.urlencode(
            [
                (key, item)
                for key, item in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
                if not key.lower().startswith("utm_")
            ]
        )
        return urllib.parse.urlunsplit(
            (
                parsed.scheme.lower(),
                parsed.netloc.lower(),
                parsed.path.rstrip("/"),
                filtered_query,
                "",
            )
        )
    try:
        path = Path(value).expanduser()
        if path.exists():
            return str(path.resolve())
    except OSError:
        pass
    return value.rstrip("/")


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).lower()
    return "".join(char for char in value if char.isalnum())


def _ngrams(value: str, size: int = 2) -> set:
    if not value:
        return set()
    if len(value) <= size:
        return {value}
    return {value[index : index + size] for index in range(len(value) - size + 1)}


def _dice(left: set, right: set) -> float:
    if not left or not right:
        return 0.0
    return (2.0 * len(left & right)) / (len(left) + len(right))


def _terms(value: str) -> set:
    normalized = unicodedata.normalize("NFKC", value).lower()
    latin = set(re.findall(r"[a-z0-9]+(?:[._+-][a-z0-9]+)*", normalized))
    chinese_chunks = re.findall(r"[\u3400-\u9fff]+", normalized)
    chinese = set()
    for chunk in chinese_chunks:
        chinese.update(_ngrams(chunk, 2))
    return {term for term in latin | chinese if term not in GENERIC_TERMS}


def compare_topic(topic: str, title: str) -> tuple:
    normalized_topic = normalize_text(topic)
    normalized_title = normalize_text(title)
    if not normalized_topic or not normalized_title:
        return 0.0, []

    sequence = difflib.SequenceMatcher(None, normalized_topic, normalized_title).ratio()
    character_overlap = _dice(_ngrams(normalized_topic), _ngrams(normalized_title))
    topic_terms = _terms(topic)
    title_terms = _terms(title)
    term_overlap = _dice(topic_terms, title_terms)
    score = max(
        0.65 * sequence + 0.35 * character_overlap,
        0.72 * term_overlap + 0.28 * character_overlap,
    )
    if normalized_topic == normalized_title:
        score = 1.0
    elif normalized_topic in normalized_title or normalized_title in normalized_topic:
        length_ratio = min(len(normalized_topic), len(normalized_title)) / max(
            len(normalized_topic), len(normalized_title)
        )
        score = max(score, 0.72 + 0.28 * length_ratio)

    reasons = []
    if sequence >= 0.70:
        reasons.append("near-title")
    shared_terms = sorted(topic_terms & title_terms, key=lambda item: (-len(item), item))[:6]
    if shared_terms:
        reasons.append("shared-terms:" + ",".join(shared_terms))
    return min(score, 1.0), reasons


def build_report(
    topic: str,
    source: str,
    history_roots: list,
    excludes: list,
    duplicate_threshold: float,
    related_threshold: float,
    limit: int,
) -> dict:
    requested_source = canonical_source(source)
    matches = []
    articles = list(iter_articles(history_roots, excludes))
    for article in articles:
        score, reasons = compare_topic(topic, article.title) if topic else (0.0, [])
        if requested_source and canonical_source(article.source) == requested_source:
            score = 1.0
            reasons = ["same-source"] + reasons
        if score < related_threshold:
            continue
        matches.append(
            {
                "score": round(score, 4),
                "title": article.title,
                "path": str(article.path),
                "source": article.source,
                "reasons": reasons or ["topic-similarity"],
            }
        )
    matches.sort(key=lambda item: (-item["score"], item["path"]))
    matches = matches[:limit]

    status = "clear"
    if matches and matches[0]["score"] >= duplicate_threshold:
        status = "duplicate"
    elif matches:
        status = "related"
    return {
        "status": status,
        "topic": topic,
        "source": source,
        "history_roots": [str(path.expanduser().resolve()) for path in history_roots],
        "scanned": len(articles),
        "matches": matches,
    }


def print_human_report(report: dict) -> None:
    labels = {"clear": "CLEAR", "related": "RELATED", "duplicate": "DUPLICATE"}
    print("Topic history check: {}".format(labels[report["status"]]))
    print("Scanned {} historical article(s).".format(report["scanned"]))
    if not report["matches"]:
        print("No materially similar historical title or source was found.")
        return
    for match in report["matches"]:
        print("- {:.0%} | {}".format(match["score"], match["title"]))
        print("  {}".format(match["path"]))
        print("  reasons: {}".format("; ".join(match["reasons"])))
    if report["status"] == "duplicate":
        print("Stop before drafting. Continue only for an explicitly requested update or sequel with a distinct angle.")
    else:
        print("Review the matches and state the new angle before drafting.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare a proposed WeChat topic with historical article titles and sources."
    )
    parser.add_argument("--topic", default="", help="Chosen article topic or working title")
    parser.add_argument("--source", default="", help="Source URL or local path, when available")
    parser.add_argument(
        "--history-root",
        action="append",
        default=[],
        help="Root containing historical article*.md files; may be repeated",
    )
    parser.add_argument("--exclude", action="append", default=[], help="Path to exclude; may be repeated")
    parser.add_argument("--duplicate-threshold", type=float, default=DEFAULT_DUPLICATE_THRESHOLD)
    parser.add_argument("--related-threshold", type=float, default=DEFAULT_RELATED_THRESHOLD)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    return parser.parse_args()


def emit_run_marker() -> None:
    """Mark the start of a workflow run for tom-workflow-optimize. stderr keeps --json stdout clean."""
    run_id = os.environ.get("WECHAT_RUN_ID") or "{}-{}".format(time.strftime("%Y%m%d%H%M%S"), os.getpid())
    print("[[wf-run start tom-wechat-publisher {}]]".format(run_id), file=sys.stderr)


def main() -> int:
    args = parse_args()
    emit_run_marker()
    if not args.topic.strip() and not args.source.strip():
        print("ERROR: --topic or --source is required", file=sys.stderr)
        return 2
    if not 0 <= args.related_threshold <= args.duplicate_threshold <= 1:
        print("ERROR: thresholds must satisfy 0 <= related <= duplicate <= 1", file=sys.stderr)
        return 2
    if args.limit < 1:
        print("ERROR: --limit must be at least 1", file=sys.stderr)
        return 2

    roots = [Path(value) for value in args.history_root]
    if not roots:
        roots = [Path.home() / "Desktop/wechat"]
    try:
        report = build_report(
            topic=args.topic.strip(),
            source=args.source.strip(),
            history_roots=roots,
            excludes=[Path(value) for value in args.exclude],
            duplicate_threshold=args.duplicate_threshold,
            related_threshold=args.related_threshold,
            limit=args.limit,
        )
    except FileNotFoundError as error:
        print("ERROR: {}".format(error), file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_human_report(report)
    if report["status"] == "duplicate":
        return DUPLICATE_EXIT
    if report["status"] == "related":
        return RELATED_EXIT
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
