from __future__ import annotations

import base64
import os
import subprocess
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class WrapperFixture:
    work: Path
    article: Path
    wenyan_log: Path
    checker_marker: Path


class PublishSafetyTests(unittest.TestCase):
    def run_wrapper_with_fakes(
        self, *, upload: bool, checker_exit: int = 0, extra_args: tuple[str, ...] = ()
    ) -> tuple[subprocess.CompletedProcess[str], WrapperFixture]:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        bin_dir = root / "bin"
        work = root / "work"
        images = work / "images"
        bin_dir.mkdir()
        images.mkdir(parents=True)
        wenyan_log = root / "wenyan-args.log"
        wenyan_log.touch()
        checker_marker = root / "checker-ran"
        wenyan = bin_dir / "wenyan"
        wenyan.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >> \"$WENYAN_ARGS_LOG\"\n"
            "case \"$1\" in\n"
            "  --version) echo '2.0.8' ;;\n"
            "  render) echo '<section>preview</section>' ;;\n"
            "  publish) echo 'media_id=test-draft' ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        wenyan.chmod(0o755)
        checker = root / "check_prose.py"
        checker.write_text(
            "import os\n"
            "from pathlib import Path\n\n"
            "Path(os.environ['CHECKER_MARKER']).touch()\n"
            "raise SystemExit(int(os.environ['CHECKER_EXIT']))\n",
            encoding="utf-8",
        )
        (images / "cover.png").write_bytes(
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
            )
        )
        article = work / "article.md"
        article.write_text(
            "---\n"
            "title: 'Test'\n"
            "author: '赵涛'\n"
            "cover: 'images/cover.png'\n"
            "cover_generation: 'ai'\n"
            "cover_style: '新中式国风'\n"
            "cover_poem:\n"
            "  - '一川清浊自分流'\n"
            "  - '万径归关辨晦明'\n"
            "cover_typography: 'vertical'\n"
            "---\n\n# Test\n",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}:/usr/bin:/bin"
        env["WENYAN_ARGS_LOG"] = str(wenyan_log)
        env["CHECKER_MARKER"] = str(checker_marker)
        env["CHECKER_EXIT"] = str(checker_exit)
        env["TOM_WECHAT_CHECK_PROSE"] = str(checker)
        env["WECHAT_APP_ID"] = "test-app-id"
        env["WECHAT_APP_SECRET"] = "test-app-secret"
        command = [
            "/bin/bash",
            str(ROOT / "scripts/publish_draft.sh"),
            str(article),
            "--theme",
            "dns-sakura",
            "--theme-selection-confirmed",
            "--no-open-preview",
            *extra_args,
        ]
        if upload:
            command.append("--upload")
        result = subprocess.run(
            command,
            cwd=work,
            env=env,
            capture_output=True,
            text=True,
        )
        return result, WrapperFixture(work, article, wenyan_log, checker_marker)

    def test_no_footnote_is_applied_to_preview_and_upload(self) -> None:
        result, fixture = self.run_wrapper_with_fakes(upload=True, extra_args=("--no-footnote",))

        self.assertEqual(result.returncode, 0, result.stderr)
        invocations = fixture.wenyan_log.read_text(encoding="utf-8").splitlines()
        render = next(line for line in invocations if line.startswith("render "))
        publish = next(line for line in invocations if line.startswith("publish "))
        self.assertIn("--no-footnote", render)
        self.assertIn("--no-footnote", publish)

    def test_preview_runs_configured_prose_checker(self) -> None:
        result, fixture = self.run_wrapper_with_fakes(upload=False, checker_exit=0)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(fixture.checker_marker.is_file())

    def test_prose_checker_failure_stops_before_render(self) -> None:
        result, fixture = self.run_wrapper_with_fakes(upload=False, checker_exit=7)

        self.assertEqual(result.returncode, 7)
        self.assertNotIn("render ", fixture.wenyan_log.read_text(encoding="utf-8"))

    def test_failure_notification_is_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            bin_dir = root / "bin"
            infoflow = home / ".comate/skills/infoflow-message-group/scripts"
            work = root / "work"
            bin_dir.mkdir(parents=True)
            infoflow.mkdir(parents=True)
            work.mkdir()
            marker = root / "sent.txt"
            (bin_dir / "wenyan").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            (bin_dir / "wenyan").chmod(0o755)
            (infoflow / "setup.sh").write_text("#!/bin/sh\necho 'export INFOFLOW_TOKEN=x'\n", encoding="utf-8")
            (infoflow / "msg_send_single.sh").write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
            (infoflow / "setup.sh").chmod(0o755)
            (infoflow / "msg_send_single.sh").chmod(0o755)
            article = work / "article.md"
            article.write_text("# example\n", encoding="utf-8")
            (work / "image_specs.json").write_text("[]", encoding="utf-8")
            env = os.environ.copy()
            env.pop("TOM_WECHAT_FAILURE_NOTIFY", None)
            env["TOM_WECHAT_CHECK_PROSE"] = str(root / "check_prose.py")
            env["HOME"] = str(home)
            env["PATH"] = f"{bin_dir}:/usr/bin:/bin"
            result = subprocess.run(
                ["/bin/bash", str(ROOT / "scripts/publish_draft.sh"), str(article), "--require-body-images", "--no-open-preview"],
                cwd=work,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(marker.exists(), result.stderr)


if __name__ == "__main__":
    unittest.main()
