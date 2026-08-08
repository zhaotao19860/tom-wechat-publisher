from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module


def assert_generated_quatrain(test: unittest.TestCase, lines: list[str]) -> None:
    test.assertEqual(len(lines), 4)
    test.assertEqual(len({len(line) for line in lines}), 1)
    test.assertIn(len(lines[0]), {5, 7})
    for line in lines:
        test.assertRegex(line, r"^[\u4e00-\u9fff]{5,7}$")


class ArticleAssetTests(unittest.TestCase):
    def test_valid_article_has_no_asset_errors(self) -> None:
        validator = load_script("validate_article_assets.py")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "images").mkdir()
            (root / "images" / "cover.jpg").write_bytes(b"jpeg")
            article = root / "article.md"
            article.write_text(
                """---
title: '示例'
cover: 'images/cover.jpg'
cover_generation: 'ai'
cover_style: '青绿山水'
cover_poem:
  - '旧名已散新名渡'
  - '一纸回音半字讹'
cover_typography: 'horizontal'
---
# 示例
""",
                encoding="utf-8",
            )
            self.assertEqual(validator.validate_article(article), [])

    def test_invalid_cover_metadata_reports_all_contract_errors(self) -> None:
        validator = load_script("validate_article_assets.py")
        with tempfile.TemporaryDirectory() as temp:
            article = Path(temp) / "article.md"
            article.write_text(
                """---
title: '示例'
cover: 'missing.png'
cover_generation: 'html'
cover_style: '赛博朋克'
cover_poem:
  - '只有一句'
cover_typography: 'diagonal'
---
""",
                encoding="utf-8",
            )
            errors = validator.validate_article(article)
            joined = "\n".join(errors)
            self.assertIn("cover_generation", joined)
            self.assertIn("cover_style", joined)
            self.assertIn("cover_poem", joined)
            self.assertIn("cover_typography", joined)
            self.assertIn("cover file", joined)

    def test_image_spec_uses_frontmatter_cover_and_poem(self) -> None:
        image_spec = load_script("image_spec.py")
        with tempfile.TemporaryDirectory() as temp:
            article = Path(temp) / "article.md"
            article.write_text(
                """---
title: '示例'
cover: 'images/cover-ai-v2.jpg'
cover_generation: 'ai'
cover_style: '青绿山水'
cover_poem:
  - '旧名已散新名渡'
  - '一纸回音半字讹'
cover_typography: 'horizontal'
---
# 示例

## 原理
正文。
""",
                encoding="utf-8",
            )
            cover = image_spec.derive_specs(article)[0].to_dict()
            self.assertEqual(cover["filename"], "cover-ai-v2")
            self.assertEqual(cover["cover_text"], ["旧名已散新名渡", "一纸回音半字讹"])
            self.assertEqual(cover["cover_style"], "青绿山水")
            self.assertEqual(cover["cover_typography"], "horizontal")

    def test_generated_cover_poems_are_four_line_quatrains(self) -> None:
        image_spec = load_script("image_spec.py")
        cases = [
            ("DMARC 与 DNSSEC", [("原理", "Compact Denial 与 NXNAME 处理空名")]),
            ("DNS 免费服务", [("收费", "基础设施成本与 SLA 付费")]),
            ("普通技术文章", [("现象", "团队在夜间核对服务记录")]),
        ]
        for title, sections in cases:
            with self.subTest(title=title):
                assert_generated_quatrain(self, image_spec._cover_poem_lines(title, sections))

    def test_cover_prompt_requires_all_four_poem_lines(self) -> None:
        generate_article = load_script("generate_article.py")
        lines = ["山水开新卷", "风云入远汀", "孤灯查旧录", "一问见分明"]
        prompt = generate_article._build_image_prompt(
            {"kind": "cover", "cover_text": lines},
            "测试文章",
        )
        for line in lines:
            self.assertIn(line, prompt)
        self.assertIn("exactly four lines", prompt)
        self.assertNotIn("prefer fewer", prompt)
        self.assertNotIn("two lines", prompt)

    def test_ocr_does_not_require_title_for_no_text_ai_slot(self) -> None:
        ocr = load_script("ocr_validate.py")
        spec = {
            "kind": "summary",
            "title_zh": "这只是文章中的图题",
            "render_mode": "ai",
            "fact_risk": "low",
            "labels": [],
            "items": [],
        }
        with mock.patch.object(ocr, "run_ocr", return_value=[]):
            warnings, _ = ocr.validate(Path("unused.png"), spec)
        self.assertEqual(warnings, [])

    def test_ocr_accepts_deterministic_template_chrome(self) -> None:
        ocr = load_script("ocr_validate.py")
        spec = {
            "kind": "flow",
            "title_zh": "处理路径",
            "source_section": "触发链路",
            "structure": "pipeline",
            "render_mode": "html",
            "items": [{"step": "1", "label": "查询", "sub": "开始"}],
        }
        lines = ["触发链路", "PROCESS PATH", "S11 / WECHAT INFOGRAPHIC", "处理路径", "查询", "开始"]
        with mock.patch.object(ocr, "run_ocr", return_value=lines):
            warnings, _ = ocr.validate(Path("unused.png"), spec)
        self.assertEqual(warnings, [])

    def test_ocr_checks_cover_poem_instead_of_article_title(self) -> None:
        ocr = load_script("ocr_validate.py")
        spec = {
            "kind": "cover",
            "title_zh": "文章标题不应成为封面必填文字",
            "cover_text": ["旧名已散新名渡", "一纸回音半字讹"],
            "render_mode": "ai",
            "fact_risk": "low",
        }
        with mock.patch.object(ocr, "run_ocr", return_value=["旧名已散新名渡"]):
            warnings, _ = ocr.validate(Path("unused.png"), spec)
        self.assertEqual(warnings, ["missing cover poem in OCR: expected '一纸回音半字讹'"])


if __name__ == "__main__":
    unittest.main()
