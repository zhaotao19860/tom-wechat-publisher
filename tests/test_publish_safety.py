from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PublishSafetyTests(unittest.TestCase):
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
