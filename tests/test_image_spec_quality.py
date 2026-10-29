from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path("/usr/bin/python3")


class ImageSpecQualityTests(unittest.TestCase):
    def test_intro_image_gets_a_body_spec(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "images").mkdir()
            (root / "images" / "intro.png").write_bytes(b"png")
            article = root / "article.md"
            article.write_text(
                """---
title: '示例'
---
# 示例
开场。
![开场隐喻](images/intro.png)

## 原理
正文。
""",
                encoding="utf-8",
            )
            out = root / "image_specs.json"
            result = subprocess.run(
                [str(PYTHON), str(ROOT / "scripts/image_spec.py"), str(article), "--out", str(out)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            specs = json.loads(out.read_text(encoding="utf-8"))
            intro = next(spec for spec in specs if spec["filename"] == "intro")
            self.assertEqual(intro["source_section"], "")

    def test_first_body_spec_defaults_to_xiaohei_ai_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "images").mkdir()
            (root / "images" / "intro.png").write_bytes(b"png")
            article = root / "article.md"
            article.write_text(
                """---
title: '示例'
---
# 示例
开场。

## 运维清单
![开场隐喻](images/intro.png)
""",
                encoding="utf-8",
            )
            out = root / "image_specs.json"
            result = subprocess.run(
                [str(PYTHON), str(ROOT / "scripts/image_spec.py"), str(article), "--out", str(out)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            specs = json.loads(out.read_text(encoding="utf-8"))
            first = [spec for spec in specs if spec["kind"] != "cover"][0]
            self.assertEqual(first["render_mode"], "ai")
            self.assertEqual(first["fact_risk"], "low")
            self.assertEqual(first["illustration_backend"], "ian-xiaohei-illustrations")

    def test_validator_rejects_generic_labels_and_missing_locked_facts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "images").mkdir()
            for name in ("one.png", "two.png", "three.png"):
                (root / "images" / name).write_bytes(b"png")
            article = root / "article.md"
            article.write_text(
                """# 示例
## 第一节
![一](images/one.png)
## 第二节
![二](images/two.png)
## 第三节
![三](images/three.png)
""",
                encoding="utf-8",
            )
            specs = [
                {"filename": "one", "kind": "summary", "source_section": "第一节", "render_mode": "ai", "fact_risk": "low", "illustration_backend": "ian-xiaohei-illustrations", "rendered_by": "ian-xiaohei-illustrations:create-image", "labels": ["结果是什么"], "items": [{"label": "结果是什么"}]},
                {"filename": "two", "kind": "checklist", "source_section": "第二节", "render_mode": "html", "fact_risk": "high", "illustration_backend": "html-render", "rendered_by": "html-render", "labels": ["9.20.24"], "items": [{"label": "升级", "sub": "9.20.24"}], "locked_facts": []},
                {"filename": "three", "kind": "summary", "source_section": "第三节", "render_mode": "ai", "fact_risk": "low", "illustration_backend": "ian-xiaohei-illustrations", "rendered_by": "ian-xiaohei-illustrations:create-image", "labels": [], "items": []},
            ]
            spec_path = root / "image_specs.json"
            spec_path.write_text(json.dumps(specs, ensure_ascii=False), encoding="utf-8")
            result = subprocess.run(
                [str(PYTHON), str(ROOT / "scripts/validate_body_images.py"), str(article), "--spec", str(spec_path), "--min", "3"],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("generic labels", result.stderr + result.stdout)
            self.assertIn("locked_facts", result.stderr + result.stdout)

    def test_validator_rejects_non_xiaohei_first_body_image(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "images").mkdir()
            for name in ("facts.png", "scene.png", "actions.png"):
                (root / "images" / name).write_bytes(b"png")
            article = root / "article.md"
            article.write_text(
                """# 示例
开场。
![事实图](images/facts.png)

## 原理
![小黑场景](images/scene.png)

## 行动
![行动图](images/actions.png)
""",
                encoding="utf-8",
            )
            specs = [
                {"filename": "facts", "kind": "impact", "source_section": "", "render_mode": "html", "fact_risk": "high", "illustration_backend": "html-render", "rendered_by": "html-render", "labels": ["9 个 CVE"], "items": [], "locked_facts": ["9 个 CVE"]},
                {"filename": "scene", "kind": "summary", "source_section": "原理", "render_mode": "ai", "fact_risk": "low", "illustration_backend": "ian-xiaohei-illustrations", "rendered_by": "ian-xiaohei-illustrations/create-image", "labels": [], "items": []},
                {"filename": "actions", "kind": "checklist", "source_section": "行动", "render_mode": "html", "fact_risk": "high", "illustration_backend": "html-render", "rendered_by": "html-render", "labels": ["升级"], "items": [], "locked_facts": ["升级"]},
            ]
            spec_path = root / "image_specs.json"
            spec_path.write_text(json.dumps(specs, ensure_ascii=False), encoding="utf-8")
            result = subprocess.run(
                [str(PYTHON), str(ROOT / "scripts/validate_body_images.py"), str(article), "--spec", str(spec_path), "--min", "3"],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("first body image must be an Ian Xiaohei ai + low illustration", result.stderr + result.stdout)

    def test_validator_accepts_xiaohei_as_intro_body_image(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "images").mkdir()
            for name in ("scene.png", "facts.png", "actions.png"):
                (root / "images" / name).write_bytes(b"png")
            article = root / "article.md"
            article.write_text(
                """# 示例
开场。
![小黑场景](images/scene.png)

## 原理
![事实图](images/facts.png)

## 行动
![行动图](images/actions.png)
""",
                encoding="utf-8",
            )
            specs = [
                {"filename": "scene", "kind": "summary", "source_section": "", "render_mode": "ai", "fact_risk": "low", "illustration_backend": "ian-xiaohei-illustrations", "rendered_by": "ian-xiaohei-illustrations/create-image", "labels": [], "items": []},
                {"filename": "facts", "kind": "impact", "source_section": "原理", "render_mode": "html", "fact_risk": "high", "illustration_backend": "html-render", "rendered_by": "html-render", "labels": ["9 个 CVE"], "items": [], "locked_facts": ["9 个 CVE"]},
                {"filename": "actions", "kind": "checklist", "source_section": "行动", "render_mode": "html", "fact_risk": "high", "illustration_backend": "html-render", "rendered_by": "html-render", "labels": ["升级"], "items": [], "locked_facts": ["升级"]},
            ]
            spec_path = root / "image_specs.json"
            spec_path.write_text(json.dumps(specs, ensure_ascii=False), encoding="utf-8")
            result = subprocess.run(
                [str(PYTHON), str(ROOT / "scripts/validate_body_images.py"), str(article), "--spec", str(spec_path), "--min", "3"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def _footer_case(self, tail: str, footer_name: str = "footer-guofeng-seal.png",
                     footer_spec: bool = False) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "images").mkdir()
            for name in ("scene.png", "facts.png", "actions.png", footer_name):
                (root / "images" / name).write_bytes(b"png")
            article = root / "article.md"
            article.write_text(
                """# 示例
开场。
![小黑场景](images/scene.png)

## 原理
![事实图](images/facts.png)

## 行动
![行动图](images/actions.png)
""" + tail,
                encoding="utf-8",
            )
            specs = [
                {"filename": "scene", "kind": "summary", "source_section": "", "render_mode": "ai", "fact_risk": "low", "illustration_backend": "ian-xiaohei-illustrations", "rendered_by": "ian-xiaohei-illustrations/create-image", "labels": [], "items": []},
                {"filename": "facts", "kind": "impact", "source_section": "原理", "render_mode": "html", "fact_risk": "high", "illustration_backend": "html-render", "rendered_by": "html-render", "labels": ["9 个 CVE"], "items": [], "locked_facts": ["9 个 CVE"]},
                {"filename": "actions", "kind": "checklist", "source_section": "行动", "render_mode": "html", "fact_risk": "high", "illustration_backend": "html-render", "rendered_by": "html-render", "labels": ["升级"], "items": [], "locked_facts": ["升级"]},
            ]
            if footer_spec:
                specs.append({"filename": "footer-guofeng-seal", "kind": "summary", "render_mode": "html", "fact_risk": "low", "illustration_backend": "existing-asset"})
            spec_path = root / "image_specs.json"
            spec_path.write_text(json.dumps(specs, ensure_ascii=False), encoding="utf-8")
            return subprocess.run(
                [str(PYTHON), str(ROOT / "scripts/validate_body_images.py"), str(article), "--spec", str(spec_path), "--min", "3"],
                capture_output=True,
                text=True,
            )

    def test_validator_accepts_footer_as_last_image_without_spec(self) -> None:
        result = self._footer_case("\n## 参考资料\n- 链接\n\n![](images/footer-guofeng-seal.png)\n")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("footer present", result.stdout)

    def test_validator_ignores_legacy_footer_spec_entry(self) -> None:
        result = self._footer_case("\n![](images/footer-guofeng-seal.png)\n", footer_spec=True)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_validator_rejects_footer_before_body_images_end(self) -> None:
        result = self._footer_case("\n![](images/footer-guofeng-seal.png)\n\n## 尾声\n![行动图](images/actions.png)\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must appear once, as the last image", result.stderr + result.stdout)

    def test_validator_rejects_legacy_footer(self) -> None:
        result = self._footer_case("\n![](images/wechat.jpg)\n", footer_name="wechat.jpg")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("legacy footer image wechat", result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
