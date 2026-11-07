"""Synthetic CLI checks; no recovered image or game execution."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from . import trace_loader_failure


class TraceLoaderFailureTests(unittest.TestCase):
    def test_expected_failure_must_be_observed(self):
        for found in (False, True):
            with self.subTest(found=found), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "trace"
                probe = Mock(found=found, reason="synthetic trace")
                probe.run.return_value = b"fixture"
                with patch.object(trace_loader_failure, "FailureTrace", return_value=probe), \
                        patch.object(trace_loader_failure.sys, "argv", ["trace", str(output)]), \
                        patch("builtins.print"):
                    if found:
                        trace_loader_failure.main()
                    else:
                        with self.assertRaisesRegex(RuntimeError, "Fatal report code was not observed"):
                            trace_loader_failure.main()
                self.assertEqual((output / "image.bin").read_bytes(), b"fixture")


if __name__ == "__main__":
    unittest.main()
