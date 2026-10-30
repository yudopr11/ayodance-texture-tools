import hashlib
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from acv_tools.acv_pack import pack_archive
from acv_tools.acv_unpack import decode_entry, read_index
from acv_tools.acv_upscale import (
    MAGIC,
    RENDERER,
    ai_image,
    dds_format,
    dds_info,
    digest,
    pipeline_identity,
    process,
    receipt_path,
    resize_dds,
    restore_rgb24,
    target_size,
)


def tiny_dds():
    header = bytearray(128)
    header[:4] = b'DDS '
    struct.pack_into('<7I', header, 4, 124, 0x100f, 4, 4, 16, 0, 1)
    struct.pack_into('<8I', header, 76, 32, 0x41, 0, 32,
                     0xff, 0xff00, 0xff0000, 0xff000000)
    struct.pack_into('<I', header, 108, 0x1000)
    return bytes(header) + bytes((128, 64, 32, 128)) * 16


class UpscaleTests(unittest.TestCase):
    def test_dimensions_and_rejected_headers(self):
        self.assertEqual(target_size(256, 512), (512, 1024))
        self.assertEqual(target_size(1024, 2048), (1024, 2048))
        self.assertEqual(target_size(1536, 768), (2048, 1024))
        self.assertEqual(target_size(4096, 4096), (4096, 4096))
        self.assertEqual(dds_info(tiny_dds())[:2], (4, 4))
        for raw in (b'not a DDS', tiny_dds()[:100]):
            with self.assertRaises(ValueError):
                dds_info(raw)
        raw = bytearray(tiny_dds())
        struct.pack_into('<I', raw, 112, 0x200)
        with self.assertRaises(ValueError):
            dds_info(raw)

    def test_ai_preserves_source_alpha_and_uses_enhanced_rgb(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            decoded, enhanced = root / 'source.png', root / 'ai.png'
            Image.new('RGBA', (4, 4), (20, 40, 60, 77)).save(decoded)
            Image.new('RGB', (16, 16), (90, 100, 110)).save(enhanced)
            ai_image(decoded, enhanced, (8, 8))
            with Image.open(enhanced) as output:
                self.assertEqual(output.size, (8, 8))
                self.assertEqual(output.getpixel((0, 0)), (90, 100, 110, 77))
            with self.assertRaises(ValueError):
                ai_image(decoded, enhanced, (8, 8))
        self.assertEqual(dds_format(tiny_dds()), 'R8G8B8A8_UNORM')
        raw = bytearray(tiny_dds())
        struct.pack_into('<I', raw, 80, 4)
        raw[84:88] = b'DXT5'
        self.assertEqual(dds_format(raw), 'BC3_UNORM')
        raw[84:88] = b'????'
        with self.assertRaises(ValueError):
            dds_format(raw)

    def test_rgb24_restores_all_mips_and_rejects_truncation(self):
        raw = bytearray(tiny_dds())
        struct.pack_into('<7I', raw, 80, 0x40, 0, 24, 0xff0000, 0xff00, 0xff, 0)
        converted = bytearray(tiny_dds())
        struct.pack_into('<I', converted, 28, 3)
        converted = bytes(converted[:128]) + bytes((30, 20, 10, 255)) * 21
        restored = restore_rgb24(converted, raw)
        self.assertEqual(dds_info(restored)[2], dds_info(raw)[2])
        self.assertEqual(restored[128:], bytes((30, 20, 10)) * 21)
        self.assertEqual(struct.unpack_from('<I', restored, 20)[0], 12)
        with self.assertRaises(ValueError):
            restore_rgb24(converted[:-1], raw)

    @unittest.skipUnless(Path('tools/esrgan/realesrgan-ncnn-vulkan.exe').exists(),
                         'Requires installed ESRGAN and GPU')
    def test_ai_legacy_pixel_formats(self):
        texconv = Path('tools/texconv.exe').resolve()
        esrgan = Path('tools/esrgan/realesrgan-ncnn-vulkan.exe').resolve()
        for bits, masks, pixels in (
            (24, (0xff0000, 0xff00, 0xff, 0), bytes((30, 20, 10)) * 16),
            (16, (0xf00, 0xf0, 0xf, 0xf000), bytes((0x23, 0x81)) * 16),
            (16, (0x7c00, 0x3e0, 0x1f, 0), bytes((0x23, 0x11)) * 16),
        ):
            with self.subTest(bits=bits), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                raw = bytearray(tiny_dds()[:128])
                struct.pack_into('<7I', raw, 80, 0x41 if masks[-1] else 0x40,
                                 0, bits, *masks)
                struct.pack_into('<I', raw, 20, 4 * bits // 8)
                raw = bytes(raw) + pixels
                self.assertTrue(resize_dds(raw, root / 'input.dds', root / 'out', texconv, esrgan))
                result = (root / 'out/input.dds').read_bytes()
                self.assertEqual(dds_info(result)[:2], (8, 8))
                self.assertEqual(dds_info(result)[2], dds_info(raw)[2])

    def test_fresh_ai_cache_stays_untagged_while_archive_is_tagged(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = tiny_dds()
            enhanced = bytearray(raw[:128])
            struct.pack_into('<3I', enhanced, 12, 8, 8, 32)
            enhanced = bytes(enhanced) + raw[128:] * 4
            payload = zlib.compress(raw)
            source = root / '009.acv'
            record = b'GUI/hud/spectator.dds'.ljust(160, b'\0')
            source.write_bytes(struct.pack('<I', 1) + record +
                               struct.pack('<4I', 7, len(payload), len(raw), 0) + payload)
            staging = root / 'staging'
            staging.mkdir()
            texconv, esrgan = root / 'texconv.exe', root / 'esrgan.exe'

            def tool(command):
                if command[0] == esrgan:
                    Image.new('RGB', (16, 16), (90, 100, 110)).save(command[4])
                elif '-ft' in command:
                    destination = Path(command[command.index('-o') + 1])
                    Image.new('RGBA', (4, 4), (20, 40, 60, 128)).save(destination / 'input.png')
                else:
                    destination = Path(command[command.index('-o') + 1])
                    (destination / 'enhanced.dds').write_bytes(enhanced)

            with patch('acv_tools.acv_upscale.run_tool', side_effect=tool) as mocked:
                result = process(source, staging, texconv, esrgan, pipeline='test-image-cache')
            self.assertEqual(mocked.call_count, 3)
            self.assertEqual(result['renderer'], RENDERER)
            cached = staging / '.texture-cache/test-image-cache' / (hashlib.sha256(raw).hexdigest() + '.dds')
            self.assertEqual(cached.read_bytes(), enhanced)
            self.assertEqual(cached.with_suffix('.sha256').read_text(), digest(cached))
            self.assertNotEqual(cached.read_bytes()[32:40], MAGIC)
            with (staging / source.name).open('rb') as stream:
                marked = decode_entry(stream, read_index(stream)[0])
            self.assertEqual(struct.unpack_from('<8s5I', marked, 32), (MAGIC, 1, 4, 4, 8, 8))
            self.assertEqual(marked[76:], enhanced[76:])

    def test_pipeline_identity_tracks_model_and_converter(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            texconv, esrgan = root / 'texconv.exe', root / 'esrgan.exe'
            models = root / 'models'
            models.mkdir()
            files = [texconv, esrgan, models / 'realesrgan-x4plus.bin',
                     models / 'realesrgan-x4plus.param']
            for path in files:
                path.write_bytes(b'original')
            original = pipeline_identity(texconv, esrgan)
            files[2].write_bytes(b'new model')
            self.assertNotEqual(pipeline_identity(texconv, esrgan), original)
            current = pipeline_identity(texconv, esrgan)
            texconv.write_bytes(b'new converter')
            self.assertNotEqual(pipeline_identity(texconv, esrgan), current)

    @unittest.skipUnless(Path('tools/texconv.exe').exists(), 'Requires DirectXTex texconv')
    def test_resize_repack_verify_and_resume(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = tiny_dds()
            payload = zlib.compress(raw)
            untouched = zlib.compress(b'opaque non-texture')
            record = b'model/test.dds'.ljust(160, b'\0') + struct.pack('<4I', 7, len(payload), len(raw), 0)
            record += b'other.bin'.ljust(160, b'\0') + struct.pack('<4I', 9, len(untouched), 18, len(payload))
            source = root / '001.acv'
            source.write_bytes(struct.pack('<I', 2) + record + payload + untouched)
            snapshot = source.read_bytes()
            staging = root / 'staging'
            staging.mkdir()
            result = process(source, staging, Path('tools/texconv.exe').resolve())
            self.assertEqual((result['status'], result['resized']), ('verified', 1))
            with (staging / source.name).open('rb') as stream:
                entries = read_index(stream)
                converted = decode_entry(stream, entries[0])
                self.assertEqual(dds_info(converted)[:2], (8, 8))
                self.assertEqual(dds_info(converted)[2], dds_info(raw)[2])
                self.assertEqual(struct.unpack_from('<8s5I', converted, 32), (MAGIC, 1, 4, 4, 8, 8))
                self.assertEqual(decode_entry(stream, entries[1]), b'opaque non-texture')
                self.assertEqual(converted[131], 128)
                self.assertTrue(all(abs(a - b) <= 1 for a, b in
                                    zip(converted[128:131], (128, 64, 32), strict=True)))
            self.assertEqual(source.read_bytes(), snapshot)
            self.assertEqual(process(source, staging, Path('tools/texconv.exe').resolve()), result)
            receipt = receipt_path(staging / source.name)
            # Upgrade a verified legacy repack from its old image cache, with no tool/AI rerun.
            legacy = converted[:32] + bytes(44) + converted[76:]
            replacements = root / 'legacy-textures'
            (replacements / 'model').mkdir(parents=True)
            (replacements / 'model/test.dds').write_bytes(legacy)
            pack_archive(staging / source.name, replacements, root / 'legacy.acv')
            (root / 'legacy.acv').replace(staging / source.name)
            saved = dict(result)
            del saved['renderer']
            saved['output_sha256'] = digest(staging / source.name)
            receipt.write_text(json.dumps(saved))
            cache = staging / '.texture-cache' / result['pipeline']
            cache.mkdir(parents=True)
            cached = cache / (hashlib.sha256(raw).hexdigest() + '.dds')
            cached.write_bytes(legacy)
            cached.with_suffix('.sha256').write_text(digest(cached))
            with patch('acv_tools.acv_upscale.run_tool', side_effect=AssertionError('Must reuse cached image')):
                upgraded = process(source, staging, Path('tools/texconv.exe').resolve(),
                                   root / 'must-not-run.exe', pipeline=result['pipeline'])
            self.assertEqual(upgraded['renderer'], RENDERER)
            with (staging / source.name).open('rb') as stream:
                rebuilt = decode_entry(stream, read_index(stream)[0])
                self.assertEqual(rebuilt, converted)
            self.assertEqual(cached.read_bytes(), legacy)
            receipt.unlink()
            self.assertEqual(process(source, staging, Path('tools/texconv.exe').resolve())['status'], 'verified')
            receipt.write_text('{truncated')
            self.assertEqual(process(source, staging, Path('tools/texconv.exe').resolve())['status'], 'verified')
            (staging / source.name).write_bytes(b'corrupt')
            with self.assertRaises(ValueError):
                process(source, staging, Path('tools/texconv.exe').resolve())


if __name__ == '__main__':
    unittest.main()
