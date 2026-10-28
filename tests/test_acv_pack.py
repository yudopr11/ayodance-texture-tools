import io
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from acv_tools.acv_crypto import crypt
from acv_tools.acv_pack import encode_index, pack_archive
from acv_tools.acv_unpack import (
    decode_entry,
    inflate_checked,
    process_archive,
    read_index,
)


def make_tagged_archive(variant, count=5):
    tag = (0xff000025 | variant << 16) if variant else 0
    records, payloads = bytearray(), []
    offset = 0
    for index in range(count):
        name = f"texture\\item{index}.dds" if index else "data\\info.slk"
        raw = (b"DDS " if index else b"info") + bytes(range(64)) * (index + 1)
        packed = zlib.compress(raw)
        iv = bytes(range(32)) if variant == 6 else None
        if tag:
            alignment = 32 if iv is not None else 8
            packed = crypt(packed + bytes(-len(packed) % alignment), tag, decrypt=False, iv=iv)
        records.extend(name.encode("cp949").ljust(160, b"\0"))
        records.extend(struct.pack("<4I", 0, len(packed), len(raw), offset))
        if tag:
            records.extend(struct.pack("<I", tag))
        if iv is not None:
            records.extend(iv)
        payloads.append(packed)
        offset += len(packed)
    return encode_index(bytes(records), tag, count) + b"".join(payloads)


class TaggedArchiveTests(unittest.TestCase):
    def test_original_archive_cipher_vector(self):
        # First index block of the original 328.acv; not a self-generated roundtrip.
        self.assertEqual(crypt(bytes.fromhex("a9b25551f8c01cde"), 0xff050148, header=True),
                         bytes.fromhex("789cdbf9e2d8f78d"))

    def test_decode_all_variants_and_embedded_length_positions(self):
        for variant in (0, 1, 2, 3, 5, 6):
            for count in (1, 3, 5, 13):
                with self.subTest(variant=variant, count=count):
                    stream = io.BytesIO(make_tagged_archive(variant, count))
                    entries = read_index(stream)
                    self.assertEqual(len(entries), count)
                    for index, entry in enumerate(entries):
                        raw = decode_entry(stream, entry)
                        self.assertEqual(raw, (b"DDS " if index else b"info") + bytes(range(64)) * (index + 1))

    def test_patch_and_preserve_unprovided_payloads(self):
        for variant in (0, 1, 2, 3, 5, 6):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                template, output, folder = root / "original.acv", root / "new.acv", root / "replacements"
                template.write_bytes(make_tagged_archive(variant))
                texture = folder / "texture/item2.dds"
                texture.parent.mkdir(parents=True)
                texture.write_bytes(b"DDS " + b"upscaled replacement" * 50)
                self.assertEqual(pack_archive(template, folder, output)["replaced"], 1)
                with template.open("rb") as original, output.open("rb") as rebuilt:
                    before, after = read_index(original), read_index(rebuilt)
                    for old, new in zip(before, after, strict=True):
                        if old[0].endswith("item2.dds"):
                            self.assertEqual(decode_entry(rebuilt, new), texture.read_bytes())
                        else:
                            original.seek(old[4])
                            rebuilt.seek(new[4])
                            self.assertEqual(original.read(old[2]), rebuilt.read(new[2]))
                snapshot = output.read_bytes()
                with self.assertRaises(FileExistsError):
                    pack_archive(template, folder, output)
                self.assertEqual(output.read_bytes(), snapshot)

    def test_texture_filter_and_unknown_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            template, folder = root / "original.acv", root / "textures"
            template.write_bytes(make_tagged_archive(6))
            result = process_archive(template, folder, textures_only=True)
            self.assertEqual((result["total_entries"], result["entries"]), (5, 4))
            self.assertFalse((folder / "data/info.slk").exists())
            (folder / "unknown.txt").write_bytes(b"wrong file")
            with self.assertRaises(ValueError):
                pack_archive(template, folder, root / "new.acv")
            self.assertFalse((root / "new.acv").exists())

    def test_ambiguous_template_names_and_packed_size_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            template, folder, output = root / "original.acv", root / "files", root / "new.acv"
            folder.mkdir()
            (folder / "data").mkdir()
            replacement = folder / "data/info.slk"
            replacement.write_bytes(b"hello")
            archive = bytearray(make_tagged_archive(0, 2))
            archive[180:340] = b"DATA\\INFO.SLK".ljust(160, b"\0")
            template.write_bytes(archive)
            with self.assertRaises(ValueError):
                pack_archive(template, folder, output)
            template.write_bytes(make_tagged_archive(0, 2))
            with patch("acv_tools.acv_pack.MAX_RAW", 5), self.assertRaises(ValueError):
                pack_archive(template, folder, output)
            self.assertFalse(output.exists())

    def test_native_padding_and_length_validation(self):
        packed = zlib.compress(b"hello")
        self.assertEqual(inflate_checked(packed + b"xyz", 5, alignment=8), b"hello")
        for data, size in ((packed + bytes(8), 5), (packed, 4), (packed[:-1], 5)):
            with self.subTest(data=data, size=size), self.assertRaises(ValueError):
                inflate_checked(data, size, alignment=8)
        archive = bytearray(make_tagged_archive(5))
        struct.pack_into("<I", archive, 8, 0)
        with self.assertRaises(ValueError):
            read_index(io.BytesIO(archive))


if __name__ == "__main__":
    unittest.main()
