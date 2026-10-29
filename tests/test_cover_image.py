from __future__ import annotations

import json
import shutil
import struct
import zlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/cover_image.py"


def write_png(path: Path, width: int, height: int) -> None:
    """Minimal RGB PNG with a vertical gradient, built with the standard library only."""
    rows = b"".join(b"\x00" + bytes((y * 255 // height, 80, 160)) * width for y in range(height))
    chunk = lambda tag, data: struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


@unittest.skipUnless(shutil.which("sips"), "cover_image.py needs macOS sips")
class CoverImageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.src = self.tmp / "raw.png"
        write_png(self.src, 1600, 900)

    def run_tool(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)

    def test_check_rejects_16_9(self) -> None:
        r = self.run_tool("check", str(self.src), "--json")
        self.assertEqual(r.returncode, 1)
        data = json.loads(r.stdout)
        self.assertEqual((data["width"], data["height"], data["ok"]), (1600, 900, False))

    def test_crop_produces_1800x766(self) -> None:
        out = self.tmp / "cover.jpg"
        r = self.run_tool("crop", str(self.src), "--out", str(out), "--anchor", "top", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual((data["width"], data["height"], data["exact"]), (1800, 766, True))
        self.assertEqual(data["crop"], "1600x681+0+0")
        self.assertEqual(self.run_tool("check", str(out)).returncode, 0)

    def test_anchor_moves_crop_window(self) -> None:
        r = self.run_tool("crop", str(self.src), "--out", str(self.tmp / "b.png"), "--anchor", "bottom", "--json")
        self.assertEqual(json.loads(r.stdout)["crop"], "1600x681+0+219")

    def test_crop_refuses_to_overwrite_source(self) -> None:
        r = self.run_tool("crop", str(self.src), "--out", str(self.src))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("must differ from the source", r.stderr)


if __name__ == "__main__":
    unittest.main()
