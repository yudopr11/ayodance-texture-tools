import io
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from acv_tools.acv_unpack import (
    UnsupportedFormat,
    decode_entry,
    process_archive,
    read_index,
    safe_relative_path,
)


def make_archive(items):
    records, payloads = [], []
    offset = 0
    for name, raw in items:
        packed = zlib.compress(raw)
        records.append(name.encode("cp949").ljust(160, b"\0") +
                       struct.pack("<4I", 0, len(packed), len(raw), offset))
        payloads.append(packed)
        offset += len(packed)
    return struct.pack("<I", len(items)) + b"".join(records + payloads)


class UnpackTests(unittest.TestCase):
    def test_korean_name_and_decode(self):
        stream = io.BytesIO(make_archive([("abm\\음악.slk", b"example")]))
        entries = read_index(stream)
        self.assertEqual(entries[0][0], "abm\\음악.slk")
        self.assertEqual(decode_entry(stream, entries[0]), b"example")

    def test_extract_and_refuse_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "001.acv"
            source.write_bytes(make_archive([("data\\texture.dds", b"DDS example")]))
            output = Path(folder) / "out"
            self.assertEqual(process_archive(source)["entries"], 1)
            self.assertFalse(output.exists())
            self.assertEqual(process_archive(source, output)["dds_entries"], 1)
            self.assertEqual((output / "data/texture.dds").read_bytes(), b"DDS example")
            with self.assertRaises(FileExistsError):
                process_archive(source, output)

    def test_unsupported_variants(self):
        for variant in (4, 7, 255):
            with self.subTest(variant=variant), self.assertRaises(UnsupportedFormat):
                read_index(io.BytesIO(bytes([1, 0, variant, 255]) + bytes(32)))

    def test_truncated_and_bad_bounds(self):
        archive = make_archive([("a.dds", b"payload")])
        for data in (b"", archive[:10], archive[:-1], archive + b"extra"):
            with self.subTest(data=data), self.assertRaises(ValueError):
                read_index(io.BytesIO(data))

    def test_corrupt_zlib(self):
        archive = make_archive([("a.dds", b"payload")])
        stream = io.BytesIO(archive[:-1] + bytes([archive[-1] ^ 255]))
        entry = read_index(stream)[0]
        with self.assertRaises((ValueError, zlib.error)):
            decode_entry(stream, entry)

    def test_unsafe_paths(self):
        for name in ("../x", "a\\..\\x", "C:\\x", "C:x", "\\\\host\\x", "/x",
                     "CON.dds", "a:x", "a/", "a//b", "a/./b", "a. /x", "a\x00b"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                safe_relative_path(name)
        self.assertEqual(safe_relative_path("model\\avatar\\shirt.dds"), Path("model/avatar/shirt.dds"))

    def test_collision_preflight(self):
        for items in ([('a.dds', b'a'), ('A.dds', b'b')], [('a', b'a'), ('a/b', b'b')]):
            with tempfile.TemporaryDirectory() as folder:
                source, output = Path(folder) / "001.acv", Path(folder) / "out"
                source.write_bytes(make_archive(items))
                with self.assertRaises(ValueError):
                    process_archive(source, output)
                self.assertFalse(output.exists())

    def test_declared_size_mismatch(self):
        archive = bytearray(make_archive([("a.dds", b"payload")]))
        struct.pack_into("<I", archive, 4 + 168, 1)
        stream = io.BytesIO(archive)
        entry = read_index(stream)[0]
        with self.assertRaises(ValueError):
            decode_entry(stream, entry)


if __name__ == "__main__":
    unittest.main()
