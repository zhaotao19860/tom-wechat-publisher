from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class CompareAndPreviewTests(unittest.TestCase):
    def test_missing_required_ai_candidate_is_pending_not_html(self) -> None:
        compare = load_script("compare_body_images.py")
        recommendation, reason = compare._recommend(95, [], 0, ["AI generation skipped"], None, ai_required=True)
        self.assertEqual(recommendation, "pending-ai")
        self.assertIn("AI", reason)

    def test_existing_ai_candidate_is_reused(self) -> None:
        compare = load_script("compare_body_images.py")
        with tempfile.TemporaryDirectory() as temp:
            article_dir = Path(temp)
            images = article_dir / "images"
            images.mkdir()
            candidate = images / "scene.png"
            candidate.write_bytes(b"png")
            self.assertEqual(compare._existing_ai_candidate(article_dir, "scene"), candidate)

    def test_persistent_preview_copies_html_and_local_assets(self) -> None:
        persist = load_script("persist_preview.py")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            staged = root / "staged"
            target = root / "preview"
            staged.mkdir()
            (staged / "body.png").write_bytes(b"png")
            preview = staged / "preview.html"
            preview.write_text('<img src="./body.png">', encoding="utf-8")
            output = persist.persist_preview(preview, staged, target)
            self.assertEqual(output, target / "preview.html")
            self.assertTrue(output.is_file())
            self.assertTrue((target / "body.png").is_file())


if __name__ == "__main__":
    unittest.main()
