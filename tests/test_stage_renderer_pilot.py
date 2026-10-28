import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from acv_tools.acv_pack import pack_archive
from acv_tools.acv_unpack import decode_entry, read_index
from acv_tools.acv_upscale import MAGIC, RENDERER, digest, tag_dds
from acv_tools.stage_renderer_pilot import stage


def dds(width, height):
    raw = bytearray(128 + width * height * 4)
    raw[:4] = b'DDS '
    struct.pack_into('<I', raw, 4, 124)
    struct.pack_into('<2I', raw, 12, height, width)
    struct.pack_into('<8I', raw, 76, 32, 0x41, 0, 32, 0xff0000, 0xff00, 0xff, 0xff000000)
    return bytes(raw)


class RendererPilotTests(unittest.TestCase):
    def test_tag_preserves_header_dimensions_format_and_pixels(self):
        original, enhanced = dds(2, 1), dds(4, 2)
        marked = tag_dds(original, enhanced)
        assert marked is not None
        self.assertEqual(marked[:32], enhanced[:32])
        self.assertEqual(marked[76:], enhanced[76:])
        self.assertEqual(struct.unpack_from('<8s5I', marked, 32), (MAGIC, 1, 2, 1, 4, 2))

    def test_no_tag_for_unchanged_size(self):
        self.assertIsNone(tag_dds(dds(2, 1), dds(2, 1)))

    def test_rejects_downsizing_and_bad_headers(self):
        for enhanced in (dds(1, 1), b'broken'):
            with self.assertRaises(ValueError):
                tag_dds(dds(2, 1), enhanced)

    def test_rejects_retagging_or_existing_reserved_data(self):
        original, enhanced = dds(2, 1), dds(4, 2)
        with self.assertRaises(ValueError):
            tag_dds(original, tag_dds(original, enhanced))
        modified = bytearray(enhanced)
        modified[75] = 1
        with self.assertRaises(ValueError):
            tag_dds(original, bytes(modified))

    def test_rejects_already_tagged_source(self):
        marked = tag_dds(dds(2, 1), dds(4, 2))
        with self.assertRaisesRegex(ValueError, 'use the original archive'):
            tag_dds(marked, dds(8, 4))

    def test_rejects_unsupported_format_and_invalid_format_header(self):
        original, enhanced = bytearray(dds(2, 1)), bytearray(dds(4, 2))
        for raw in (original, enhanced):
            struct.pack_into('<I4s', raw, 80, 4, b'FAKE')
        with self.assertRaisesRegex(ValueError, 'Unsupported DDS FourCC'):
            tag_dds(original, enhanced)
        enhanced = bytearray(dds(4, 2))
        struct.pack_into('<I', enhanced, 76, 0)
        with self.assertRaisesRegex(ValueError, 'pixel-format header size'):
            tag_dds(dds(2, 1), enhanced)

    def test_stage_verifies_receipt_and_preserves_other_payloads(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original, enhanced = root / 'original/009.acv', root / 'enhanced/009.acv'
            original.parent.mkdir()
            enhanced.parent.mkdir()
            native, enlarged = dds(2, 1), dds(4, 2)
            records, payloads, offset = bytearray(), [], 0
            for name, raw in ((b'GUI/hud/spectator.dds', native), (b'other.bin', b'opaque')):
                payload = zlib.compress(raw)
                records += name.ljust(160, b'\0') + struct.pack('<4I', 7, len(payload), len(raw), offset)
                payloads.append(payload)
                offset += len(payload)
            original.write_bytes(struct.pack('<I', 2) + records + b''.join(payloads))
            replacements = root / 'replacements'
            texture = replacements / 'GUI/hud/spectator.dds'
            texture.parent.mkdir(parents=True)
            texture.write_bytes(enlarged)
            pack_archive(original, replacements, enhanced)
            receipt = {'status': 'verified', 'source_sha256': digest(original),
                       'output_sha256': digest(enhanced)}
            enhanced.with_suffix('.acv.json').write_text(json.dumps(receipt))
            output = root / 'pilot/009.acv'
            result = stage(original, enhanced, output)
            self.assertEqual(result['renderer'], RENDERER)
            self.assertEqual(result['tagged_textures'], 1)
            self.assertEqual(result['output_sha256'], digest(output))
            self.assertEqual(json.loads(output.with_suffix('.acv.json').read_text()), result)
            self.assertEqual(digest(original), receipt['source_sha256'])
            self.assertEqual(digest(enhanced), receipt['output_sha256'])
            with output.open('rb') as stream:
                entries = read_index(stream)
                self.assertEqual(decode_entry(stream, entries[0]), tag_dds(native, enlarged))
                self.assertEqual(decode_entry(stream, entries[1]), b'opaque')
            with self.assertRaises(FileExistsError):
                stage(original, enhanced, output)
            receipt['source_sha256'] = 'incorrect'
            enhanced.with_suffix('.acv.json').write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, 'hashes do not match'):
                stage(original, enhanced, root / 'rejected/009.acv')
            self.assertFalse((root / 'rejected/009.acv').exists())

    def test_rejects_changed_pixel_format(self):
        enhanced = bytearray(dds(4, 2))
        struct.pack_into('<I', enhanced, 104, 0)
        with self.assertRaises(ValueError):
            tag_dds(dds(2, 1), bytes(enhanced))


if __name__ == '__main__':
    unittest.main()
