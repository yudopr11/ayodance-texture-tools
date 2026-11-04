"""Package entry points must start without launching tools or touching game assets."""

import subprocess
import sys
import unittest
from pathlib import Path


class PackageCliTests(unittest.TestCase):
    def test_help_from_project_root(self):
        root = Path(__file__).resolve().parents[1]
        for module in ("acv_unpack", "acv_pack", "acv_upscale", "stage_renderer_pilot", "texture_manifest", "renderer_diagnostics"):
            with self.subTest(module=module):
                result = subprocess.run(
                    [sys.executable, "-B", "-m", f"acv_tools.{module}", "--help"],
                    cwd=root, capture_output=True, text=True, timeout=20,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("usage:", result.stdout.lower())


if __name__ == "__main__":
    unittest.main()
