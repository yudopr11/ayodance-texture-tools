"""Synthetic DAT tests: no converter, model download or CPU/GPU inference."""

import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from acv_tools.acv_unpack import decode_entry, read_index
from acv_tools.acv_upscale import (
    MAGIC,
    dds_info,
    pipeline_identity,
    process,
    receipt_path,
    resize_dds,
)
from acv_tools.dat_upscale import load_onnx, tiled_rgb, upscale_image
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

    def test_onnx_session_requires_gpu_and_is_reused(self):
        runtime, session = MagicMock(), MagicMock()
        runtime.get_available_providers.return_value = ['DmlExecutionProvider', 'CPUExecutionProvider']
        events = [{'args': {'provider': 'DmlExecutionProvider', 'op_name': 'Conv'}}]

        def create_session(*args, **kwargs):
            profile = Path(kwargs['sess_options'].profile_file_prefix + '.json')
            profile.write_text(json.dumps(events))
            session.end_profiling.return_value = str(profile)
            return session

        runtime.InferenceSession.side_effect = create_session
        session.get_providers.return_value = ['DmlExecutionProvider', 'CPUExecutionProvider']
        session.get_inputs.return_value = [SimpleNamespace(name='input', type='tensor(float)', shape=[1, 3, 'H', 'W'])]
        session.get_outputs.return_value = [object()]
        load_onnx.cache_clear()
        try:
            with patch.dict('sys.modules', {'onnxruntime': runtime}):
                self.assertIs(load_onnx('model.onnx'), session)
                self.assertIs(load_onnx('model.onnx'), session)
                runtime.InferenceSession.assert_called_once()
                session.disable_fallback.assert_called_once()
                self.assertEqual(runtime.InferenceSession.call_args.kwargs['providers'],
                                 [('DmlExecutionProvider', {'device_id': '0'})])
                options = runtime.SessionOptions.return_value
                self.assertFalse(options.enable_mem_pattern)
                self.assertEqual(options.execution_mode, runtime.ExecutionMode.ORT_SEQUENTIAL)
                runtime.get_available_providers.return_value = ['CPUExecutionProvider']
                with self.assertRaisesRegex(ValueError, 'requires onnxruntime-directml'):
                    load_onnx('missing-gpu.onnx')
                runtime.get_available_providers.return_value = ['DmlExecutionProvider']
                session.get_providers.return_value = ['CPUExecutionProvider']
                with self.assertRaisesRegex(ValueError, 'fell back to CPU'):
                    load_onnx('fallback.onnx')
                session.get_providers.return_value = ['DmlExecutionProvider', 'CPUExecutionProvider']
                events[:] = [{'args': {'provider': 'CPUExecutionProvider', 'op_name': 'Conv'}},
                             {'args': {'provider': 'DmlExecutionProvider', 'op_name': 'MemcpyFromHost'}}]
                with self.assertRaisesRegex(ValueError, 'no profiled DirectML computation'):
                    load_onnx('registered-but-unused.onnx')
        finally:
            load_onnx.cache_clear()

    def test_onnx_pixels_padding_shapes_and_failure_checks(self):
        import numpy as np
        from PIL import Image

        session = MagicMock()
        session.get_inputs.return_value = [SimpleNamespace(name='input')]

        def infer(outputs, feed):
            tensor = feed['input']
            self.assertEqual(tensor.dtype, np.float32)
            self.assertTrue(tensor.flags.c_contiguous)
            self.assertGreaterEqual(min(tensor.shape[2:]), 16)
            if image.width <= 128 and image.height <= 128:
                def indices(length):
                    result = list(range(length))
                    result += list(range(length - 2, -1, -1))[:max(0, 16 - length)]
                    result += [result[-1]] * max(0, 16 - len(result))
                    return result

                expected = np.asarray(image.convert('RGB'), dtype=np.float32)
                expected = expected[indices(image.height)][:, indices(image.width)] / np.float32(255)
                np.testing.assert_array_equal(tensor[0].transpose(1, 2, 0), expected)
            return [tensor.repeat(4, axis=2).repeat(4, axis=3)]

        session.run.side_effect = infer
        with tempfile.TemporaryDirectory() as temporary, \
                patch('acv_tools.dat_upscale.load_onnx', return_value=session):
            root = Path(temporary)
            source, destination = root / 'input.png', root / 'output.png'
            for size in ((1, 1), (1, 7), (7, 1), (3, 7), (31, 17), (301, 177)):
                with self.subTest(size=size):
                    image = Image.new('RGBA', size)
                    image.putdata([((x * 17 + y * 13) % 256, x % 256, y % 256, 77)
                                   for y in range(size[1]) for x in range(size[0])])
                    image.save(source)
                    upscale_image(source, destination, root / 'model.onnx')
                    with Image.open(destination) as result:
                        expected = image.convert('RGB').resize((size[0] * 4, size[1] * 4),
                                                               Image.Resampling.NEAREST)
                        self.assertEqual(result.tobytes(), expected.tobytes())
            Image.new('RGB', (3, 7)).save(source)
            session.run.side_effect = None
            session.run.return_value = [np.full((1, 3, 64, 64), np.nan, dtype=np.float32)]
            with self.assertRaisesRegex(ValueError, 'non-finite'):
                upscale_image(source, destination, root / 'model.onnx')
            session.run.return_value = [np.zeros((1, 3, 32, 32), dtype=np.float32)]
            with self.assertRaisesRegex(ValueError, 'not 4x'):
                upscale_image(source, destination, root / 'model.onnx')

    def test_fingerprint_tracks_model_converter_and_packages(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            converter, model = root / 'texconv.exe', root / 'model.safetensors'
            converter.write_bytes(b'converter')
            model.write_bytes(b'model')
            with patch('acv_tools.acv_upscale.version', return_value='test'):
                old = pipeline_identity(converter, model=model)
                onnx = root / 'model.onnx'
                onnx.write_bytes(model.read_bytes())
                self.assertNotEqual(old, pipeline_identity(converter, model=onnx))
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
            self.assertEqual(json.loads((staging / '.metadata/009.acv.json').read_text()), result)
            with self.assertRaisesRegex(ValueError, 'cannot be safely resumed'):
                process(source, staging, converter, pipeline='other', model=model)

    def test_duplicate_opaque_records_are_preserved_when_replacing_texture(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, staging = root / '058.acv', root / 'stage'
            staging.mkdir()
            raw = tiny_dds()
            names = ['opaque.bin', 'gui/text.dds', 'opaque.bin', 'opaque.bin']
            payloads = [b'not zlib: first', zlib.compress(raw), b'not zlib: second', b'not zlib: third']
            records, offset = [], 0
            for i, (name, payload) in enumerate(zip(names, payloads, strict=True)):
                records.append(name.encode().ljust(160, b'\0') +
                               struct.pack('<4I', i + 7, len(payload), len(raw), offset))
                offset += len(payload)
            original = struct.pack('<I', len(records)) + b''.join(records) + b''.join(payloads)
            source.write_bytes(original)
            header = bytearray(raw[:128])
            struct.pack_into('<3I', header, 12, 8, 8, 32)
            enlarged = bytes(header) + raw[128:132] * 64

            def resize(raw, incoming, destination, *args):
                destination.mkdir(parents=True, exist_ok=True)
                (destination / incoming.name).write_bytes(enlarged)
                return True

            with patch('acv_tools.acv_upscale.resize_dds', side_effect=resize) as resizer:
                result = process(source, staging, root / 'texconv', pipeline='duplicate-test')
            self.assertEqual((result['textures'], result['resized'], result['status']), (1, 1, 'verified'))
            resizer.assert_called_once()
            self.assertEqual(source.read_bytes(), original)
            with source.open('rb') as before, (staging / source.name).open('rb') as after:
                old_entries, new_entries = read_index(before), read_index(after)
                self.assertEqual(len(old_entries), len(new_entries))
                for i, (old, new) in enumerate(zip(old_entries, new_entries, strict=True)):
                    self.assertEqual(old[:2], new[:2])
                    self.assertEqual(old[5:], new[5:])
                    if i == 1:
                        rebuilt = decode_entry(after, new)
                        self.assertEqual(dds_info(rebuilt)[:2], (8, 8))
                        self.assertEqual(rebuilt[32:40], MAGIC)
                    else:
                        self.assertEqual(old[2:4], new[2:4])
                        after.seek(new[4])
                        self.assertEqual(after.read(new[2]), payloads[i])

    def test_duplicate_dds_and_unsafe_opaque_paths_still_fail(self):
        for names, message in [(['gui/text.dds', 'GUI\\TEXT.DDS'], 'Duplicate DDS archive paths'),
                               (['../opaque.bin', 'gui/text.dds'], 'Unsafe')]:
            with self.subTest(names=names), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source, staging = root / '058.acv', root / 'stage'
                staging.mkdir()
                raw = tiny_dds()
                payload = zlib.compress(raw)
                records = [name.encode().ljust(160, b'\0') +
                           struct.pack('<4I', 7, len(payload), len(raw), i * len(payload))
                           for i, name in enumerate(names)]
                source.write_bytes(struct.pack('<I', len(records)) + b''.join(records) + payload * len(records))
                with patch('acv_tools.acv_upscale.resize_dds') as resizer, \
                        self.assertRaisesRegex(ValueError, message):
                    process(source, staging, root / 'texconv', pipeline='duplicate-test')
                resizer.assert_not_called()
                self.assertFalse((staging / source.name).exists())
                self.assertFalse(receipt_path(staging / source.name).exists())

    def test_dxt_unused_fields_preserved_but_changed_formats_rejected(self):
        from PIL import Image

        for fourcc, bits, converted_fourcc, flags in [(b'DXT1', 256, b'DXT1', 4),
                                                     (b'DXT3', 256, b'DXT3', 4),
                                                     (b'DXT5', 24, b'DXT5', 4),
                                                     (b'DXT3', 16, b'DXT3', 4),
                                                     (b'DXT3', 256, b'DXT5', 4),
                                                     (b'DXT3', 16, b'DXT5', 4),
                                                     (b'DXT3', 256, b'DXT3', 5)]:
            with self.subTest(fourcc=fourcc, bits=bits, converted=converted_fourcc, flags=flags), \
                    tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                raw = bytearray(tiny_dds()[:128])
                masks = (0x7c00, 0x3e0, 0x1f, 0) if bits == 16 else (11, 22, 33, 44)
                struct.pack_into('<7I', raw, 80, 4, int.from_bytes(fourcc, 'little'),
                                 bits, *masks)
                raw = bytes(raw) + bytes(8 if fourcc == b'DXT1' else 16)
                header = bytearray(raw[:128])
                struct.pack_into('<2I', header, 12, 8, 8)
                struct.pack_into('<7I', header, 80, flags, int.from_bytes(converted_fourcc, 'little'),
                                 0, 0, 0, 0, 0)
                pixels = bytes(range(32 if converted_fourcc == b'DXT1' else 64))
                converted = bytes(header) + pixels

                def tool(command, converted=converted):
                    destination = Path(command[command.index('-o') + 1])
                    if '-ft' in command:
                        Image.new('RGBA', (4, 4)).save(destination / 'input.png')
                    else:
                        (destination / 'enhanced.dds').write_bytes(converted)

                def enhance(source, destination, model):
                    Image.new('RGB', (16, 16)).save(destination)

                with patch('acv_tools.acv_upscale.run_tool', side_effect=tool), \
                        patch('acv_tools.dat_upscale.upscale_image', side_effect=enhance):
                    args = (raw, root / 'input.dds', root / 'out', root / 'texconv')
                    if fourcc != converted_fourcc or flags != 4:
                        with self.assertRaisesRegex(ValueError, 'pixel format changed'):
                            resize_dds(*args, model=root / 'model.onnx')
                    else:
                        self.assertTrue(resize_dds(*args, model=root / 'model.onnx'))
                        result = (root / 'out/input.dds').read_bytes()
                        self.assertEqual(result[76:108], raw[76:108])
                        self.assertEqual(result[128:], pixels)
                        self.assertEqual(dds_info(result)[:2], (8, 8))

    def test_gpu_receipt_records_directml_device(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = tiny_dds()
            payload = zlib.compress(raw)
            source, staging = root / '009.acv', root / 'stage'
            source.write_bytes(struct.pack('<I', 1) + b'gui/text.dds'.ljust(160, b'\0') +
                               struct.pack('<4I', 7, len(payload), len(raw), 0) + payload)
            staging.mkdir()
            with patch('acv_tools.acv_upscale.resize_dds', return_value=False):
                result = process(source, staging, root / 'texconv', pipeline='gpu-test',
                                 model=root / 'model.onnx')
            self.assertEqual(result['device'], 'directml:0')
            self.assertEqual(result['status'], 'verified')
            self.assertEqual(json.loads((staging / '.metadata/009.acv.json').read_text()), result)

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
            self.assertFalse(receipt_path(staging / source.name).exists())

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
