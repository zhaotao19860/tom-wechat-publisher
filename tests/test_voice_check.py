from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import account_profile  # noqa: E402
import humanize_article  # noqa: E402
import voice_check  # noqa: E402


def han(n: int, ch: str = "字") -> str:
    return ch * n


def rules(findings: list[dict]) -> list[str]:
    return [item["rule"] for item in findings]


CLEAN = (
    "---\ntitle: 'x'\n---\n\n"
    "线上某台服务器频繁告警，调用比较函数时返回超过二十秒，增加打印日志发现是比较函数返回慢。\n\n"
    "怎么配置才能不冲突呢？先看参数。\n"
)


class VoiceCheckRuleTests(unittest.TestCase):
    def check(self, text: str, facts: list[str] | None = None) -> tuple[list[str], list[str], dict]:
        fails, warns, stats = voice_check.check(text, facts or [])
        return rules(fails), rules(warns), stats

    def test_clean_article_has_no_findings(self) -> None:
        fails, warns, _ = self.check(CLEAN)
        self.assertEqual(fails, [])
        self.assertEqual(warns, [])

    def test_stock_frame_fails(self) -> None:
        fails, _, _ = self.check(CLEAN + "\n我原本以为打开这项校验只会更安全。RFC 第二节写得很硬。\n")
        self.assertEqual(fails.count("stock_frame"), 2)

    def test_stock_frame_inside_code_or_references_is_ignored(self) -> None:
        text = CLEAN + "\n```\n我原本以为\n```\n\n## 参考资料\n\n- 到这里为止的那篇文章\n"
        fails, _, _ = self.check(text)
        self.assertNotIn("stock_frame", fails)

    def test_template_h2_fails_and_noun_h2_passes(self) -> None:
        fails, _, _ = self.check("## dig 看得见签名，delv 才会验\n\n" + CLEAN)
        self.assertIn("template_h2", fails)
        fails, _, _ = self.check("## 客户端这边\n\n" + CLEAN)
        self.assertNotIn("template_h2", fails)

    def test_unsourced_claim_always_fails_even_with_facts(self) -> None:
        text = CLEAN + "\n用户报障时我第一件事就是用它查一下。\n"
        fails, _, _ = self.check(text, ["我第一件事就是看日志"])
        self.assertIn("claim_unsourced", fails)

    def test_claim_check_warns_without_covering_fact(self) -> None:
        text = CLEAN + "\n我在公司网络上直接试了一次，结果连不上。\n"
        _, warns, _ = self.check(text)
        self.assertIn("claim_unverified", warns)
        _, warns, _ = self.check(text, ["做 DNS 十多年，维护过四套 DNS 系统"])
        self.assertIn("claim_unverified", warns)

    def test_claim_covered_by_keyword_overlap(self) -> None:
        text = CLEAN + "\n我做了十年 DNS，这种事见得多。\n"
        _, warns, _ = self.check(text, ["做 DNS 十多年，2011 年开始做运营商缓存"])
        self.assertNotIn("claim_unverified", warns)

    def test_claim_covered_by_fact_substring(self) -> None:
        text = CLEAN + "\n我九月写过 DNS-AID 那篇，字段也是照着抄的。\n"
        _, warns, _ = self.check(text, ["九月写过 DNS-AID 那篇"])
        self.assertNotIn("claim_unverified", warns)

    def test_sentence_cv_warns_on_even_rhythm_only(self) -> None:
        even = "\n\n".join(han(20) + "。" + han(20) + "。" + han(20) + "。" for _ in range(11))
        _, warns, stats = self.check(even + "\n\n还有问题吗？\n")
        self.assertIn("sentence_cv", warns)
        self.assertLess(stats["sentence_cv"], 0.55)
        varied = "\n\n".join(han(5) + "。" + han(60) + "。" + han(18) + "。" for _ in range(11))
        _, warns, stats = self.check(varied + "\n\n还有问题吗？\n")
        self.assertNotIn("sentence_cv", warns)
        self.assertGreaterEqual(stats["sentence_cv"], 0.55)

    def test_sentence_cv_needs_thirty_sentences(self) -> None:
        even = "\n\n".join(han(20) + "。" for _ in range(10))
        _, warns, stats = self.check(even)
        self.assertNotIn("sentence_cv", warns)
        self.assertNotIn("sentence_cv", stats)

    def test_section_cv_and_h2_count(self) -> None:
        sections = "".join(f"## 第{n}节\n\n{han(100)}。\n\n" for n in "一二三四五")
        _, warns, stats = self.check(sections)
        self.assertIn("section_cv", warns)
        self.assertIn("h2_count", warns)
        self.assertEqual(stats["h2"], 5)
        uneven = f"## 甲\n\n{han(500)}。\n\n## 乙\n\n{han(30)}。\n\n## 丙\n\n{han(120)}。\n"
        _, warns, _ = self.check(uneven)
        self.assertNotIn("section_cv", warns)
        self.assertNotIn("h2_count", warns)

    def test_no_question_only_for_long_articles(self) -> None:
        long_text = "\n\n".join(han(100) + "。" for _ in range(13))
        _, warns, _ = self.check(long_text)
        self.assertIn("no_question", warns)
        _, warns, _ = self.check(long_text + "\n\n这样行吗？\n")
        self.assertNotIn("no_question", warns)
        _, warns, _ = self.check("\n\n".join(han(100) + "。" for _ in range(5)))
        self.assertNotIn("no_question", warns)

    def test_paragraph_final_judgment_ratio(self) -> None:
        closing = "\n\n".join(han(40) + "。这组指纹就不算数。" for _ in range(6))
        _, warns, stats = self.check(closing)
        self.assertIn("judgment_endings", warns)
        self.assertEqual(stats["judgment_endings"], "6/6")
        plain = "\n\n".join(han(40) + "。" + han(30) + "。" for _ in range(6))
        _, warns, _ = self.check(plain)
        self.assertNotIn("judgment_endings", warns)

    def test_gloss_needs_two_hits(self) -> None:
        one = CLEAN + "\n这种做法叫首次信任。\n"
        _, warns, _ = self.check(one)
        self.assertNotIn("gloss", warns)
        _, warns, _ = self.check(one + "\nDAWN 的全称是一串英文。\n")
        self.assertIn("gloss", warns)

    def test_enumeration_density(self) -> None:
        dense = "甲、乙、丙、丁、戊、己、庚、辛这些都要能跑起来。" + han(30) + "。\n"
        _, warns, _ = self.check(dense)
        self.assertIn("enumeration_density", warns)

    def test_load_facts_skips_headings_notes_and_bullets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "voice-facts.md"
            path.write_text("# 标题\n\n> 草稿说明\n\n- 做 DNS 十多年\n* 在做 AI 网关 irouter\n\n", encoding="utf-8")
            self.assertEqual(voice_check.load_facts(path), ["做 DNS 十多年", "在做 AI 网关 irouter"])


