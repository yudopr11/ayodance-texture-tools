"""Legacy depth-one and genuine 3D DDS resource validation."""
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from acv_tools.acv_upscale import MAGIC, dds_info, preserved_image_format, process
from acv_tools.texture_manifest import capture
from tests.test_preserved_psd import legacy_preserved


def volume(width=4, height=4, depth=1, mips=3, fourcc=b'DXT5'):
    header = bytearray(128)
    header[:4] = b'DDS '
    struct.pack_into('<I', header, 4, 124)
    struct.pack_into('<6I', header, 8, 0x8a1007, height, width, 16, depth, mips)
    struct.pack_into('<8I', header, 76, 32, 4, int.from_bytes(fourcc, 'little'), 0, 0, 0, 0, 0)
    struct.pack_into('<2I', header, 108, 0x401008, 0x200000)
    block = 8 if fourcc == b'DXT1' else 16
    count = sum(((max(1, width >> level) + 3) // 4)
                * ((max(1, height >> level) + 3) // 4)
                * max(1, depth >> level) * block for level in range(mips))
    return bytes(header) + bytes(n % 256 for n in range(count))


class PreservedVolumeTests(unittest.TestCase):
    def test_only_depth_one_has_a_two_dimensional_view(self):
        for fourcc in [b'DXT1', b'DXT3', b'DXT5']:
            self.assertEqual(dds_info(volume(fourcc=fourcc))[:2], (4, 4))
            with self.assertRaisesRegex(ValueError, 'True 3D'):
                dds_info(volume(depth=3, fourcc=fourcc))

    def test_historical_volume_and_true_3d_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, staged, receipt = legacy_preserved(root, volume(depth=3), 'DDS-volume')
            self.assertEqual(capture(source, staged)['preserved_images'], receipt['preserved_images'])
            with patch('acv_tools.acv_upscale.resize_dds', side_effect=AssertionError('No 3D flattening')):
                result = process(source, root, root/'converter', pipeline='test')
            self.assertEqual(result['textures'], 0)
            self.assertEqual(result['preserved_images'], receipt['preserved_images'])

    def test_malformed_unsupported_oversized_and_tagged_volumes_fail(self):
        cases = [volume()[:-1], volume() + b'x']
        for offset, value in [(4, 123), (8, 0x21007), (12, 0), (16, 0), (24, 0),
                              (24, 10000000), (28, 32), (76, 31), (80, 0x40),
                              (84, int.from_bytes(b'DX10', 'little')), (112, 0x200200), (108, 0), (108, 0x1008), (8, 0x881007)]:
            raw = bytearray(volume())
            struct.pack_into('<I', raw, offset, value)
            cases.append(bytes(raw))
        raw = bytearray(volume())
        raw[32:40] = MAGIC
        cases.append(bytes(raw))
        for raw in cases:
            with self.subTest(header=raw[:128]), self.assertRaises(ValueError):
                preserved_image_format(raw)
        cube = bytearray(volume())
        struct.pack_into('<I', cube, 112, 0x200)
        self.assertIsNone(preserved_image_format(cube))
        with self.assertRaisesRegex(ValueError, 'requires explicit handling'):
            dds_info(cube)


if __name__ == '__main__':
    unittest.main()
