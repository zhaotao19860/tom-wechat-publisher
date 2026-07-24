from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SYSTEM_PYTHON = Path("/usr/bin/python3")


class PythonCompatibilityTests(unittest.TestCase):
    def test_all_scripts_compile_with_python_39(self) -> None:
        python = SYSTEM_PYTHON if SYSTEM_PYTHON.exists() else Path(sys.executable)
        scripts = sorted((ROOT / "scripts").glob("*.py"))
        self.assertTrue(scripts)
        result = subprocess.run(
            [str(python), "-m", "py_compile", *map(str, scripts)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_prepare_module_imports_with_python_39(self) -> None:
        python = SYSTEM_PYTHON if SYSTEM_PYTHON.exists() else Path(sys.executable)
        result = subprocess.run(
            [
                str(python),
                "-c",
                "import runpy; runpy.run_path('scripts/prepare_wechat_md.py', run_name='compat_check')",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
