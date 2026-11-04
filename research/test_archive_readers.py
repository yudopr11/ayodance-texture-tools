"""CPU-only research checks; requires the retained native image and Unicorn."""

import json
import random
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from unicorn.x86_const import UC_X86_REG_ESP

from acv_tools.acv_pack import encode_index
from research import emulate_archive_index, emulate_archive_open
from research.emulate_archive_index import IndexReader, inspect
from research.emulate_native_inflate import image_bytes
from tests.test_acv_pack import make_tagged_archive


class ArchiveReaderTests(unittest.TestCase):
    def test_native_encoded_index_variants(self):
        image = image_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "262.acv"
            for variant in (0, 1, 2, 3, 5, 6):
                for count in (1, 5, 13):
                    with self.subTest(variant=variant, count=count):
                        archive.write_bytes(make_tagged_archive(variant, count))
                        result = inspect(image, archive)
                        self.assertEqual(result["count"], count)
                        self.assertEqual(result["status"], "PASS" if variant >= 2 else "NOT_ENCODED")

    def test_native_index_overrun_is_not_a_pass(self):
        tag = 0xff020025
        name = random.Random(17).randbytes(159).replace(b"\0", b"X") + b"\0"
        packed = zlib.compress(b"x")
        record = name + struct.pack("<5I", 123, len(packed), 1, 0, tag)
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "262.acv"
            archive.write_bytes(encode_index(record, tag, 1) + packed)
            with self.assertRaisesRegex(AssertionError, "outside live allocation"):
                inspect(image_bytes(), archive)

    def test_wrong_native_crypto_mode_is_not_corrected(self):
        original = IndexReader.callback
        def wrong_mode(reader, uc, address, size, context):
            if address == 0xc39100:
                reader.put(uc.reg_read(UC_X86_REG_ESP) + 24, 0)
            return original(reader, uc, address, size, context)
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "262.acv"
            archive.write_bytes(make_tagged_archive(2))
            with (patch.object(IndexReader, "callback", wrong_mode),
                  self.assertRaisesRegex(AssertionError, "crypto arguments changed")):
                inspect(image_bytes(), archive)

    def test_index_failure_preserves_completed_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "failure.json"
            with (patch("sys.argv", ["index", temporary, str(report)]),
                  patch.object(emulate_archive_index, "image_bytes", return_value=b""),
                  patch.object(emulate_archive_index, "inspect", side_effect=[
                      {"status": "PASS", "count": 1}, ValueError("injected")]),
                  self.assertRaisesRegex(ValueError, "injected")):
                emulate_archive_index.main()
            self.assertEqual([row["status"] for row in json.loads(report.read_text())["rows"]],
                             ["PASS", "FAIL"])

    def test_report_cli_preserves_allocation_failure_and_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "failure.json"
            with (patch("sys.argv", ["emulate_archive_open", temporary, str(report)]),
                  patch.object(emulate_archive_open, "image_bytes", return_value=b""),
                  patch.object(emulate_archive_open, "fixtures", return_value=[]),
                  patch.object(emulate_archive_open, "Reader") as reader):
                reader.return_value.run.return_value = {"status": "MODELED_ALLOCATION_FAILURE"}
                with self.assertRaises(SystemExit) as error:
                    emulate_archive_open.main()
            self.assertEqual(error.exception.code, 1)
            self.assertEqual(len(json.loads(report.read_text())["runs"]), 8)


if __name__ == "__main__":
    unittest.main()
