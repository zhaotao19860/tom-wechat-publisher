from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check_topic_history.py"


class TopicHistoryTests(unittest.TestCase):
    def run_check(
        self,
        history_root: Path,
        *,
        topic: str,
        source: str = "",
        exclude: Optional[Path] = None,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable,
            str(SCRIPT),
            "--history-root",
            str(history_root),
            "--topic",
            topic,
            "--json",
        ]
        if source:
            command.extend(["--source", source])
        if exclude is not None:
            command.extend(["--exclude", str(exclude)])
        return subprocess.run(command, capture_output=True, text=True)

    @staticmethod
    def write_article(root: Path, dirname: str, title: str, source: str = "") -> Path:
        article_dir = root / dirname
        article_dir.mkdir(parents=True)
        source_line = f'\nsource: "{source}"' if source else ""
        article = article_dir / "article.md"
        article.write_text(
            f'---\ntitle: "{title}"{source_line}\n---\n\n# {title}\n',
            encoding="utf-8",
        )
        return article

    def test_same_source_is_a_duplicate_even_when_title_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = "https://example.com/advisories/123"
            article = self.write_article(root, "old", "旧标题", source)

            result = self.run_check(root, topic="完全不同的新标题", source=source)

            self.assertEqual(result.returncode, 3, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["status"], "duplicate")
            self.assertEqual(report["matches"][0]["path"], str(article.resolve()))
            self.assertIn("same-source", report["matches"][0]["reasons"])

    def test_highly_similar_topic_is_a_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_article(
                root,
                "old",
                "CoreDNS proxyproto 高危风险解析：PPv2 报文为何触发 panic",
            )

            result = self.run_check(
                root,
                topic="CoreDNS proxyproto 风险解析：PPv2 报文触发 panic 的原因",
            )

            self.assertEqual(result.returncode, 3, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["status"], "duplicate")
            self.assertGreaterEqual(report["matches"][0]["score"], 0.75)

    def test_unrelated_topic_is_clear(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_article(root, "old", "dnstap：高速 DNS 日志如何降低观测成本")

            result = self.run_check(root, topic="微信公众号封面中的宋画构图方法")

            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["status"], "clear")

    def test_excluded_output_directory_is_not_treated_as_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "current"
            self.write_article(root, "current", "即将撰写的文章")

            result = self.run_check(root, topic="即将撰写的文章", exclude=output)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["scanned"], 0)

    def test_skill_and_generate_wrapper_require_prewrite_check(self) -> None:
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        wrapper = (ROOT / "scripts/publish_draft.sh").read_text(encoding="utf-8")

        self.assertIn("## Mandatory Topic History Check", skill)
        self.assertIn("check_topic_history.py", wrapper)
        self.assertLess(
            wrapper.index("check_topic_history.py"),
            wrapper.index('gen_cmd=("$PYTHON" "$script_dir/generate_article.py"'),
        )


if __name__ == "__main__":
    unittest.main()
