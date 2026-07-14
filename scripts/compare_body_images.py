#!/usr/bin/env python3
"""Generate side-by-side body image candidates for WeChat articles.

Reads an existing image_specs.json and produces both deterministic HTML body
images and AI Guizang-style body images. Covers are intentionally skipped.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import generate_article as ga  # type: ignore
import html_render  # type: ignore


def _load_specs(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("image specs must be a JSON array")
    return [item for item in data if isinstance(item, dict)]


def _article_title(specs: list[dict], fallback: str) -> str:
    for spec in specs:
        if str(spec.get("kind") or "") == "cover" and spec.get("title_zh"):
            return str(spec["title_zh"])
    for spec in specs:
        if spec.get("title_zh"):
            return str(spec["title_zh"])
    return fallback


def _render_html_candidate(spec: dict, idx: int, html_root: Path) -> tuple[Path, list[str], int]:
    html_images = html_root / "images"
    html_pages = html_root / "html"
    html_images.mkdir(parents=True, exist_ok=True)
    html_pages.mkdir(parents=True, exist_ok=True)

    filename = f"{spec.get('filename', f'slot_{idx}')}.png"
    html_path = html_pages / f"{spec.get('filename', f'slot_{idx}')}.html"
    png_path = html_images / filename
    html_path.write_text(html_render.render_html_for_spec(spec), encoding="utf-8")
    width, height = html_render.canvas_for_kind(str(spec.get("kind") or "summary"))
    if not html_render.render_png(html_path, png_path, width, height):
        return png_path, [f"HTML render failed; inspect {html_path}"], 0
    ga.normalize_wechat_image(png_path)
    warnings = ga._file_health_warnings(png_path)
    if not warnings:
        warnings = ["deterministic labels from image_specs.json; text accuracy expected"]
    return png_path, warnings, 95


def _render_ai_candidate(spec: dict, idx: int, ai_root: Path, article_title: str, resolution: str) -> tuple[Path | None, list[str], int, Path]:
    ai_images = ai_root / "images"
    prompt_dir = ai_root / "prompts"
    ai_images.mkdir(parents=True, exist_ok=True)
    prompt_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{spec.get('filename', f'slot_{idx}')}.png"
    prompt = ga._build_image_prompt(spec, article_title)
    prompt_path = prompt_dir / f"{idx:02d}-{spec.get('filename', f'slot_{idx}')}.md"
    prompt_path.write_text(prompt, encoding="utf-8")

    cwd = os.getcwd()
    try:
        os.chdir(ai_root)
        ok = ga.run_create_image(prompt, filename, aspect="16:9", resolution=resolution)
    finally:
        os.chdir(cwd)

    png_path = ai_images / filename
    if not ok or not png_path.exists():
        return None, ["AI generation failed or output missing"], 0, prompt_path
    ga.normalize_wechat_image(png_path)
    ocr_warnings, _ = ga._ocr_warnings_for_image(png_path, spec)
    warnings = ga._unique_warnings([*ocr_warnings, *ga._file_health_warnings(png_path)])
    score = ga._candidate_score("create-image guizang", spec, png_path, warnings)
    return png_path, warnings, score, prompt_path


def _recommend(html_score: int, html_warnings: list[str], ai_score: int, ai_warnings: list[str], ai_path: Path | None) -> tuple[str, str]:
    if ai_path is None:
        return "html", "AI 未生成成功，HTML 版可稳定使用。"
    severe_ai = any("stray text" in w.lower() or "garbled" in w.lower() or "too small" in w.lower() for w in ai_warnings)
    severe_html = any("failed" in w.lower() or "too small" in w.lower() for w in html_warnings)
    if severe_ai and not severe_html:
        return "html", "AI 版存在文字或文件健康风险，正文发布优先选 HTML。"
    if ai_score >= html_score + 8 and not severe_ai:
        return "ai", "AI 版视觉表现更好，且未发现阻塞级风险。"
    if html_score >= ai_score and not severe_html:
        return "html", "HTML 版文字准确性更稳，适合正文技术图。"
    return "ai", "两者都可用，AI 版更适合作为视觉增强候选。"


def _write_comparison(out_dir: Path, rows: list[dict]) -> Path:
    lines = [
        "# Body Image Comparison",
        "",
        "同一组正文配图的 HTML 版和 AI Guizang 版候选。封面未参与比较。",
        "",
        "## Recommendation",
        "",
    ]
    ai_count = sum(1 for row in rows if row["recommend"] == "ai")
    html_count = sum(1 for row in rows if row["recommend"] == "html")
    overall = "html" if html_count >= ai_count else "ai"
    if overall == "html":
        lines.append("整体推荐：`HTML 正文图`，因为正文技术图文字准确性更稳定。可挑选个别 AI 图替换视觉要求更强的段落。")
    else:
        lines.append("整体推荐：`AI Guizang 正文图`，因为多数候选在视觉表现和校验分数上更优。仍需人工检查中文标签。")
    lines.extend(["", "## Per Image", ""])
    for row in rows:
        lines.extend([
            f"### {row['title']}",
            "",
            f"- HTML: `{row['html']}` score={row['html_score']}",
            f"- AI: `{row['ai'] or 'not generated'}` score={row['ai_score']}",
            f"- 推荐: `{row['recommend']}`",
            f"- 原因: {row['reason']}",
        ])
        if row["html_warnings"]:
            lines.append(f"- HTML notes: {'; '.join(row['html_warnings'][:3])}")
        if row["ai_warnings"]:
            lines.append(f"- AI notes: {'; '.join(row['ai_warnings'][:4])}")
        lines.append("")
    path = out_dir / "comparison.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Generate HTML and AI body image candidates")
    parser.add_argument("specs", type=Path, help="image_specs.json")
    parser.add_argument("--out-dir", type=Path, default=None, help="comparison output directory")
    parser.add_argument("--article-title", default="WeChat technical article")
    parser.add_argument("--resolution", default=os.environ.get("TOM_WECHAT_CREATE_IMAGE_RESOLUTION", "2K"))
    parser.add_argument("--skip-ai", action="store_true")
    args = parser.parse_args(argv)

    specs = _load_specs(args.specs)
    body_specs = [spec for spec in specs if str(spec.get("kind") or "") != "cover"]
    if not body_specs:
        raise SystemExit("no body specs found")
    out_dir = args.out_dir or args.specs.parent / "body-image-comparison"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    title = _article_title(specs, args.article_title)
    rows: list[dict] = []
    for idx, spec in enumerate(body_specs, start=1):
        html_path, html_warnings, html_score = _render_html_candidate(spec, idx, out_dir / "html")
        ai_path: Path | None = None
        ai_warnings: list[str] = ["AI generation skipped"] if args.skip_ai else []
        ai_score = 0
        prompt_path: Path | None = None
        if not args.skip_ai:
            ai_path, ai_warnings, ai_score, prompt_path = _render_ai_candidate(spec, idx, out_dir / "ai", title, args.resolution)
        rec, reason = _recommend(html_score, html_warnings, ai_score, ai_warnings, ai_path)
        rows.append({
            "title": spec.get("title_zh") or spec.get("filename") or f"slot {idx}",
            "html": str(html_path.relative_to(out_dir)),
            "html_score": html_score,
            "html_warnings": html_warnings,
            "ai": str(ai_path.relative_to(out_dir)) if ai_path else "",
            "ai_score": ai_score,
            "ai_warnings": ai_warnings,
            "ai_prompt": str(prompt_path.relative_to(out_dir)) if prompt_path else "",
            "recommend": rec,
            "reason": reason,
        })
        print(f"{idx}. {spec.get('filename')}: recommend={rec} html={html_score} ai={ai_score}")

    comparison = _write_comparison(out_dir, rows)
    print(f"wrote {comparison}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