class VoiceCheckCliTests(unittest.TestCase):
    def run_cli(self, body: str, *args: str, facts: str | None = None) -> subprocess.CompletedProcess[str]:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        article = Path(temp.name) / "article.md"
        article.write_text(body, encoding="utf-8")
        command = [sys.executable, "-I", str(SCRIPTS / "voice_check.py"), str(article), *args]
        if facts is not None:
            facts_path = Path(temp.name) / "voice-facts.md"
            facts_path.write_text(facts, encoding="utf-8")
            command += ["--facts", str(facts_path)]
        return subprocess.run(command, capture_output=True, text=True)

    FAILING = CLEAN + "\n我原本以为这样就行。\n"

    def test_block_mode_is_default_and_exits_one_on_fail(self) -> None:
        result = self.run_cli(self.FAILING)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("需要修改", result.stdout)
        result = self.run_cli(self.FAILING, "--mode", "block")
        self.assertEqual(result.returncode, 1)

    def test_warn_mode_exits_zero_but_prints(self) -> None:
        result = self.run_cli(self.FAILING, "--mode", "warn")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("我原本以为", result.stdout)
        self.assertIn("warn 模式", result.stdout)

    def test_warnings_alone_do_not_fail_block_mode(self) -> None:
        result = self.run_cli(CLEAN + "\n我在公司网络上试了一次。\n", "--mode", "block")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("需要人工判断", result.stdout)

    def test_clean_article_passes(self) -> None:
        result = self.run_cli(CLEAN)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("未发现", result.stdout)

    def test_json_report(self) -> None:
        result = self.run_cli(self.FAILING, "--json", "--mode", "warn")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["mode"], "warn")
        self.assertEqual(report["exit_code"], 0)
        self.assertEqual(rules(report["fails"]), ["stock_frame"])
        self.assertIn("han", report["stats"])

    def test_facts_file_covers_claim(self) -> None:
        body = CLEAN + "\n我做了十年 DNS，这种事见得多。\n"
        result = self.run_cli(body, "--json")
        self.assertIn("claim_unverified", rules(json.loads(result.stdout)["warns"]))
        result = self.run_cli(body, "--json", facts="- 做 DNS 十多年，2011 年开始做运营商缓存\n")
        self.assertNotIn("claim_unverified", rules(json.loads(result.stdout)["warns"]))

    def test_missing_facts_file_warns_and_continues(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        article = Path(temp.name) / "article.md"
        article.write_text(CLEAN, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-I", str(SCRIPTS / "voice_check.py"), str(article), "--facts", str(Path(temp.name) / "nope.md")],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("cannot read facts file", result.stderr)

    def test_unreadable_article_exits_two(self) -> None:
        result = subprocess.run(
            [sys.executable, "-I", str(SCRIPTS / "voice_check.py"), "/nonexistent/article.md", "--mode", "warn"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)


class HumanizeAndTemplateTests(unittest.TestCase):
    def test_humanize_no_longer_inserts_filler_sentence(self) -> None:
        paragraph = "解析器收到查询后先查缓存。缓存没有命中就去问权威。权威返回结果后写回缓存。客户端最后拿到应答。"
        # The old splitter fired on a bare paragraph of four similar sentences.
        for text in (paragraph, "# 标题\n\n" + paragraph):
            self.assertNotIn("这一点很重要", humanize_article.humanize(text).text)

    def test_humanize_keeps_three_part_lists(self) -> None:
        result = humanize_article.humanize("这里要看缓存、递归和权威三处。\n")
        self.assertIn("缓存、递归和权威", result.text)

    def test_removed_steps_are_gone(self) -> None:
        self.assertFalse(hasattr(humanize_article, "_split_rigid_paragraphs"))
        self.assertFalse(hasattr(humanize_article, "_swap_three_part_lists"))

    def test_fallback_templates_have_no_reversal_or_summary_heading(self) -> None:
        source = (SCRIPTS / "generate_article.py").read_text(encoding="utf-8")
        self.assertNotIn("不在于", source)
        self.assertNotIn("## 一句话总结", source)
        self.assertNotIn('"## 一句话总结"', source)

    def test_generate_humanize_is_off_by_default(self) -> None:
        wrapper = (SCRIPTS / "publish_draft.sh").read_text(encoding="utf-8")
        self.assertIn("generate_humanize=0", wrapper)
        self.assertNotIn("generate_humanize=1  #", wrapper)


class ProfileVoiceFieldTests(unittest.TestCase):
    def write_profile(self, directory: Path, extra: dict) -> None:
        data = {"id": "demo", "track": "dns", **extra}
        (directory / "demo.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_voice_fields_default_empty_and_exported(self) -> None:
        profile = account_profile.load_profile("default")
        self.assertEqual(profile["voice"], "")
        self.assertEqual(profile["voice_facts"], "")
        values = account_profile.shell_values(account_profile.load_profile("medical"))
        self.assertEqual(values["PROFILE_VOICE"], "")
        self.assertEqual(values["PROFILE_VOICE_FACTS"], "")

    def test_relative_voice_paths_resolve_against_accounts_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / "demo").mkdir()
            (directory / "demo" / "voice.md").write_text("# voice\n", encoding="utf-8")
            (directory / "demo" / "voice-facts.md").write_text("- fact\n", encoding="utf-8")
            self.write_profile(directory, {"voice": "demo/voice.md", "voice_facts": "demo/voice-facts.md"})
            with mock.patch.object(account_profile, "PROFILE_DIR", directory):
                profile = account_profile.load_profile("demo")
            self.assertEqual(profile["voice"], str(directory / "demo" / "voice.md"))
            values = account_profile.shell_values(profile)
            self.assertEqual(values["PROFILE_VOICE_FACTS"], str(directory / "demo" / "voice-facts.md"))

    def test_missing_voice_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            self.write_profile(directory, {"voice_facts": "missing.md"})
            with mock.patch.object(account_profile, "PROFILE_DIR", directory):
                with self.assertRaises(account_profile.ProfileError):
                    account_profile.load_profile("demo")

    def test_voice_field_must_be_string(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            self.write_profile(directory, {"voice": ["a.md"]})
            with mock.patch.object(account_profile, "PROFILE_DIR", directory):
                with self.assertRaises(account_profile.ProfileError):
                    account_profile.load_profile("demo")


if __name__ == "__main__":
    unittest.main()
