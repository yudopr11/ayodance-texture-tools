"""Mislabeled PSD regression tests; no converter, AI or game processes."""

import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from acv_tools.acv_pack import pack_archive
from acv_tools.acv_unpack import decode_entry, read_index
from acv_tools.acv_upscale import digest, is_preserved_psd, process, receipt_path
from acv_tools.texture_manifest import build, capture, save, sha256, stage_update
from tests.test_acv_upscale import tiny_dds
from tests.test_texture_manifest import archive, enlarged


def psd():
    # PSD v1, RGB, three channels, raw planar pixels and empty optional sections.
    return (b'8BPS' + struct.pack('>H6sHIIHH', 1, bytes(6), 3, 4, 4, 8, 3)
            + bytes(12) + bytes(2) + bytes(range(48)))


class PreservedPsdTests(unittest.TestCase):
    def test_mixed_archive_preserves_psd_and_logs_it_through_update(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'original').mkdir()
            source, staged = root / 'original/138.acv', root / 'staged'
            staged.mkdir()
            raw = psd()
            archive(source, [('gui/mislabeled.dds', raw), ('gui/real.dds', tiny_dds())])
            source_hash = digest(source)

            def resize(raw, incoming, destination, *args):
                self.assertEqual(raw, tiny_dds())
                destination.mkdir(exist_ok=True)
                (destination / incoming.name).write_bytes(enlarged())
                return True

            with patch('acv_tools.acv_upscale.resize_dds', side_effect=resize) as resize_mock:
                result = process(source, staged, root / 'converter', pipeline='test',
                                 model=root / 'model.onnx')
            resize_mock.assert_called_once()
            expected = [{'path': 'gui/mislabeled.dds', 'format': 'PSD',
                         'source_sha256': sha256(raw)}]
            self.assertEqual(result['preserved_images'], expected)
            self.assertEqual((result['textures'], result['resized']), (1, 1))
            with source.open('rb') as before, (staged / source.name).open('rb') as after:
                old, new = read_index(before)[0], read_index(after)[0]
                self.assertEqual(decode_entry(after, new), raw)
                self.assertEqual(old[2:4], new[2:4])
                before.seek(old[4])
                after.seek(new[4])
                self.assertEqual(before.read(old[2]), after.read(new[2]))
            self.assertEqual(digest(source), source_hash)
            manifest = root / 'manifest'
            self.assertEqual(build(source, staged, manifest)['textures'], 1)
            record = json.loads((manifest / '138.acv.json').read_text())
            self.assertEqual(record['preserved_images'], expected)
            with patch('acv_tools.texture_manifest.pipeline_identity', return_value='test'), \
                    patch('acv_tools.acv_upscale.run_tool', side_effect=AssertionError('No converter')), \
                    patch('acv_tools.dat_upscale.upscale_image', side_effect=AssertionError('No AI')):
                report = stage_update(source, root / 'update', manifest,
                                      root / 'converter', root / 'model.onnx')
            self.assertEqual((report['reused'], report['new_or_changed']), (1, 0))
            self.assertEqual(report['result']['preserved_images'], expected)

    def test_psd_only_archive_is_identical_and_receipt_list_is_checked(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, staged = root / '138.acv', root / 'staged'
            staged.mkdir()
            archive(source, [('gui/mislabeled.dds', psd())])
            with patch('acv_tools.acv_upscale.resize_dds', side_effect=AssertionError('No resizer')):
                result = process(source, staged, root / 'converter', pipeline='test')
            self.assertEqual(result['textures'], 0)
            self.assertEqual(digest(source), digest(staged / source.name))
            self.assertEqual(capture(source, staged)['preserved_images'], result['preserved_images'])
            receipt = receipt_path(staged / source.name)
            del result['preserved_images']
            save(receipt, result)
            with self.assertRaisesRegex(ValueError, 'Preserved images differ'):
                capture(source, staged)

    def test_capture_rejects_changed_psd_stored_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, staged = root / '138.acv', root / 'staged'
            staged.mkdir()
            archive(source, [('gui/mislabeled.dds', psd())])
            result = process(source, staged, root / 'converter', pipeline='test')
            replacements = root / 'replacements'
            (replacements / 'gui').mkdir(parents=True)
            (replacements / 'gui/mislabeled.dds').write_bytes(psd()[:-1] + b'\xff')
            output = staged / source.name
            tampered = root / 'tampered.acv'
            pack_archive(output, replacements, tampered)
            tampered.replace(output)
            result['output_sha256'] = digest(output)
            save(receipt_path(output), result)
            with self.assertRaisesRegex(ValueError, 'Preserved PSD stored payload changed'):
                capture(source, staged)

    def test_invalid_images_and_duplicate_paths_still_fail(self):
        cases = [(b'DDS ' + bytes(124), False), (psd()[:30], False),
                 (b'not an image', False), (psd(), True)]
        for raw, duplicate in cases:
            with self.subTest(raw=raw[:8], duplicate=duplicate), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source, staged = root / '138.acv', root / 'staged'
                staged.mkdir()
                entries = [('gui/bad.dds', raw)]
                if duplicate:
                    entries.append(('GUI/BAD.DDS', raw))
                archive(source, entries)
                with patch('acv_tools.acv_upscale.run_tool', side_effect=AssertionError('No converter')), \
                        self.assertRaises(ValueError):
                    process(source, staged, root / 'converter', pipeline='test')
                self.assertFalse((staged / source.name).exists())
                self.assertFalse(receipt_path(staged / source.name).exists())

    def test_psd_decoded_pixel_limit(self):
        oversized = bytearray(psd())
        struct.pack_into('>II', oversized, 14, 5000, 5000)
        with self.assertRaisesRegex(ValueError, 'oversized PSD'):
            is_preserved_psd(bytes(oversized))


if __name__ == '__main__':
    unittest.main()
