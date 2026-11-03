"""Actual exceptional-format codecs with mocked AI/converter, never external workloads."""
import io
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from acv_tools.acv_unpack import decode_entry, read_index
from acv_tools.acv_upscale import (
    MAGIC,
    RENDERER,
    dds_format,
    dds_info,
    digest,
    preserved_image_format,
    process,
    resize_dds,
    rgba_dds,
    source_dds,
    tag_dds,
)
from acv_tools.texture_manifest import build, capture, stage_update, untag
from tests.test_preserved_p8 import p8
from tests.test_preserved_psd import psd
from tests.test_preserved_volume import volume
from tests.test_texture_manifest import archive


def fake_ai(source, destination, model):
    with Image.open(source) as image:
        image.convert('RGB').resize((image.width * 4, image.height * 4)).save(destination)


def fake_converter(command):
    source, destination = Path(command[-1]), Path(command[command.index('-o') + 1])
    with Image.open(source) as opened:
        image = opened.convert('RGBA')
    if '-ft' in command:
        image.save(destination / (source.stem + '.png'))
        return
    fmt = command[command.index('-f') + 1]
    levels = max(image.size).bit_length() if command[command.index('-m') + 1] == '0' else 1
    blobs = []
    for level in range(levels):
        mip = image.resize((max(1, image.width >> level), max(1, image.height >> level)))
        if fmt.startswith('BC'):
            stream = io.BytesIO()
            mip.save(stream, format='DDS', pixel_format={'BC1_UNORM': 'DXT1', 'BC2_UNORM': 'DXT3',
                                                        'BC3_UNORM': 'DXT5'}[fmt])
            blobs.append(stream.getvalue())
        else:
            blobs.append(rgba_dds(mip))
    header = bytearray(blobs[0][:128])
    struct.pack_into('<I', header, 28, levels)
    if levels > 1:
        struct.pack_into('<I', header, 8, struct.unpack_from('<I', header, 8)[0] | 0x20000)
        struct.pack_into('<I', header, 108, 0x401008)
    (destination / (source.stem + '.dds')).write_bytes(bytes(header) + b''.join(b[128:] for b in blobs))


