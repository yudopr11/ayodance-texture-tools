"""Run recovered v64 zlib code in isolated x86 emulation, never the host client.

The heap callbacks are modeled; this does not reproduce game VA fragmentation,
archive lookup/crypto, login state, or Direct3D. Supply the retained research
unicorn dependency via PYTHONPATH if it is not installed in the local .venv.
"""

import argparse
import hashlib
import json
import struct
import zlib
from pathlib import Path

from unicorn import (
    UC_ARCH_X86,
    UC_HOOK_CODE,
    UC_MODE_32,
    UC_PROT_READ,
    UC_PROT_WRITE,
    Uc,
)
from unicorn.x86_const import UC_X86_REG_EAX, UC_X86_REG_EIP, UC_X86_REG_ESP

from acv_tools.acv_unpack import crypt, decode_entry, read_index

ROOT = Path(__file__).resolve().parents[1]
IMAGE_SHA = "47d0e6b9bcaa3008fa91bd86dd8d3b290aa8ee55bf3391d9cba0a6f43246a73d"
LIMIT = 64 * 1024 * 1024


def image_bytes():
    image = (ROOT / "diagnostics/checkpoint-v64/image.bin").read_bytes()
    if len(image) != 66727936 or hashlib.sha256(image).hexdigest() != IMAGE_SHA:
        raise ValueError("Expected verified recovered v64 image")
    return image


def align(size):
    return (size + 4095) & ~4095


def native_inflate(image, packed, capacity):
    if not 0 <= capacity <= LIMIT or not 0 < len(packed) <= LIMIT:
        raise ValueError("Payload outside 64 MiB research bound")
    uc = Uc(UC_ARCH_X86, UC_MODE_32)
    uc.mem_map(0x400000, align(len(image)))
    uc.mem_write(0x400000, image)
    stack, source, destination, heap = 0x10010000, 0x20000000, 0x30000000, 0x40000000
    for address, size in [(0x10000000, 0x20000), (source, align(len(packed))),
                          (destination, align(capacity + 32)), (heap, 0x100000)]:
        uc.mem_map(address, size, UC_PROT_READ | UC_PROT_WRITE)
    uc.mem_write(source, packed)
    uc.mem_write(destination, b"\xa5" * (capacity + 32))
    length_ptr = stack + 64
    uc.mem_write(length_ptr, struct.pack("<I", capacity))
    uc.mem_write(stack, struct.pack("<5I", 0x10000000, destination, length_ptr, source, len(packed)))
    uc.reg_write(UC_X86_REG_ESP, stack)
    next_heap, allocations, frees = heap, {}, []

    def u32(address):
        return struct.unpack("<I", uc.mem_read(address, 4))[0]

    def callback(uc, address, size, _):
        nonlocal next_heap
        esp = uc.reg_read(UC_X86_REG_ESP)
        if address == 0x404070:  # zalloc(opaque, items, size), cdecl
            count = u32(esp + 8) * u32(esp + 12)
            if count > 0x100000 or next_heap + align(max(count, 1)) > heap + 0x100000:
                raise ValueError("Modeled zlib heap bound exceeded")
            result = next_heap
            next_heap += align(max(count, 1))
            allocations[result] = count
        else:  # zfree(opaque, allocation), cdecl
            pointer = u32(esp + 8)
            if pointer not in allocations or pointer in frees:
                raise ValueError("Unexpected native zlib free")
            frees.append(pointer)
            result = 0
        uc.reg_write(UC_X86_REG_EAX, result)
        uc.reg_write(UC_X86_REG_EIP, u32(esp))
        uc.reg_write(UC_X86_REG_ESP, esp + 4)

    for address in (0x404070, 0x404090):
        uc.hook_add(UC_HOOK_CODE, callback, begin=address, end=address)
    uc.emu_start(0x401020, 0x10000000, timeout=15_000_000, count=100_000_000)
    if uc.reg_read(UC_X86_REG_EIP) != 0x10000000:
        raise RuntimeError("Native inflate instruction/time budget exceeded")
    result = uc.reg_read(UC_X86_REG_EAX)
    length = u32(length_ptr)
    if length > capacity or bytes(uc.mem_read(destination + capacity, 32)) != b"\xa5" * 32:
        raise AssertionError("Native inflate exceeded destination bound")
    if set(frees) != set(allocations):
        raise AssertionError("Native inflate did not release modeled zlib allocations")
    return result, bytes(uc.mem_read(destination, length)), sum(allocations.values())


def check(image, packed, expected):
    result, actual, heap_bytes = native_inflate(image, packed, len(expected))
    if result != 0 or actual != expected:
        raise AssertionError(f"Native inflate mismatch: result=0x{result:08x}, bytes={len(actual)}")
    return {"packed_bytes": len(packed), "decoded_bytes": len(actual),
            "sha256": hashlib.sha256(actual).hexdigest(), "modeled_heap_bytes": heap_bytes}


def self_test(image):
    for data in (b"", b"a", b"DDS " + bytes(range(256)) * 4096):
        for level in (0, 9):
            check(image, zlib.compress(data, level), data)
    result, _, _ = native_inflate(image, zlib.compress(b"abcdef"), 2)
    if result == 0:
        raise AssertionError("Native decoder accepted undersized output")
    result, _, _ = native_inflate(image, b"not a zlib stream", 32)
    if result == 0:
        raise AssertionError("Native decoder accepted corrupt input")
    return {"status": "PASS", "cases": 8}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, nargs="?")
    parser.add_argument("report", type=Path, nargs="?")
    parser.add_argument("--name", help="Exact internal resource path; otherwise test up to three DDS entries")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    image = image_bytes()
    if args.self_test:
        print(json.dumps(self_test(image)))
        return
    if args.archive is None or args.report is None:
        parser.error("archive and NEW report are required")
    # Reserve the report before expensive interpretation; no existing evidence is replaced.
    with args.report.open("x", encoding="utf-8") as out, args.archive.open("rb") as stream:
        entries = read_index(stream)
        selected = [e for e in entries if e[0] == args.name] if args.name else [
            e for e in entries if e[0].lower().endswith(".dds")][:3]
        if not selected:
            raise ValueError("No matching resources")
        rows = []
        for entry in selected:
            expected = decode_entry(stream, entry)
            stream.seek(entry[4])
            packed = stream.read(entry[2])
            if len(packed) != entry[2]:
                raise ValueError("Truncated stored payload")
            if entry[5]:
                packed = crypt(packed, entry[5], iv=entry[6])
            rows.append({"name": entry[0], **check(image, packed, expected)})
        json.dump({"status": "PASS", "image_sha256": IMAGE_SHA, "archive": str(args.archive),
                   "scope": "native inflate only; crypto and index are Python, heap modeled; no login replay",
                   "entries": rows}, out, indent=2, ensure_ascii=True)
        out.write("\n")
    print(f"PASS: {len(rows)} native decoded resource(s); {args.report}")


if __name__ == "__main__":
    main()
