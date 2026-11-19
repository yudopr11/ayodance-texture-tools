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

    def test_native_and_python_crypto_parity(self):
        from acv_tools import acv_crypto

        sample = bytes((i * 37 + 11) & 255 for i in range(512))
        iv = bytes(range(32, 64))
        for tag in (0xff010002, 0xff020003, 0xff030009, 0xff050148, 0xff060025):
            variant = (tag >> 16) & 255
            for header in (False, True):
                for decrypt in (False, True):
                    use_iv = iv if (variant == 6 and not header) else None
                    native_out = acv_crypto.crypt(sample, tag, header=header, decrypt=decrypt, iv=use_iv)
                    saved_native = acv_crypto._NATIVE
                    acv_crypto._NATIVE = None
                    acv_crypto.blowfish_cipher.cache_clear()
                    try:
                        python_out = acv_crypto.crypt(sample, tag, header=header, decrypt=decrypt, iv=use_iv)
                    finally:
                        acv_crypto._NATIVE = saved_native
                        acv_crypto.blowfish_cipher.cache_clear()
                    self.assertEqual(native_out, python_out, f"tag={tag:08x} header={header} decrypt={decrypt}")
        if acv_crypto.has_native_crypto():
            native_bf = acv_crypto.ArchiveBlowfish(bytes(range(56)), 0xff050148, use_native=True)
            python_bf = acv_crypto.ArchiveBlowfish(bytes(range(56)), 0xff050148, use_native=False)
            self.assertEqual(native_bf.p, python_bf.p)
            self.assertEqual(native_bf.s, python_bf.s)

    def test_parallel_unpack_and_pack_with_compress_levels(self):
        for variant in (0, 3, 5, 6):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                template = root / "original.acv"
                template.write_bytes(make_tagged_archive(variant, count=20))
                seq = process_archive(template, workers=1)
                par = process_archive(template, workers=4)
                self.assertEqual(seq, par)

                folder = root / "replacements"
                for idx in (1, 4, 7, 10, 13, 16, 19):
                    target = folder / f"texture/item{idx}.dds"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b"DDS " + f"replacement-{idx}-".encode() * 40)

                out_par = root / "rebuilt_par.acv"
                out_lvl1 = root / "rebuilt_lvl1.acv"
                self.assertEqual(pack_archive(template, folder, out_par, compress_level=6, workers=4)["replaced"], 7)
                self.assertEqual(pack_archive(template, folder, out_lvl1, compress_level=1, workers=2)["replaced"], 7)

                for rebuilt_path in (out_par, out_lvl1):
                    with template.open("rb") as original, rebuilt_path.open("rb") as rebuilt:
                        before, after = read_index(original), read_index(rebuilt)
                        self.assertEqual(len(before), len(after))
                        for old, new in zip(before, after, strict=True):
                            candidate = folder / old[0].replace("\\", "/")
                            if candidate.is_file():
                                self.assertEqual(decode_entry(rebuilt, new), candidate.read_bytes())
                            else:
                                original.seek(old[4])
                                rebuilt.seek(new[4])
                                self.assertEqual(original.read(old[2]), rebuilt.read(new[2]))


if __name__ == "__main__":
    unittest.main()
