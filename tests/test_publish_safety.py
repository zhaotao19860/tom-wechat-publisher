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
    root: Path
    work: Path
    article: Path
    wenyan_log: Path
    checker_marker: Path
    notification_log: Path


class PublishSafetyTests(unittest.TestCase):
    def run_wrapper_with_fakes(
        self,
        *,
        upload: bool,
        checker_exit: int = 0,
        extra_args: tuple[str, ...] = (),
        wenyan_publish_output: str = "published successfully, media_id=test-draft",
        app_id: str = "test-app-id",
        wenyan_leak_secret: bool = False,
        wenyan_leak_app_id: bool = False,
        notify_capture: bool = False,
    ) -> tuple[subprocess.CompletedProcess[str], WrapperFixture]:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        bin_dir = root / "bin"
        work = root / "work"
        tmp_dir = root / "tmp"
        images = work / "images"
        bin_dir.mkdir()
        tmp_dir.mkdir()
        images.mkdir(parents=True)
        wenyan_log = root / "wenyan-args.log"
        wenyan_log.touch()
        checker_marker = root / "checker-ran"
        notification_log = root / "notification.log"
        wenyan = bin_dir / "wenyan"
        wenyan.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >> \"$WENYAN_ARGS_LOG\"\n"
            "case \"$1\" in\n"
            "  --version) echo '2.0.8' ;;\n"
            "  render) echo '<section>preview</section>' ;;\n"
            "  publish) if [ \"$WENYAN_LEAK_SECRET\" = 1 ]; then printf 'error: secret=%s\\n' \"$WECHAT_APP_SECRET\" >&2; exit 1; fi; if [ \"$WENYAN_LEAK_APP_ID\" = 1 ]; then printf 'error: app_id=%s\\n' \"$WECHAT_APP_ID\" >&2; exit 1; fi; printf '%s\\n' \"$WENYAN_PUBLISH_OUTPUT\" ;;\n"
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
        env["TMPDIR"] = str(tmp_dir)
        env["WENYAN_ARGS_LOG"] = str(wenyan_log)
        env["CHECKER_MARKER"] = str(checker_marker)
        env["CHECKER_EXIT"] = str(checker_exit)
        env["TOM_WECHAT_CHECK_PROSE"] = str(checker)
        env["WECHAT_APP_ID"] = app_id
        env["WECHAT_APP_SECRET"] = "test-app-secret"
        env["WENYAN_PUBLISH_OUTPUT"] = wenyan_publish_output
        env["WENYAN_LEAK_SECRET"] = "1" if wenyan_leak_secret else "0"
        env["WENYAN_LEAK_APP_ID"] = "1" if wenyan_leak_app_id else "0"
        if notify_capture:
            home = root / "home"
            infoflow = home / ".comate/skills/infoflow-message-group/scripts"
            infoflow.mkdir(parents=True)
            setup = infoflow / "setup.sh"
            setup.write_text("#!/bin/sh\nprintf '%s\\n' 'export INFOFLOW_TOKEN=token'\n", encoding="utf-8")
            setup.chmod(0o755)
            sender = infoflow / "msg_send_single.sh"
            sender.write_text(
                "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$INFOFLOW_CAPTURE\"\n",
                encoding="utf-8",
            )
            sender.chmod(0o755)
            env["HOME"] = str(home)
            env["TOM_WECHAT_FAILURE_NOTIFY"] = "recipient"
            env["INFOFLOW_CAPTURE"] = str(notification_log)
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
        return result, WrapperFixture(root, work, article, wenyan_log, checker_marker, notification_log)

    def test_no_footnote_is_applied_to_preview_and_upload(self) -> None:
        result, fixture = self.run_wrapper_with_fakes(upload=True, extra_args=("--no-footnote",))

        self.assertEqual(result.returncode, 0, result.stderr)
        invocations = fixture.wenyan_log.read_text(encoding="utf-8").splitlines()
        render = next(line for line in invocations if line.startswith("render "))
        publish = next(line for line in invocations if line.startswith("publish "))
        self.assertIn("--no-footnote", render)
        self.assertIn("--no-footnote", publish)

    def test_upload_reports_masked_app_id_and_media_id(self) -> None:
        result, _ = self.run_wrapper_with_fakes(upload=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Target WeChat AppID: test...p-id", result.stdout)
        self.assertIn("Draft Media ID: test-draft", result.stdout)
        self.assertNotIn("test-app-secret", result.stdout + result.stderr)

    def test_upload_masks_short_app_id(self) -> None:
        result, _ = self.run_wrapper_with_fakes(upload=True, app_id="short")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Target WeChat AppID: ***", result.stdout)

    def test_upload_redacts_secret_from_output_log_and_notification(self) -> None:
        result, fixture = self.run_wrapper_with_fakes(
            upload=True,
            extra_args=("--keep-temp",),
            wenyan_leak_secret=True,
            notify_capture=True,
        )

        self.assertNotEqual(result.returncode, 0)
        secret = "test-app-secret"
        self.assertNotIn(secret, result.stdout + result.stderr)
        upload_log = next((fixture.root / "tmp").glob("wechat-draft.*/upload.log"))
        self.assertNotIn(secret, upload_log.read_text(encoding="utf-8"))
        self.assertNotIn(secret, fixture.notification_log.read_text(encoding="utf-8"))

    def test_upload_masks_adapter_app_id_in_output_log_and_notification(self) -> None:
        result, fixture = self.run_wrapper_with_fakes(
            upload=True,
            extra_args=("--keep-temp",),
            wenyan_leak_app_id=True,
            notify_capture=True,
        )

        self.assertNotEqual(result.returncode, 0)
        full_app_id = "test-app-id"
        masked_app_id = "test...p-id"
        self.assertNotIn(full_app_id, result.stdout + result.stderr)
        self.assertIn(masked_app_id, result.stdout)
        upload_log = next((fixture.root / "tmp").glob("wechat-draft.*/upload.log"))
        upload_output = upload_log.read_text(encoding="utf-8")
        self.assertNotIn(full_app_id, upload_output)
        self.assertIn(f"error: app_id={masked_app_id}", upload_output)
        self.assertNotIn(full_app_id, fixture.notification_log.read_text(encoding="utf-8"))

    def test_upload_rejects_zero_exit_without_explicit_draft_evidence(self) -> None:
        result, _ = self.run_wrapper_with_fakes(
            upload=True, wenyan_publish_output="completed"
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Draft created successfully.", result.stdout)

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
