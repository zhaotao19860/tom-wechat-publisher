from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
PYTHON = Path("/usr/bin/python3") if Path("/usr/bin/python3").exists() else Path(sys.executable)
sys.path.insert(0, str(SCRIPTS))

import html_render  # noqa: E402
import image_spec  # noqa: E402
import tracks  # noqa: E402


COVER_SPEC = {
    "kind": "cover",
    "title_zh": "测试标题",
    "subtitle": "副标题",
    "notes": "说明",
    "items": [{"label": "A", "sub": "a"}, {"label": "B", "sub": "b"}],
}

AI_ARTICLE = """---
title: "AI 网关怎么做模型路由"
track: ai-gateway
---
# AI 网关怎么做模型路由

![封面](cover_article.png)

## 原理

![原理](img_section_mechanism.png)

网关按 TPM 和 RPM 做限流，主模型超时后走 fallback 到备用模型，再按 Token 计费。
"""


def _write(root: Path, name: str, text: str) -> Path:
    path = root / name
    path.write_text(text, encoding="utf-8")
    return path


class TrackRegistryTests(unittest.TestCase):
    def test_missing_or_unknown_track_is_dns(self) -> None:
        for value in (None, "", "  ", "foo", "L7", "gateway"):
            self.assertEqual(tracks.normalize_track(value), "dns", value)
        self.assertEqual(tracks.track_from_markdown("# 无 frontmatter\n"), "dns")
        self.assertEqual(tracks.track_from_markdown("---\ntitle: x\n---\n# x\n"), "dns")
        self.assertEqual(tracks.track_from_markdown("---\ntrack: unknown\n---\n"), "dns")
        self.assertEqual(tracks.track_from_markdown("---\ntrack: 'L4-Gateway'\n---\n"), "l4-gateway")

    def test_kickers_and_albums(self) -> None:
        self.assertEqual(tracks.kicker("dns"), "DNS Daily")
        self.assertEqual(tracks.kicker("dns", upper=True), "DNS DAILY")
        self.assertEqual(tracks.kicker("l4-gateway"), "L4 GATEWAY")
        self.assertEqual(tracks.kicker("l4-gateway", upper=True), "L4 GATEWAY")
        self.assertEqual(tracks.kicker("ai-gateway"), "AI GATEWAY")
        self.assertEqual(tracks.kicker("ai-gateway", upper=True), "AI GATEWAY")
        self.assertEqual(tracks.album("dns"), "DNS知识库")
        self.assertEqual(tracks.album(None), "DNS知识库")
        self.assertEqual(tracks.album("l4-gateway"), "网关知识库")
        self.assertEqual(tracks.album("ai-gateway"), "网关知识库")

    def test_ensure_frontmatter_track(self) -> None:
        md = '---\ntitle: "x"\n---\n# x\n'
        out = tracks.ensure_frontmatter_track(md, "l4-gateway")
        self.assertEqual(out, '---\ntitle: "x"\ntrack: l4-gateway\n---\n# x\n')
        self.assertEqual(tracks.ensure_frontmatter_track(out, "ai-gateway"), out)
        self.assertEqual(tracks.ensure_frontmatter_track("# no fm\n", "ai-gateway"), "# no fm\n")

    def test_cli_album(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cases = {
                "dns.md": ("---\ntitle: x\n---\n", "DNS知识库"),
                "l4.md": ("---\ntrack: l4-gateway\n---\n", "网关知识库"),
                "ai.md": ("---\ntrack: ai-gateway\n---\n", "网关知识库"),
            }
            for name, (text, expected) in cases.items():
                path = _write(root, name, text)
                result = subprocess.run(
                    [str(PYTHON), str(SCRIPTS / "tracks.py"), "album", str(path)],
                    capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)


class HtmlRenderTrackTests(unittest.TestCase):
    def test_no_track_keeps_dns_kickers(self) -> None:
        self.assertIn('<div class="kicker">DNS Daily · 安全观察</div>', html_render._render_cover_dark_gold(dict(COVER_SPEC)))
        self.assertIn('<div class="kicker">DNS DAILY · FEATURE</div>', html_render._render_cover_magazine(dict(COVER_SPEC)))
        self.assertIn('<div class="kicker">DNS DAILY</div>', html_render._render_cover_grid(dict(COVER_SPEC)))

    def test_explicit_dns_track_matches_no_track(self) -> None:
        for render in (html_render._render_cover_dark_gold, html_render._render_cover_magazine, html_render._render_cover_grid):
            self.assertEqual(render(dict(COVER_SPEC)), render(dict(COVER_SPEC, track="dns")))
            self.assertEqual(render(dict(COVER_SPEC)), render(dict(COVER_SPEC, track="bogus")))

    def test_gateway_kickers(self) -> None:
        ai = dict(COVER_SPEC, track="ai-gateway")
        self.assertIn('<div class="kicker">AI GATEWAY · FEATURE</div>', html_render._render_cover_magazine(ai))
        self.assertIn('<div class="kicker">AI GATEWAY</div>', html_render._render_cover_grid(ai))
        self.assertIn("AI GATEWAY · 架构实践", html_render._render_cover_dark_gold(ai))
        self.assertNotIn("DNS DAILY", html_render._render_cover_magazine(ai))
        l4 = dict(COVER_SPEC, track="l4-gateway")
        self.assertIn('<div class="kicker">L4 GATEWAY</div>', html_render._render_cover_grid(l4))


class ImageSpecTrackTests(unittest.TestCase):
    def test_ai_gateway_tokens_extracted(self) -> None:
        text = "网关按 TPM 和 RPM 做限流，主模型超时后走 fallback。"
        tokens = image_spec._extract_technical_tokens(text, limit=8, track="ai-gateway")
        self.assertIn("TPM", tokens)
        self.assertIn("fallback", tokens)
        self.assertIn("限流", tokens)
        dns_tokens = image_spec._extract_technical_tokens(text, limit=8, track="dns")
        self.assertNotIn("fallback", dns_tokens)

    def test_l4_short_tokens_need_word_boundary(self) -> None:
        tokens = image_spec._extract_technical_tokens("users address tuning", limit=8, track="l4-gateway")
        for short in ("RS", "DR", "TUN"):
            self.assertNotIn(short, tokens)
        tokens = image_spec._extract_technical_tokens("LVS 用 DR 模式把 VIP 流量交给 RS，配合健康检查。", limit=8, track="l4-gateway")
        for expected in ("LVS", "DR", "VIP", "RS", "健康检查"):
            self.assertIn(expected, tokens)

    def test_spec_carries_track_and_dns_omits_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ai_specs = image_spec.derive_specs(_write(root, "ai.md", AI_ARTICLE))
            self.assertTrue(ai_specs)
            self.assertTrue(all(spec.to_dict()["track"] == "ai-gateway" for spec in ai_specs))
            cover = next(spec.to_dict() for spec in ai_specs if spec.kind == "cover")
            self.assertIn("AI GATEWAY", html_render._render_cover_magazine(cover))

            dns_md = AI_ARTICLE.replace("track: ai-gateway\n", "")
            dns_specs = image_spec.derive_specs(_write(root, "dns.md", dns_md))
            self.assertTrue(all("track" not in spec.to_dict() for spec in dns_specs))
            out = root / "image_specs.json"
            image_spec.write_specs(dns_specs, out)
            self.assertNotIn('"track"', out.read_text(encoding="utf-8"))
            self.assertEqual(image_spec._ACTIVE_TRACK, "dns")


class TopicHistoryTrackTests(unittest.TestCase):
    def test_matches_report_track(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "a").mkdir()
            (root / "b").mkdir()
            _write(root / "a", "article.md", '---\ntitle: "AI 网关模型路由实践"\ntrack: ai-gateway\n---\n')
            _write(root / "b", "article.md", '---\ntitle: "AI 网关模型路由入门"\n---\n')
            result = subprocess.run(
                [str(PYTHON), str(SCRIPTS / "check_topic_history.py"), "--topic", "AI 网关模型路由",
                 "--history-root", str(root), "--json"],
                capture_output=True, text=True,
            )
            report = json.loads(result.stdout)
            by_title = {m["title"]: m["track"] for m in report["matches"]}
            self.assertEqual(by_title["AI 网关模型路由实践"], "ai-gateway")
            self.assertEqual(by_title["AI 网关模型路由入门"], "dns")



class PublishAlbumReminderTests(unittest.TestCase):
    """Reuse the fake-wenyan harness from test_publish_safety (no network)."""

    def _run(self, **kwargs):
        sys.path.insert(0, str(ROOT / "tests"))
        from test_publish_safety import PublishSafetyTests

        return PublishSafetyTests.run_wrapper_with_fakes(self, **kwargs)

    def test_reminder_after_upload(self) -> None:
        result, _ = self._run(upload=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Album reminder: 发表时请在后台选择合集「DNS知识库」", result.stdout)

    def test_reminder_without_upload(self) -> None:
        result, _ = self._run(upload=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Album reminder: 发表时请在后台选择合集「DNS知识库」", result.stdout)

if __name__ == "__main__":
    unittest.main()


class TrackHintTests(unittest.TestCase):
    def test_gateway_hint_needs_two_terms(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        import tracks as t
        self.assertEqual(t.suggest_track("LVS 用 FullNAT 转发，ECMP 分流"), "l4-gateway")
        self.assertEqual(t.suggest_track("模型路由和 TPM 限流都在 AI 网关里"), "ai-gateway")
        self.assertEqual(t.suggest_track("DNS 解析到 VIP，users 很多"), "")
        self.assertEqual(t.suggest_track("只提了一次 LVS"), "")