class ExceptionalUpscaleTests(unittest.TestCase):
    def test_all_three_enlarge_repack_inventory_and_reuse_without_another_ai_call(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'original').mkdir()
            source, staged = root/'original/306.acv', root/'staged'
            staged.mkdir()
            natives = [psd(), p8(), volume()]
            archive(source, list(zip(['gui/psd.dds', 'gui/palette.dds', 'avatar/volume.dds'], natives, strict=True)))
            source_hash = digest(source)
            with patch('acv_tools.dat_upscale.upscale_image', side_effect=fake_ai) as ai, \
                    patch('acv_tools.acv_upscale.run_tool', side_effect=fake_converter):
                result = process(source, staged, root/'converter', pipeline='test', model=root/'model.onnx')
            self.assertEqual(ai.call_count, 3)
            self.assertEqual((result['textures'], result['resized']), (3, 3))
            self.assertNotIn('preserved_images', result)
            self.assertEqual(result['renderer'], RENDERER)
            self.assertEqual(digest(source), source_hash)
            with (staged/source.name).open('rb') as stream:
                outputs = [decode_entry(stream, e) for e in read_index(stream)]
            for native, enhanced, version in zip(natives, outputs, [1, 2, 2], strict=True):
                self.assertEqual(dds_info(enhanced)[:2], (8, 8))
                self.assertEqual(enhanced[32:40], MAGIC)
                self.assertEqual(struct.unpack_from('<I', enhanced, 40)[0], version)
                self.assertIsNotNone(untag(native, enhanced))
                with Image.open(io.BytesIO(source_dds(enhanced))) as image:
                    image.load()
                    self.assertEqual(image.size, (8, 8))
            self.assertEqual(outputs[1][76:108], p8()[76:108])
            self.assertEqual(outputs[2][76:108], volume()[76:108])
            self.assertEqual(struct.unpack_from('<I', outputs[2], 112)[0], 0x200000)
            self.assertEqual(struct.unpack_from('<I', outputs[2], 24)[0], 1)
            self.assertEqual(dds_format(outputs[0]), 'B8G8R8A8_UNORM')
            manifest = root/'manifest'
            self.assertEqual(build(source, staged, manifest)['textures'], 3)
            record = capture(source, staged)
            self.assertEqual(record['textures'][0]['original']['format'], 'PSD')
            with patch('acv_tools.texture_manifest.pipeline_identity', return_value='test'), \
                    patch('acv_tools.dat_upscale.upscale_image', side_effect=AssertionError('No AI')), \
                    patch('acv_tools.acv_upscale.run_tool', side_effect=AssertionError('No converter')):
                report = stage_update(source, root/'updated', manifest, root/'converter', root/'model.onnx')
            self.assertEqual((report['reused'], report['new_or_changed']), (3, 0))
            self.assertEqual(report['result']['output_sha256'], result['output_sha256'])

    def test_p8_shared_palette_preserves_transparent_coverage_and_validates_cache(self):
        raw = bytearray(p8())
        # Red transparent and blue opaque, known RGBA order, every source quadrant represented.
        raw[128:136] = bytes([255, 0, 0, 0, 0, 0, 255, 255])
        raw[1152:1168] = bytes([0, 0, 1, 1] * 4)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch('acv_tools.dat_upscale.upscale_image', side_effect=fake_ai), \
                    patch('acv_tools.acv_upscale.run_tool', side_effect=AssertionError('P8 needs no converter')):
                self.assertTrue(resize_dds(bytes(raw), root/'input.dds', root/'converted', root/'converter',
                                           cache=root/'cache', model=root/'model.onnx'))
            output = (root/'converted/input.dds').read_bytes()
            self.assertEqual(preserved_image_format(output), 'P8')
            self.assertEqual(struct.unpack_from('<I', output, 28)[0], 4)
            with Image.open(io.BytesIO(output)) as image, Image.open(io.BytesIO(raw)) as native:
                actual = image.convert('RGBA')
                alpha = native.convert('RGBA').getchannel('A').resize((8, 8), Image.Resampling.LANCZOS)
                self.assertEqual([a == 0 for a in actual.getchannel('A').getdata()],
                                 [a == 0 for a in alpha.getdata()])
                pixel = actual.getpixel((7, 0))
                self.assertIsInstance(pixel, tuple)
                assert isinstance(pixel, tuple)
                self.assertGreater(pixel[2], 240)
            with patch('acv_tools.dat_upscale.upscale_image', side_effect=AssertionError('Cache must be used')):
                self.assertTrue(resize_dds(bytes(raw), root/'input.dds', root/'cached', root/'converter',
                                           cache=root/'cache', model=root/'model.onnx'))
            self.assertEqual((root/'cached/input.dds').read_bytes(), output)
            tagged = tag_dds(bytes(raw), output)
            self.assertIsNotNone(tagged)
            with self.assertRaisesRegex(ValueError, 'renderer-tagged'):
                preserved_image_format(tagged)

    def test_bad_inputs_and_occupied_tags_do_not_publish(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index, raw in enumerate([psd()[:-1], p8()[:-1], volume()[:-1]]):
                staged = root/f'stage-{index}'
                staged.mkdir()
                source = root/f'{index}.acv'
                archive(source, [('image.dds', raw)])
                with patch('acv_tools.dat_upscale.upscale_image', side_effect=AssertionError('No AI')), \
                        self.assertRaises(ValueError):
                    process(source, staged, root/'converter', model=root/'model.onnx', pipeline='test')
                self.assertFalse((staged/source.name).exists())
            enlarged = bytearray(rgba_dds(Image.new('RGBA', (8, 8))))
            enlarged[65] = 1
            with self.assertRaisesRegex(ValueError, 'reserved fields'):
                tag_dds(psd(), bytes(enlarged))


if __name__ == '__main__':
    unittest.main()
