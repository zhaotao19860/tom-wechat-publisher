#!/usr/bin/env python3
import argparse
import html
import re
from pathlib import Path

FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n?", re.DOTALL)
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+['\"][^'\"]*['\"])?\)")
LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")


def inline(text: str, base: Path) -> str:
    escaped = html.escape(text, quote=False)

    def image(match: re.Match[str]) -> str:
        alt = html.escape(match.group(1), quote=True)
        src = match.group(2)
        if not src.startswith(("http://", "https://", "data:", "/")):
            # Keep relative paths portable when the preview is written beside the article.
            src = src
        return f'<img src="{html.escape(src, quote=True)}" alt="{alt}">'

    escaped = IMAGE_RE.sub(image, escaped)
    escaped = LINK_RE.sub(r'<a href="\2">\1</a>', escaped)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    return escaped


def render(markdown: str, base: Path) -> str:
    markdown = FRONTMATTER_RE.sub("", markdown)
    output: list[str] = []
    paragraph: list[str] = []
    in_code = False
    code: list[str] = []

    def flush() -> None:
        if paragraph:
            output.append(f"<p>{inline(' '.join(paragraph), base)}</p>")
            paragraph.clear()

    for raw in markdown.splitlines():
        line = raw.rstrip()
        if line.startswith("```"):
            flush()
            if in_code:
                output.append(f"<pre><code>{html.escape(chr(10).join(code))}</code></pre>")
                code.clear()
            in_code = not in_code
            continue
        if in_code:
            code.append(raw)
            continue
        if not line.strip():
            flush()
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            flush()
            level = len(heading.group(1))
            output.append(f"<h{level}>{inline(heading.group(2), base)}</h{level}>")
        elif line.startswith("> "):
            flush()
            output.append(f"<blockquote>{inline(line[2:], base)}</blockquote>")
        elif re.match(r"^[-*+]\s+", line):
            flush()
            list_item = re.sub(r"^[-*+]\s+", "", line)
            output.append(f"<ul><li>{inline(list_item, base)}</li></ul>")
        else:
            paragraph.append(line.strip())
    flush()
    if in_code:
        output.append(f"<pre><code>{html.escape(chr(10).join(code))}</code></pre>")
    return "\n".join(output)


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a local WeChat article preview.")
    parser.add_argument("--article", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--theme", default="dns-daily")
    args = parser.parse_args()
    article = Path(args.article).resolve()
    output = Path(args.output).resolve()
    theme = Path(__file__).resolve().parent.parent / "assets" / f"{args.theme}.css"
    if not article.is_file():
        parser.error(f"article not found: {article}")
    if not theme.is_file():
        parser.error(f"theme not found: {theme}")
    body = render(article.read_text(encoding="utf-8"), article.parent)
    css = theme.read_text(encoding="utf-8")
    document = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>WeChat article preview</title><style>body{{margin:0;background:#eef1f5}}main{{max-width:677px;margin:24px auto;padding:32px 28px;background:#fff;box-shadow:0 1px 8px #0002}}img{{max-width:100%;height:auto}}{css}</style></head><body><main><article id="wechat-article">{body}</article></main></body></html>'''
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
