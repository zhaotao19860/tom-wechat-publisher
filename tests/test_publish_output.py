from __future__ import annotations

import contextlib
import io
import unittest

from scripts import validate_publish_output as validator


class PublishOutputTests(unittest.TestCase):
    def test_extracts_media_id_from_wenyan_success(self) -> None:
        output = "发布成功，Media ID: WbRk3xhNjj6cpC0OM8LkSyAVTdT_RnFK\n"

        self.assertEqual(
            validator.extract_draft_media_id(output), "WbRk3xhNjj6cpC0OM8LkSyAVTdT_RnFK"
        )

    def test_rejects_output_without_media_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "Media ID"):
            validator.extract_draft_media_id("publish completed\n")

    def test_rejects_media_id_without_success_wording(self) -> None:
        with self.assertRaisesRegex(ValueError, "success"):
            validator.extract_draft_media_id("media_id=orphan-id\n")

    def test_rejects_incidental_media_id_text(self) -> None:
        with self.assertRaisesRegex(ValueError, "Media ID"):
            validator.extract_draft_media_id(
                "published successfully: not_media_id=incidental\n"
            )

    def test_rejects_success_and_media_id_on_unrelated_lines(self) -> None:
        with self.assertRaisesRegex(ValueError, "success"):
            validator.extract_draft_media_id(
                "published successfully\nMedia ID: orphan-id\n"
            )

    def test_rejects_negated_chinese_success_wording(self) -> None:
        with self.assertRaisesRegex(ValueError, "success"):
            validator.extract_draft_media_id("未发布成功，Media ID: rejected-id\n")

    def test_rejects_negated_english_success_wording(self) -> None:
        with self.assertRaisesRegex(ValueError, "success"):
            validator.extract_draft_media_id(
                "not published successfully, media_id=rejected-id\n"
            )

    def test_cli_reports_generic_error_for_unreadable_log(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            status = validator.main(["validate_publish_output.py", "/missing/upload.log"])

        self.assertEqual(status, 1)
        self.assertEqual(stderr.getvalue().strip(), "draft creation evidence invalid")


if __name__ == "__main__":
    unittest.main()
