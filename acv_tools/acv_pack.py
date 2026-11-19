"""Rebuild an ACV from an original template and a folder of replacement files.

Unprovided entries (including opaque decoys) are copied byte-for-byte. The output
must be a new file. Never point this at the installed game's data directory.
"""

import argparse
import os
import struct
import tempfile
import zlib
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .acv_crypto import MT19937, crypt
from .acv_unpack import MAX_RAW, read_index, safe_relative_path

_IO_BUFFER = 1024 * 1024
_SPOOL_LIMIT = 64 * 1024 * 1024
_DEFAULT_COMPRESS_LEVEL = 6


def _default_workers(replacement_count):
    if replacement_count < 4:
        return 1
    return min(8, os.cpu_count() or 1)


def _pack_replacement(replacement, entry_tag, iv, compress_level, max_raw):
    with replacement.open("rb") as stream:
        file_size = os.fstat(stream.fileno()).st_size
        if file_size > max_raw:
            raise ValueError(f"Replacement exceeds safety limit: {replacement}")
        raw = stream.read(file_size + 1)
        if len(raw) > file_size:
            chunks = [raw]
            total = len(raw)
            while total <= max_raw:
                chunk = stream.read(min(_IO_BUFFER, max_raw + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            raw = b"".join(chunks)
    raw_size = len(raw)
    if raw_size > max_raw:
        raise ValueError(f"Replacement exceeds safety limit: {replacement}")
    packed = zlib.compress(raw, compress_level)
    if entry_tag:
        alignment = 32 if iv is not None else 8
        packed = crypt(packed + bytes(-len(packed) % alignment), entry_tag, decrypt=False, iv=iv)
    packed_size = len(packed)
    if packed_size > max_raw:
        raise ValueError(f"Packed replacement exceeds safety limit: {replacement}")
    return raw_size, packed


def _copy_exact(source, destination, length, error_message):
    remaining = length
    while remaining:
        chunk = source.read(min(remaining, _IO_BUFFER))
        if not chunk:
            raise ValueError(error_message)
        destination.write(chunk)
        remaining -= len(chunk)


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


def pack_archive(template, replacements, output, *, compress_level=_DEFAULT_COMPRESS_LEVEL, workers=None):
    if not 0 <= compress_level <= 9:
        raise ValueError("Compression level must be between 0 and 9")
    if workers is not None and workers < 1:
        raise ValueError("Worker count must be positive")
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite {output}")
    root = replacements.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Replacement path must be a directory")
    max_raw = MAX_RAW
    with template.open("rb", buffering=_IO_BUFFER) as source:
        entries = read_index(source)
        source.seek(0)
        header = source.read(4)
        tag = struct.unpack("<I", header)[0] if header[3] == 255 else 0
        variant = (tag >> 16) & 255 if tag else 0
        entry_keys = [safe_relative_path(entry[0]).as_posix().casefold() for entry in entries]
        names = Counter(entry_keys)
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

        # Prepare replacement work items in archive order.
        replacement_items = []
        for idx, (entry, key) in enumerate(zip(entries, entry_keys, strict=True)):
            replacement = overrides.get(key)
            if replacement is not None:
                replacement_items.append((idx, replacement, entry[5], entry[6]))

        records = bytearray()
        pack_4i = struct.Struct("<4I").pack
        pack_1i = struct.Struct("<I").pack
        offset = changed = 0
        # Write plan entries: ("source", src_offset, length) or ("spool", length)
        plan = []

        def append_plan_source(src_offset, length):
            if length == 0:
                return
            if plan and plan[-1][0] == "source" and plan[-1][1] + plan[-1][2] == src_offset:
                plan[-1] = ("source", plan[-1][1], plan[-1][2] + length)
            else:
                plan.append(("source", src_offset, length))

        def append_plan_spool(length):
            if length == 0:
                return
            if plan and plan[-1][0] == "spool":
                plan[-1] = ("spool", plan[-1][1] + length)
            else:
                plan.append(("spool", length))

        def append_record(name, metadata, packed_size, raw_size, entry_tag, iv):
            nonlocal offset
            if offset + packed_size > 0xffffffff:
                raise ValueError("Archive payload exceeds 32-bit offsets")
            encoded_name = name.encode("cp949", errors="surrogateescape")
            if len(encoded_name) >= 160:
                raise ValueError("Filename does not fit the native ACV record")
            records.extend(encoded_name.ljust(160, b"\0"))
            records.extend(pack_4i(metadata, packed_size, raw_size, offset))
            if tag:
                records.extend(pack_1i(entry_tag))
            if variant == 6:
                if iv is None or len(iv) != 32:
                    raise ValueError("Template is missing entry IV material")
                records.extend(iv)
            offset += packed_size

        spool_dir = output.parent if output.parent.is_dir() else None
        thread_count = _default_workers(len(replacement_items)) if workers is None else min(workers, max(1, len(replacement_items)))
        with tempfile.SpooledTemporaryFile(max_size=_SPOOL_LIMIT, dir=spool_dir) as replaced_spool:
            if thread_count <= 1:
                for entry, key in zip(entries, entry_keys, strict=True):
                    name, metadata, packed_size, raw_size, source_offset, entry_tag, iv = entry
                    replacement = overrides.get(key)
                    if replacement is None:
                        append_plan_source(source_offset, packed_size)
                    else:
                        raw_size, packed = _pack_replacement(replacement, entry_tag, iv, compress_level, max_raw)
                        packed_size = len(packed)
                        replaced_spool.write(packed)
                        append_plan_spool(packed_size)
                        changed += 1
                    append_record(name, metadata, packed_size, raw_size, entry_tag, iv)
            else:
                max_in_flight = thread_count * 2
                pending = deque()
                next_submit = 0
                total_replacements = len(replacement_items)
                with ThreadPoolExecutor(max_workers=thread_count) as pool:
                    try:
                        def fill_queue():
                            nonlocal next_submit
                            while next_submit < total_replacements and len(pending) < max_in_flight:
                                r_idx, r_path, r_tag, r_iv = replacement_items[next_submit]
                                next_submit += 1
                                future = pool.submit(_pack_replacement, r_path, r_tag, r_iv, compress_level, max_raw)
                                pending.append((r_idx, future))

                        fill_queue()
                        for idx, entry in enumerate(entries):
                            name, metadata, packed_size, raw_size, source_offset, entry_tag, iv = entry
                            if pending and pending[0][0] == idx:
                                _, future = pending.popleft()
                                fill_queue()
                                raw_size, packed = future.result()
                                packed_size = len(packed)
                                replaced_spool.write(packed)
                                append_plan_spool(packed_size)
                                changed += 1
                            else:
                                append_plan_source(source_offset, packed_size)
                            append_record(name, metadata, packed_size, raw_size, entry_tag, iv)
                    except BaseException:
                        for _, future in pending:
                            future.cancel()
                        raise

            index = encode_index(bytes(records), tag, len(entries))
            replaced_spool.seek(0)
            with output.open("xb", buffering=_IO_BUFFER) as destination:
                destination.write(index)
                for step in plan:
                    if step[0] == "source":
                        _, src_offset, length = step
                        if source.tell() != src_offset:
                            source.seek(src_offset)
                        _copy_exact(source, destination, length, "Template payload truncated during packing")
                    else:
                        _, length = step
                        _copy_exact(replaced_spool, destination, length, "Spool payload truncated during packing")
    return {"entries": len(entries), "replaced": changed, "output_bytes": output.stat().st_size}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", type=Path, help="Original ACV, read-only")
    parser.add_argument("replacements", type=Path, help="Folder preserving extracted relative paths")
    parser.add_argument("output", type=Path, help="New ACV file, outside the installed data directory")
    parser.add_argument("--compress-level", type=int, default=_DEFAULT_COMPRESS_LEVEL, choices=range(10),
                        help=f"zlib compression level 0..9 for replacement payloads (default: {_DEFAULT_COMPRESS_LEVEL})")
    parser.add_argument("--workers", type=int, default=None, help="Worker threads for replacement packing (default: auto)")
    args = parser.parse_args()
    if args.workers is not None and args.workers < 1:
        parser.error("Worker count must be positive")
    if args.output.resolve().parent == args.template.resolve().parent:
        parser.error("Write into a separate staging directory, not the template's data directory")
    try:
        print(pack_archive(args.template, args.replacements, args.output,
                           compress_level=args.compress_level, workers=args.workers))
    except (OSError, ValueError, zlib.error) as error:
        parser.exit(1, f"Packing failed: {error}\nIf created, the output may be partial; never install it.\n")


if __name__ == "__main__":
    main()
