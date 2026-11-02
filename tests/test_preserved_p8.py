"""Palette-indexed DDS preservation; no converter, GPU or proprietary fixtures."""

import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from acv_tools.acv_pack import pack_archive
from acv_tools.acv_unpack import decode_entry, read_index
from acv_tools.acv_upscale import (
    MAGIC,
    dds_format,
    digest,
    preserved_image_format,
    process,
    receipt_path,
)
from acv_tools.texture_manifest import build, capture, save, sha256, stage_update
from tests.test_acv_upscale import tiny_dds
from tests.test_texture_manifest import archive, enlarged


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
    def test_palette_mips_and_stored_bytes_survive_mixed_repack_and_update(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'original').mkdir()
            source, staged = root / 'original/256.acv', root / 'staged'
            staged.mkdir()
            raw = p8()
            archive(source, [('avatar/palette.dds', raw), ('gui/rgb.dds', tiny_dds())])
            original_hash = digest(source)

            def resize(native, incoming, destination, *args):
                self.assertEqual(native, tiny_dds())
                destination.mkdir(exist_ok=True)
                (destination / incoming.name).write_bytes(enlarged())
                return True

            with patch('acv_tools.acv_upscale.resize_dds', side_effect=resize) as resize_mock:
                result = process(source, staged, root / 'converter', pipeline='test',
                                 model=root / 'model.onnx')
            resize_mock.assert_called_once()
            expected = [{'path': 'avatar/palette.dds', 'format': 'P8',
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
            self.assertEqual(digest(source), original_hash)
            manifest = root / 'manifest'
            self.assertEqual(build(source, staged, manifest)['textures'], 1)
            self.assertEqual(capture(source, staged)['preserved_images'], expected)
            with patch('acv_tools.texture_manifest.pipeline_identity', return_value='test'), \
                    patch('acv_tools.acv_upscale.run_tool', side_effect=AssertionError('No converter')), \
                    patch('acv_tools.dat_upscale.upscale_image', side_effect=AssertionError('No AI')):
                report = stage_update(source, root / 'update', manifest,
                                      root / 'converter', root / 'model.onnx')
            self.assertEqual((report['reused'], report['new_or_changed']), (1, 0))
            self.assertEqual(report['result']['preserved_images'], expected)

    def test_palette_only_archive_is_identical_and_receipt_is_checked(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, staged = root / '256.acv', root / 'staged'
            staged.mkdir()
            archive(source, [('palette.dds', p8())])
            with patch('acv_tools.acv_upscale.resize_dds', side_effect=AssertionError('No resizer')):
                result = process(source, staged, root / 'converter', pipeline='test')
            self.assertEqual(result['textures'], 0)
            self.assertEqual(digest(source), digest(staged / source.name))
            self.assertEqual(capture(source, staged)['preserved_images'], result['preserved_images'])
            del result['preserved_images']
            save(receipt_path(staged / source.name), result)
            with self.assertRaisesRegex(ValueError, 'Preserved images differ'):
                capture(source, staged)

    def test_changed_palette_cannot_be_certified(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, staged = root / '256.acv', root / 'staged'
            staged.mkdir()
            archive(source, [('palette.dds', p8())])
            result = process(source, staged, root / 'converter', pipeline='test')
            replacements = root / 'replacements'
            replacements.mkdir()
            modified = bytearray(p8())
            modified[128] ^= 255
            (replacements / 'palette.dds').write_bytes(modified)
            output = staged / source.name
            pack_archive(output, replacements, root / 'changed.acv')
            (root / 'changed.acv').replace(output)
            result['output_sha256'] = digest(output)
            save(receipt_path(output), result)
            with self.assertRaisesRegex(ValueError, 'Preserved P8 stored payload changed'):
                capture(source, staged)

    def test_malformed_oversized_and_tagged_p8_still_fail(self):
        cases = [p8()[:-1], p8()[:1024], p8() + b'x']
        for offset, values in [(28, (32,)), (12, (5000, 5000)), (80, (0x21,)),
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
        with self.assertRaisesRegex(ValueError, 'Unsupported DDS channel masks'):
            dds_format(unknown)
        self.assertEqual(preserved_image_format(p8()), 'P8')


if __name__ == '__main__':
    unittest.main()
