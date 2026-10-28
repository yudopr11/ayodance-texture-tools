"""Extract and verify Audition ACVs (plain and tagged formats 1, 2, 3, 5, 6)."""

import argparse
import hashlib
import json
import struct
import sys
import zlib
from pathlib import Path, PureWindowsPath

from .acv_crypto import MT19937, crypt, permutation

MAX_RAW = 64 * 1024 * 1024


class UnsupportedFormat(ValueError):
    pass


def read_index(stream):
    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(0)
    header = stream.read(4)
    if len(header) != 4:
        raise ValueError("Truncated ACV header")
    tag = struct.unpack("<I", header)[0] if header[3] == 255 else 0
    variant = header[2] if tag else 0
    if variant not in (0, 1, 2, 3, 5, 6) or (tag and (tag & 65535) >= 500):
        raise UnsupportedFormat(f"Unverified ACV tag {tag:08x}; no files extracted")
    if tag:
        raw_count = stream.read(4)
        if len(raw_count) != 4:
            raise ValueError("Truncated ACV count")
        count = struct.unpack("<I", raw_count)[0]
    else:
        count = struct.unpack("<I", header)[0]
    width = 176 if not tag else (212 if variant == 6 else 180)
    if count * width > MAX_RAW:
        raise ValueError("ACV index exceeds safety limit")
    if variant in (0, 1):
        index = stream.read(count * width)
        base = stream.tell()
    else:
        index = read_encoded_index(stream, tag, count, width, size)
        base = stream.tell()
    if len(index) != count * width or base > size:
        raise ValueError("Invalid ACV index size")
    entries = []
    expected_offset = 0
    for i in range(count):
        record = index[i * width:(i + 1) * width]
        name, separator, _ = record[:160].partition(b"\0")
        if not name or not separator:
            raise ValueError(f"Invalid filename in record {i}")
        metadata, packed_size, raw_size, offset = struct.unpack_from("<4I", record, 160)
        if offset != expected_offset or base + offset + packed_size > size:
            raise ValueError(f"Invalid payload range in record {i}")
        if max(packed_size, raw_size) > MAX_RAW:
            raise ValueError(f"Entry {i} exceeds the {MAX_RAW}-byte safety limit")
        entry_tag = struct.unpack_from("<I", record, 176)[0] if width >= 180 else 0
        iv = record[180:212] if width == 212 else None
        entries.append((name.decode("cp949", errors="surrogateescape"), metadata, packed_size, raw_size, base + offset, entry_tag, iv))
        expected_offset = offset + packed_size
    if base + expected_offset != size:
        raise ValueError("Final payload does not end at EOF")
    return entries


def read_encoded_index(stream, tag, count, width, file_size):
    variant = (tag >> 16) & 255
    first = second = third = 0
    if variant in (2, 3):
        field = stream.read(2)
        if len(field) != 2:
            raise ValueError("Truncated encoded index length")
        length = struct.unpack("<H", field)[0]
        random = None
        chunks = 0
        replacement = None
        mask = 0
    else:
        fields = stream.read(8)
        if len(fields) != 8:
            raise ValueError("Truncated encoded index envelope")
        encoded, mask = struct.unpack("<2I", fields)
        tagged_count = encoded ^ mask
        if tagged_count & 0x7fffffff != count:
            raise ValueError("Encoded index count mismatch")
        random = MT19937(count)
        chunks = random.between(10, 20) if tagged_count >> 31 else 0
        if chunks:
            first, second, third = random.between(300, 330), random.between(40, 80), random.between(5, 10)
        replacement = random.between(8, count * 8 - 8) if count > 4 else None
        start = stream.tell()
        if replacement is None:
            length = mask
        else:
            if start + replacement + 4 > file_size:
                raise ValueError("Encoded length field extends beyond EOF")
            stream.seek(start + replacement)
            field = stream.read(4)
            if len(field) != 4:
                raise ValueError("Truncated encoded length field")
            length = struct.unpack("<I", field)[0] ^ mask
            stream.seek(start)
    if not 0 < length <= MAX_RAW or stream.tell() + length > file_size:
        raise ValueError("Invalid encoded index length")
    data = bytearray(stream.read(length))
    if len(data) != length:
        raise ValueError("Truncated encoded index")
    if replacement is not None:
        if replacement + 4 > length:
            raise ValueError("Encoded length replacement outside index")
        struct.pack_into("<I", data, replacement, mask)
    if chunks:
        if length < 3360:
            chunks = 10
        order = permutation(chunks, first % chunks, second % (chunks - 1), third % (chunks - 2))
        unit = length // sum(value + 1 for value in order)
        pieces, position = [], 0
        for value in order:
            size = (value + 1) * unit
            pieces.append(data[position:position + size])
            position += size
        data = b"".join(pieces[order.index(value)] for value in range(chunks)) + data[position:]
    decoded = crypt(bytes(data), tag, header=True)
    return inflate_checked(decoded, count * width, 32 if variant == 6 else 8)


