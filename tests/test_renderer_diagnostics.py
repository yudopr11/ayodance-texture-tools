import hashlib
import io
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

from acv_tools.acv_pack import pack_archive
from acv_tools.acv_unpack import read_index
from acv_tools.renderer_diagnostics import find_upload
from tests.test_acv_pack import make_tagged_archive


class RendererDiagnosticsTests(unittest.TestCase):
    def test_maps_tagged_archives_and_reports_all_prefix_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / 'template.acv'
            template.write_bytes(make_tagged_archive(5, count=3))
            replacements = root / 'files'
            payload = replacements / 'texture/item2.dds'
            payload.parent.mkdir(parents=True)
            raw = b'DDS ' + bytes(4092) + b'first'
            payload.write_bytes(raw)
            archive = root / '001.acv'
            pack_archive(template, replacements, archive)
            payload.write_bytes(raw[:-5] + b'other')
            pack_archive(template, replacements, root / '002.acv')
            crc = zlib.crc32(raw[:4096])
            match = find_upload(archive, len(raw), crc)
            self.assertEqual(len(match), 1)
            self.assertEqual(match[0]['path'], 'texture\\item2.dds')
            self.assertEqual(match[0]['sha256'], hashlib.sha256(raw).hexdigest())
            self.assertEqual(len(find_upload(root, len(raw), crc)), 2)
            self.assertEqual(find_upload(root, len(raw), crc ^ 1), [])
            self.assertEqual(find_upload(root, len(raw) + 1, crc), [])

    def test_corrupt_compressed_payload_is_a_controlled_cli_error(self):
        with tempfile.TemporaryDirectory() as directory:
            data = bytearray(make_tagged_archive(0, count=3))
            entry = read_index(io.BytesIO(data))[2]
            data[entry[4]:entry[4] + 2] = b'xx'
            archive = Path(directory) / 'broken-payload.acv'
            archive.write_bytes(data)
            result = subprocess.run([sys.executable, '-B', '-m', 'acv_tools.renderer_diagnostics',
                                     str(archive), '--bytes', str(entry[3]), '--crc32', '0'],
                                    capture_output=True, text=True, timeout=20)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('error:', result.stderr)
            self.assertNotIn('Traceback', result.stderr)

    def test_bad_parameters_and_corruption_are_not_silently_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for size, crc in ((0, 0), (64 * 1024 * 1024 + 1, 0), (128, -1), (128, 2**32)):
                with self.subTest(size=size, crc=crc), self.assertRaises(ValueError):
                    find_upload(root, size, crc)
            with self.assertRaisesRegex(ValueError, 'No ACV files'):
                find_upload(root, 128, 0)
            (root / 'broken.acv').write_bytes(b'broken')
            with self.assertRaises(ValueError):
                find_upload(root, 128, 0)


if __name__ == '__main__':
    unittest.main()
