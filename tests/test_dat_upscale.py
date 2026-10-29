"""Synthetic DAT tests: no converter, model download or CPU/GPU inference."""

import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from acv_tools.acv_unpack import decode_entry, read_index
from acv_tools.acv_upscale import (
    MAGIC,
    dds_info,
    pipeline_identity,
    process,
    resize_dds,
)
from acv_tools.dat_upscale import tiled_rgb
from tests.test_acv_upscale import tiny_dds


class DatTests(unittest.TestCase):
    def test_tiles_cover_non_square_edges_and_reject_wrong_scale(self):
        from PIL import Image

        image = Image.new('RGB', (301, 177))
        image.putdata([(x % 256, y % 256, (x + y) % 256)
                       for y in range(image.height) for x in range(image.width)])
        calls = []

        def infer(tile):
            calls.append(tile.size)
            return tile.resize((tile.width * 4, tile.height * 4), Image.Resampling.NEAREST)

        result = tiled_rgb(image, infer)
        expected = image.resize(result.size, Image.Resampling.NEAREST)
        self.assertEqual(result.tobytes(), expected.tobytes())
        self.assertEqual(len(calls), 6)
        self.assertLessEqual(max(max(size) for size in calls), 192)
        with self.assertRaisesRegex(ValueError, 'not 4x'):
            tiled_rgb(image, lambda tile: tile)

    def test_fingerprint_tracks_model_converter_and_packages(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            converter, model = root / 'texconv.exe', root / 'model.safetensors'
            converter.write_bytes(b'converter')
            model.write_bytes(b'model')
            with patch('acv_tools.acv_upscale.version', return_value='test'):
                old = pipeline_identity(converter, model=model)
                model.write_bytes(b'updated model')
                current = pipeline_identity(converter, model=model)
                self.assertNotEqual(old, current)
                converter.write_bytes(b'updated converter')
                self.assertNotEqual(current, pipeline_identity(converter, model=model))
                old = pipeline_identity(converter, model=model)
            with patch('acv_tools.acv_upscale.version', return_value='new version'):
                self.assertNotEqual(old, pipeline_identity(converter, model=model))

    def test_all_textures_use_model_cache_alpha_tagging_and_resume(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, staging = root / '009.acv', root / 'stage'
            staging.mkdir()
            raw = tiny_dds()
            payload = zlib.compress(raw)
            names = ['texture/gui/text.dds', 'model/avatar/skin.dds', 'model/avatar/font.dds']
            records = [name.encode().ljust(160, b'\0') +
                       struct.pack('<4I', 7, len(payload), len(raw), i * len(payload))
                       for i, name in enumerate(names)]
            source.write_bytes(struct.pack('<I', 3) + b''.join(records) + payload * 3)
            original = source.read_bytes()
            model, converter = root / 'model.safetensors', root / 'texconv.exe'

            def tool(command):
                destination = Path(command[command.index('-o') + 1])
                if '-ft' in command:
                    Image.new('RGBA', (4, 4), (20, 40, 60, 77)).save(destination / 'input.png')
                else:
                    with Image.open(destination / 'enhanced.png') as image:
                        self.assertEqual(image.size, (8, 8))
                        self.assertEqual(image.getpixel((0, 0)), (90, 100, 110, 77))
                        header = bytearray(raw[:128])
                        struct.pack_into('<3I', header, 12, 8, 8, 32)
                        (destination / 'enhanced.dds').write_bytes(bytes(header) + image.tobytes())

            def enhance(decoded, enhanced, model_path):
                self.assertEqual(model_path, model)
                Image.new('RGB', (16, 16), (90, 100, 110)).save(enhanced)

            with patch('acv_tools.acv_upscale.run_tool', side_effect=tool) as converter_mock, \
                    patch('acv_tools.dat_upscale.upscale_image', side_effect=enhance) as ai:
                result = process(source, staging, converter, pipeline='dat-test', model=model)
                self.assertEqual(ai.call_count, 1)  # Same raw bytes share the model cache, even for UI.
                self.assertEqual(converter_mock.call_count, 2)
                self.assertEqual(process(source, staging, converter,
                                         pipeline='dat-test', model=model), result)
                self.assertEqual(converter_mock.call_count, 2)
            self.assertEqual((result['resized'], result['status']), (3, 'verified'))
            self.assertEqual(result['model'], model.name)
            self.assertEqual(source.read_bytes(), original)
            cached = list((staging / '.texture-cache/dat-test').glob('*.dds'))
            self.assertEqual(len(cached), 1)
            self.assertNotEqual(cached[0].read_bytes()[32:40], MAGIC)
            with (staging / source.name).open('rb') as stream:
                for entry in read_index(stream):
                    rebuilt = decode_entry(stream, entry)
                    self.assertEqual(dds_info(rebuilt)[:2], (8, 8))
                    self.assertEqual(struct.unpack_from('<8s5I', rebuilt, 32),
                                     (MAGIC, 1, 4, 4, 8, 8))
                    self.assertEqual(rebuilt[128:132], bytes((90, 100, 110, 77)))
            self.assertEqual(json.loads((staging / '009.acv.json').read_text()), result)
            with self.assertRaisesRegex(ValueError, 'cannot be safely resumed'):
                process(source, staging, converter, pipeline='other', model=model)

    def test_model_failure_does_not_publish_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = tiny_dds()
            payload = zlib.compress(raw)
            source = root / '009.acv'
            source.write_bytes(struct.pack('<I', 1) + b'gui/text.dds'.ljust(160, b'\0') +
                               struct.pack('<4I', 7, len(payload), len(raw), 0) + payload)
            staging = root / 'stage'
            staging.mkdir()
            with patch('acv_tools.acv_upscale.run_tool'), \
                    patch('acv_tools.dat_upscale.upscale_image', side_effect=ValueError('bad model')), \
                    self.assertRaisesRegex(ValueError, 'gui/text.dds: bad model'):
                process(source, staging, root / 'texconv', pipeline='dat-test', model=root / 'model')
            self.assertFalse((staging / source.name).exists())
            self.assertFalse((staging / (source.name + '.json')).exists())

    def test_tagged_source_is_rejected_before_cache_or_at_cap(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = bytearray(tiny_dds())
            raw[32:40] = MAGIC
            struct.pack_into('<2I', raw, 12, 2048, 2048)
            with self.assertRaisesRegex(ValueError, 'already has renderer metadata'):
                resize_dds(bytes(raw), root / 'input.dds', root / 'out',
                           root / 'texconv.exe', model=root / 'model.safetensors')


if __name__ == '__main__':
    unittest.main()
