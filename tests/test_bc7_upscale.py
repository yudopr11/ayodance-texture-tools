"""Synthetic BC7/DX10 conversion with no external tools or model inference."""
import io
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from acv_tools.acv_unpack import decode_entry, read_index
from acv_tools.acv_upscale import MAGIC, digest, process, source_dds
from acv_tools.texture_manifest import build, capture, stage_update, untag
from tests.test_exceptional_upscale import fake_ai, fake_converter
from tests.test_texture_manifest import archive


def bc7(width=4, height=4, levels=3):
    header = bytearray(148)
    header[:4] = b'DDS '
    struct.pack_into('<7I', header, 4, 124, 0xa1007, height, width, 16, 1, levels)
    struct.pack_into('<8I', header, 76, 32, 4, int.from_bytes(b'DX10', 'little'), 0, 0, 0, 0, 0)
    struct.pack_into('<I', header, 108, 0x401008)
    struct.pack_into('<5I', header, 128, 98, 3, 0, 1, 1)
    # BC7 mode 6, zero endpoints and indices: transparent black, one block per mip.
    blocks = sum(((max(1, width >> level) + 3) // 4) * ((max(1, height >> level) + 3) // 4)
                 for level in range(levels))
    return bytes(header) + (b'\x40' + bytes(15)) * blocks


class BC7UpscaleTests(unittest.TestCase):
    def test_conversion_repack_mips_alpha_manifest_and_reuse(self):
        raw = bc7()
        view = source_dds(raw)
        self.assertEqual(view[84:88], bytes(4))
        self.assertEqual(struct.unpack_from('<I', view, 28)[0], 3)
        with Image.open(io.BytesIO(raw)) as native, Image.open(io.BytesIO(view)) as converted:
            self.assertEqual(native.convert('RGBA').tobytes(), converted.convert('RGBA').tobytes())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'original').mkdir()
            source, staged = root/'original/320.acv', root/'staged'
            staged.mkdir()
            archive(source, [('gui/bc7.dds', raw), ('opaque.bin', b'unchanged')])
            before = digest(source)
            with patch('acv_tools.dat_upscale.upscale_image', side_effect=fake_ai) as ai, \
                    patch('acv_tools.acv_upscale.run_tool', side_effect=fake_converter):
                result = process(source, staged, root/'converter', model=root/'model.onnx', pipeline='test')
            self.assertEqual((result['resized'], ai.call_count), (1, 1))
            self.assertEqual(digest(source), before)
            with (staged/source.name).open('rb') as stream:
                enhanced = decode_entry(stream, read_index(stream)[0])
            self.assertEqual(enhanced[32:40], MAGIC)
            self.assertEqual(struct.unpack_from('<I', enhanced, 28)[0], 4)
            with Image.open(io.BytesIO(enhanced)) as image:
                self.assertEqual(image.size, (8, 8))
                self.assertEqual(image.convert('RGBA').getchannel('A').getextrema(), (0, 0))
            record = capture(source, staged)['textures'][0]
            self.assertEqual(record['original']['format'], 'BC7_UNORM')
            self.assertEqual(record['output']['format'], 'B8G8R8A8_UNORM')
            manifest = root/'manifest'
            build(source, staged, manifest)
            with patch('acv_tools.texture_manifest.pipeline_identity', return_value='test'), \
                    patch('acv_tools.dat_upscale.upscale_image', side_effect=AssertionError('No AI')), \
                    patch('acv_tools.acv_upscale.run_tool', side_effect=AssertionError('No converter')):
                report = stage_update(source, root/'updated', manifest, root/'converter', root/'model.onnx')
            self.assertEqual((report['reused'], report['new_or_changed']), (1, 0))
            self.assertEqual(report['result']['output_sha256'], result['output_sha256'])

    def test_at_cap_bc7_is_not_published_or_certified_unchanged(self):
        raw = bc7(width=2048, levels=1)
        with self.assertRaisesRegex(ValueError, 'At-cap BC7'):
            untag(raw, raw)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, staged = root/'320.acv', root/'staged'
            staged.mkdir()
            archive(source, [('gui/bc7.dds', raw)])
            with patch('acv_tools.dat_upscale.upscale_image', side_effect=AssertionError('No AI')), \
                    self.assertRaisesRegex(ValueError, 'At-cap BC7'):
                process(source, staged, root/'converter', model=root/'model.onnx', pipeline='test')
            self.assertFalse((staged/source.name).exists())

    def test_unsupported_dx10_and_malformed_payloads_are_rejected(self):
        cases = [bc7()[:128], bc7()[:-1], bc7() + b'x']
        for offset, value in [(4, 123), (8, 0), (12, 0), (12, 10000), (24, 2), (28, 32),
                              (80, 0x40), (108, 0x1000), (112, 0x200), (128, 99),
                              (132, 4), (136, 4), (140, 2), (144, 2)]:
            raw = bytearray(bc7())
            struct.pack_into('<I', raw, offset, value)
            cases.append(bytes(raw))
        raw = bytearray(bc7())
        raw[32:40] = MAGIC
        cases.append(bytes(raw))
        for raw in cases:
            with self.subTest(header=raw[:148]), self.assertRaises(ValueError):
                source_dds(raw)


if __name__ == '__main__':
    unittest.main()
