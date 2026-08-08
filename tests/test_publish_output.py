from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
