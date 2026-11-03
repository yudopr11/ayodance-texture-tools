"""P8 validation and backwards-compatible historical receipts."""
import struct
import tempfile
import unittest
from pathlib import Path

from acv_tools.acv_upscale import MAGIC, preserved_image_format
from acv_tools.texture_manifest import capture
from tests.test_preserved_psd import legacy_preserved


def p8():
    header = bytearray(128)
    header[:4] = b'DDS '
    struct.pack_into('<I', header, 4, 124)
    struct.pack_into('<5I', header, 8, 0x2100f, 4, 4, 4, 0)
    struct.pack_into('<I', header, 28, 3)
    struct.pack_into('<8I', header, 76, 32, 0x20, 0, 8, 0, 0, 0, 0)
    struct.pack_into('<I', header, 108, 0x401008)
    palette = bytes(range(256)) * 4
    return bytes(header) + palette + bytes(range(21))


class PreservedP8Tests(unittest.TestCase):
    def test_historical_p8_receipt_is_still_readable(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, staged, receipt = legacy_preserved(Path(temporary), p8(), 'P8')
            self.assertEqual(capture(source, staged)['preserved_images'], receipt['preserved_images'])

    def test_malformed_oversized_and_tagged_p8_still_fail(self):
        cases = [p8()[:-1], p8()[:100], p8()[:1024], p8() + b'x']
        for offset, values in [(24, (1,)), (28, (32,)), (12, (5000, 5000)), (80, (0x21,)),
                               (88, (16,)), (92, (255,)), (112, (0x200,))]:
            invalid = bytearray(p8())
            struct.pack_into('<' + 'I' * len(values), invalid, offset, *values)
            cases.append(bytes(invalid))
        tagged = bytearray(p8())
        tagged[32:40] = MAGIC
        cases.append(bytes(tagged))
        for raw in cases:
            with self.subTest(header=raw[:128]), self.assertRaises(ValueError):
                preserved_image_format(raw)
        unknown = bytearray(p8())
        struct.pack_into('<I', unknown, 80, 0x40)
        self.assertIsNone(preserved_image_format(unknown))
        self.assertEqual(preserved_image_format(p8()), 'P8')


if __name__ == '__main__':
    unittest.main()
