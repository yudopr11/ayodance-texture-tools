"""PSD source validation and historical preservation receipts."""
import json
import shutil
import struct
import tempfile
import unittest
from pathlib import Path

from acv_tools.acv_upscale import digest, is_preserved_psd, receipt_path
from acv_tools.texture_manifest import capture, save, sha256
from tests.test_texture_manifest import archive


def psd():
    return (b'8BPS' + struct.pack('>H6sHIIHH', 1, bytes(6), 3, 4, 4, 8, 3)
            + bytes(12) + bytes(2) + bytes(range(48)))


def legacy_preserved(root, raw, kind):
    """An actual old-style receipt, not a new preservation-only processing mode."""
    source = root / 'original/001.acv'
    source.parent.mkdir()
    staged = root / 'staged'
    staged.mkdir()
    archive(source, [('gui/image.dds', raw)])
    output = staged / source.name
    shutil.copyfile(source, output)
    receipt = {'status': 'verified', 'renderer': 'v64-tagged-dimensions-v1',
               'source_sha256': digest(source), 'output_sha256': digest(output),
               'pipeline': 'historical', 'textures': 0,
               'preserved_images': [{'path': 'gui/image.dds', 'format': kind, 'source_sha256': sha256(raw)}]}
    path = receipt_path(output)
    path.parent.mkdir()
    save(path, receipt)
    return source, staged, receipt


class PreservedPsdTests(unittest.TestCase):
    def test_historical_psd_receipt_still_certifies_native_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, staged, receipt = legacy_preserved(Path(temporary), psd(), 'PSD')
            self.assertEqual(capture(source, staged)['preserved_images'], receipt['preserved_images'])
            self.assertEqual(json.loads(receipt_path(staged/source.name).read_bytes()), receipt)

    def test_invalid_and_oversized_psd_fail(self):
        self.assertTrue(is_preserved_psd(psd()))
        self.assertFalse(is_preserved_psd(b'not a PSD'))
        for raw in [psd()[:-1], psd()[:22] + b'\0\x10' + psd()[24:],
                    psd()[:14] + struct.pack('>II', 5000, 5000) + psd()[22:]]:
            with self.subTest(raw=raw[:26]), self.assertRaises((ValueError, OSError)):
                is_preserved_psd(raw)


if __name__ == '__main__':
    unittest.main()
