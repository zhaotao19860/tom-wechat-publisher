from __future__ import annotations

import json
import shlex
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import account_profile  # noqa: E402


class AccountProfileTests(unittest.TestCase):
    def test_default_profile_keeps_technical_account(self) -> None:
        profile = account_profile.load_profile("default")
        self.assertEqual(profile["id"], "default")
        self.assertEqual(profile["track"], "dns")
        self.assertEqual(profile["author"], "赵涛")
        self.assertEqual(profile["theme"], "dns-daily")
        self.assertEqual(profile["app_id_env"], "WECHAT_APP_ID")
        self.assertEqual(profile["app_secret_env"], "WECHAT_APP_SECRET")

    def test_medical_profile_is_isolated(self) -> None:
        profile = account_profile.load_profile("medical")
        self.assertEqual(profile["track"], "medical")
        self.assertEqual(profile["theme"], "med-clinical")
        self.assertEqual(profile["album"], "医学科普库")
        self.assertEqual(profile["app_id_env"], "WECHAT_MEDICAL_APP_ID")
        self.assertEqual(profile["app_secret_env"], "WECHAT_MEDICAL_APP_SECRET")
        self.assertNotEqual(profile["app_id_env"], "WECHAT_APP_ID")
        self.assertEqual(profile["history_root"], str(Path.home() / "Desktop" / "wechat-medical"))
        self.assertEqual(profile["image_visual_theme"], "clinical-illustration")
        self.assertIn("临床插画", profile["cover_styles"])
        self.assertIn("med-clinical", profile["preview_themes"])

    def test_profile_file_contains_no_secret_values(self) -> None:
        raw = json.loads((ROOT / "accounts" / "medical.json").read_text(encoding="utf-8"))
        self.assertNotIn("app_id", raw)
        self.assertNotIn("app_secret", raw)
        self.assertEqual(raw["app_id_env"], "WECHAT_MEDICAL_APP_ID")
        self.assertEqual(raw["app_secret_env"], "WECHAT_MEDICAL_APP_SECRET")

    def test_unknown_profile_fails_closed(self) -> None:
        with self.assertRaises(account_profile.ProfileError):
            account_profile.load_profile("does-not-exist")

    def test_shell_output_round_trips(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "account_profile.py"), "shell", "--profile", "medical"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        values = {}
        for line in result.stdout.splitlines():
            key, raw = line.split("=", 1)
            values[key] = shlex.split(raw)[0] if raw else ""
        self.assertEqual(values["PROFILE_ID"], "medical")
        self.assertEqual(values["PROFILE_TRACK"], "medical")
        self.assertEqual(values["PROFILE_APP_ID_ENV"], "WECHAT_MEDICAL_APP_ID")
        self.assertEqual(values["PROFILE_ALBUM"], "医学科普库")
        self.assertEqual(values["PROFILE_IMAGE_BACKEND"], "")

    def test_image_backend_is_optional_and_unset_by_default(self) -> None:
        self.assertEqual(account_profile.load_profile("default")["image_backend"], "")
        self.assertEqual(account_profile.load_profile("medical")["image_backend"], "")


if __name__ == "__main__":
    unittest.main()