def inflate_checked(packed, raw_size, alignment=1):
    decoder = zlib.decompressobj()
    raw = decoder.decompress(packed, raw_size + 1)
    padding = decoder.unused_data
    # Native archives leave arbitrary bytes in the final cipher block's padding.
    if (len(raw) != raw_size or not decoder.eof or decoder.unconsumed_tail
            or len(padding) >= alignment):
        raise ValueError("Invalid zlib stream, padding, or unpacked size")
    return raw


def decode_entry(stream, entry):
    name, _, packed_size, raw_size, offset, tag, iv = entry
    stream.seek(offset)
    packed = stream.read(packed_size)
    if len(packed) != packed_size:
        raise ValueError(f"Truncated payload: {name}")
    if tag:
        packed = crypt(packed, tag, iv=iv)
    return inflate_checked(packed, raw_size, (32 if iv is not None else 8) if tag else 1)


def safe_relative_path(name):
    path = PureWindowsPath(name)
    parts = name.replace("\\", "/").split("/")
    if path.drive or path.root or any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"Unsafe archive path: {name!r}")
    for part in parts:
        if (part.endswith((" ", ".")) or any(ord(c) < 32 or c in '<>:"|?*' for c in part)
                or PureWindowsPath(part).is_reserved()):
            raise ValueError(f"Unsafe archive path: {name!r}")
    return Path(*parts)


def process_archive(source, output=None, textures_only=False):
    with source.open("rb") as stream:
        entries = read_index(stream)
        total_entries = len(entries)
        if textures_only:
            entries = [entry for entry in entries if entry[0].lower().endswith(".dds")]
        paths = []
        if output is not None:
            paths = [safe_relative_path(entry[0]) for entry in entries]
            keys = {p.as_posix().casefold() for p in paths}
            if len(keys) != len(paths):
                raise ValueError("Duplicate output filenames (case-insensitive)")
            for path in paths:
                if any(p.as_posix().casefold() in keys for p in path.parents if p != Path(".")):
                    raise ValueError("File/directory path collision")
            output.mkdir(parents=True, exist_ok=False)
        total = 0
        dds = 0
        digest = hashlib.sha256()
        for i, entry in enumerate(entries):
            raw = decode_entry(stream, entry)
            digest.update(struct.pack("<I", len(raw)))
            digest.update(raw)
            total += len(raw)
            dds += entry[0].lower().endswith(".dds")
            if output is not None:
                target = output / paths[i]
                target.parent.mkdir(parents=True, exist_ok=True)
                # Reject pre-existing links, including directories introduced during extraction.
                if not target.resolve().is_relative_to(output.resolve()):
                    raise ValueError(f"Output path escapes destination: {entry[0]}")
                with target.open("xb") as destination:
                    destination.write(raw)
        return {"entries": len(entries), "total_entries": total_entries, "dds_entries": dds, "raw_bytes": total,
                "decoded_stream_sha256": digest.hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="One ACV or directory of ACVs")
    parser.add_argument("--output", type=Path, help="Extract into new per-archive folders; omitted = verify only")
    parser.add_argument("--textures-only", action="store_true", help="Extract/verify only DDS textures; omit non-texture and decoy records")
    args = parser.parse_args()
    sources = sorted(args.source.glob("*.acv")) if args.source.is_dir() else [args.source]
    if not sources:
        parser.error("No ACV files found")
    results = []
    for source in sources:
        output = args.output / source.stem if args.output is not None else None
        try:
            details = process_archive(source, output, args.textures_only)
            result = {"archive": str(source), "status": "extracted" if output else "verified", **details}
        except UnsupportedFormat as error:
            result = {"archive": str(source), "status": "unsupported", "error": str(error)}
        except (OSError, ValueError, zlib.error) as error:
            result = {"archive": str(source), "status": "failed", "error": str(error)}
            if output is not None:
                result["note"] = "If created, the output folder may contain partial results; it was not removed."
        results.append(result)
    print(json.dumps(results, indent=2, ensure_ascii=True))
    return 1 if any(r["status"] in ("unsupported", "failed") for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
