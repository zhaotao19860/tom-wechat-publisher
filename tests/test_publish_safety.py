from __future__ import annotations

import base64
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PublishSafetyTests(unittest.TestCase):
    def test_no_footnote_is_applied_to_preview_and_upload(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bin_dir = root / "bin"
            work = root / "work"
            images = work / "images"
            bin_dir.mkdir()
            images.mkdir(parents=True)
            log = root / "wenyan-args.log"
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
            env["WENYAN_ARGS_LOG"] = str(log)
            env["WECHAT_APP_ID"] = "test-app-id"
            env["WECHAT_APP_SECRET"] = "test-app-secret"
            result = subprocess.run(
                [
                    "/bin/bash",
                    str(ROOT / "scripts/publish_draft.sh"),
                    str(article),
                    "--theme",
                    "dns-sakura",
                    "--theme-selection-confirmed",
                    "--no-footnote",
                    "--upload",
                    "--no-open-preview",
                ],
                cwd=work,
                env=env,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            invocations = log.read_text(encoding="utf-8").splitlines()
            render = next(line for line in invocations if line.startswith("render "))
            publish = next(line for line in invocations if line.startswith("publish "))
            self.assertIn("--no-footnote", render)
            self.assertIn("--no-footnote", publish)

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
