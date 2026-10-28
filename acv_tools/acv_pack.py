"""Rebuild an ACV from an original template and a folder of replacement files.

Unprovided entries (including opaque decoys) are copied byte-for-byte. The output
must be a new file. Never point this at the installed game's data directory.
"""

import argparse
import shutil
import struct
import tempfile
import zlib
from collections import Counter
from pathlib import Path

from .acv_crypto import MT19937, crypt
from .acv_unpack import MAX_RAW, read_index, safe_relative_path


def encode_index(records, tag, count):
    variant = (tag >> 16) & 255
    if not tag:
        return struct.pack("<I", count) + records
    prefix = struct.pack("<2I", tag, count)
    if variant == 1:
        return prefix + records
    alignment = 32 if variant == 6 else 8
    compressed = zlib.compress(records, 9)
    # The native reader needs space for an embedded length field at up to 8*N.
    if variant in (5, 6) and count > 4 and len(compressed) < count * 8:
        compressed = zlib.compress(records, 0)
    padded = compressed + bytes(-len(compressed) % alignment)
    encrypted = bytearray(crypt(padded, tag, header=True, decrypt=False))
    if variant in (2, 3):
        if len(encrypted) > 65535:
            raise ValueError("Legacy encoded index exceeds its 16-bit length limit")
        return prefix + struct.pack("<H", len(encrypted)) + encrypted
    if variant not in (5, 6):
        raise ValueError("Unsupported output ACV variant")
    if count <= 4:
        mask = len(encrypted)
    else:
        offset = MT19937(count).between(8, count * 8 - 8)
        if offset + 4 > len(encrypted):
            raise ValueError("Encoded length field cannot fit in the index")
        mask = struct.unpack_from("<I", encrypted, offset)[0]
        struct.pack_into("<I", encrypted, offset, mask ^ len(encrypted))
    # Scrambling is optional in the native reader; keep that flag clear.
    return prefix + struct.pack("<2I", count ^ mask, mask) + encrypted


def pack_archive(template, replacements, output):
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite {output}")
    root = replacements.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Replacement path must be a directory")
    with template.open("rb") as source:
        entries = read_index(source)
        source.seek(0)
        header = source.read(4)
        tag = struct.unpack("<I", header)[0] if header[3] == 255 else 0
        names = Counter(safe_relative_path(entry[0]).as_posix().casefold() for entry in entries)
        overrides = {}
        for path in root.rglob("*"):
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError(f"Replacement links are not allowed: {path}")
            if path.is_file():
                key = safe_relative_path(path.relative_to(root).as_posix()).as_posix().casefold()
                if names.get(key, 0) != 1 or key in overrides:
                    raise ValueError(f"Unknown, ambiguous or duplicate replacement file: {path}")
                overrides[key] = path
        if not overrides:
            raise ValueError("No replacement files found")
        records = bytearray()
        offset = changed = 0
        with tempfile.TemporaryFile() as payloads:
            for entry in entries:
                name, metadata, packed_size, raw_size, source_offset, entry_tag, iv = entry
                replacement = overrides.get(safe_relative_path(name).as_posix().casefold())
                if replacement is None:
                    source.seek(source_offset)
                    remaining = packed_size
                    while remaining:
                        chunk = source.read(min(remaining, 1024 * 1024))
                        if not chunk:
                            raise ValueError("Template payload truncated during packing")
                        payloads.write(chunk)
                        remaining -= len(chunk)
                else:
                    with replacement.open("rb") as stream:
                        raw = stream.read(MAX_RAW + 1)
                    raw_size = len(raw)
                    if raw_size > MAX_RAW:
                        raise ValueError(f"Replacement exceeds safety limit: {replacement}")
                    packed = zlib.compress(raw, 9)
                    if entry_tag:
                        alignment = 32 if iv is not None else 8
                        packed = crypt(packed + bytes(-len(packed) % alignment), entry_tag, decrypt=False, iv=iv)
                    packed_size = len(packed)
                    if packed_size > MAX_RAW:
                        raise ValueError(f"Packed replacement exceeds safety limit: {replacement}")
                    payloads.write(packed)
                    changed += 1
                if offset + packed_size > 0xffffffff:
                    raise ValueError("Archive payload exceeds 32-bit offsets")
                encoded_name = name.encode("cp949", errors="surrogateescape")
                if len(encoded_name) >= 160:
                    raise ValueError("Filename does not fit the native ACV record")
                records.extend(encoded_name.ljust(160, b"\0"))
                records.extend(struct.pack("<4I", metadata, packed_size, raw_size, offset))
                if tag:
                    records.extend(struct.pack("<I", entry_tag))
                if (tag >> 16) & 255 == 6:
                    if iv is None or len(iv) != 32:
                        raise ValueError("Template is missing entry IV material")
                    records.extend(iv)
                offset += packed_size
            index = encode_index(bytes(records), tag, len(entries))
            payloads.seek(0)
            with output.open("xb") as destination:
                destination.write(index)
                shutil.copyfileobj(payloads, destination, length=1024 * 1024)
    return {"entries": len(entries), "replaced": changed, "output_bytes": output.stat().st_size}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", type=Path, help="Original ACV, read-only")
    parser.add_argument("replacements", type=Path, help="Folder preserving extracted relative paths")
    parser.add_argument("output", type=Path, help="New ACV file, outside the installed data directory")
    args = parser.parse_args()
    if args.output.resolve().parent == args.template.resolve().parent:
        parser.error("Write into a separate staging directory, not the template's data directory")
    try:
        print(pack_archive(args.template, args.replacements, args.output))
    except (OSError, ValueError, zlib.error) as error:
        parser.exit(1, f"Packing failed: {error}\nIf created, the output may be partial; never install it.\n")


if __name__ == "__main__":
    main()
