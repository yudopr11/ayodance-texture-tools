"""Drive native DDS consumer 0xca5880 with local fixtures, stopping at D3DX.

Archive/index/crypto are supplied by Python, path normalization is identity, and
allocation is bounded/modelled. Native DDS header reads, seeks, copies and upload
argument construction execute. This is not a login or graphics replay.
"""

import argparse
import hashlib
import json
import struct
from pathlib import Path

from unicorn import UC_ARCH_X86, UC_HOOK_CODE, UC_MODE_32
from unicorn.unicorn import Uc
from unicorn.x86_const import UC_X86_REG_EAX, UC_X86_REG_EIP, UC_X86_REG_ESP

from acv_tools.acv_unpack import crypt, decode_entry, read_index
from research.emulate_native_inflate import LIMIT, align, check, image_bytes


def consume(image, path, dds):
    if len(dds) < 128 or len(dds) > LIMIT or dds[:4] != b"DDS ":
        raise ValueError("Expected bounded DDS fixture")
    uc = Uc(UC_ARCH_X86, UC_MODE_32)
    uc.mem_map(0x400000, align(len(image)))
    uc.mem_write(0x400000, image)
    uc.mem_map(0, 4096)  # Synthetic FS exception-chain anchor, not a host TEB.
    stack, resource, data, heap = 0x10010000, 0x20000000, 0x30000000, 0x40000000
    for address, size in ((0x10000000, 0x20000), (resource, 0x10000),
                          (data, align(len(dds))), (heap, align(len(dds)))):
        uc.mem_map(address, size)

    def put(address, *values):
        uc.mem_write(address, struct.pack("<" + "I" * len(values), *values))

    def get(address):
        return struct.unpack("<I", uc.mem_read(address, 4))[0]

    path_ptr = resource + 0x100
    uc.mem_write(path_ptr, path.encode("cp949") + b"\x00")
    put(stack, 0x10000000, path_ptr, resource + 0x1000, resource + 0x1004)
    put(resource + 8, data, len(dds))
    uc.mem_write(resource + 0x4c, b"\x01")
    uc.mem_write(data, dds)
    put(0x3CB1EE8, 1024, 1024)  # Explicit synthetic renderer dimension limits.
    uc.reg_write(UC_X86_REG_ESP, stack)
    reads, allocations, upload = [], [], {}

    def callback(uc, address, size, _):
        esp = uc.reg_read(UC_X86_REG_ESP)
        args = [get(esp + 4 * n) for n in range(1, 16)]
        if address == 0x5ABA90:
            count = args[1] * args[2]
            if args[3] != resource or get(resource + 0x18) + count > len(dds):
                raise AssertionError("Native consumer read outside resource fixture")
            reads.append(count)
            return  # Execute actual native reader and memcpy.
        if address == 0xCBFA8A:
            device, pointer, length, width, height, mips, usage, fmt, pool = args[:9]
            if length != len(dds) or bytes(uc.mem_read(pointer, length)) != dds:
                raise AssertionError("Native DDS consumer changed/truncated upload payload")
            expected_height, expected_width = struct.unpack_from("<2I", dds, 12)
            if (width, height) != (min(expected_width, 1024), min(expected_height, 1024)):
                raise AssertionError("Unexpected native DDS upload dimensions")
            upload.update(bytes=length, requested_width=width, requested_height=height,
                          requested_mips=mips, usage=usage, format=fmt, pool=pool,
                          reads=reads, allocations=allocations,
                          sha256=hashlib.sha256(dds).hexdigest())
            uc.emu_stop()  # Do not execute the D3DX import, device or shim.
            return
        if address == 0x9D0160:
            if args[0] != path_ptr:
                raise AssertionError("Unexpected path-normalizer input")
            result = path_ptr
        elif address == 0x5AAF60:
            if args[0] != path_ptr:
                raise AssertionError("Unexpected native archive request")
            result = resource
        else:  # Native operator-new boundary, modelled rather than host allocation.
            if args[0] != len(dds) or allocations:
                raise AssertionError("Unexpected native consumer allocation")
            allocations.append(args[0])
            result = heap
        uc.reg_write(UC_X86_REG_EAX, result)
        uc.reg_write(UC_X86_REG_EIP, get(esp))
        uc.reg_write(UC_X86_REG_ESP, esp + 4)  # cdecl callers remove arguments.

    for address in (0x9D0160, 0x5AAF60, 0x4388F0, 0x5ABA90, 0xCBFA8A):
        uc.hook_add(UC_HOOK_CODE, callback, begin=address, end=address)
    uc.emu_start(0xCA5880, 0x10000000, timeout=5_000_000, count=10_000_000)
    if not upload:
        raise RuntimeError(f"Native consumer did not reach D3DX boundary: EIP={uc.reg_read(UC_X86_REG_EIP):08x}")
    return upload


def self_test(image):
    for width, height in ((1, 1), (64, 32), (2048, 1024)):
        # Header-only fixtures exercise consumer behavior, not D3DX DDS validation.
        header = bytearray(128)
        header[:4] = b"DDS "
        struct.pack_into("<I", header, 4, 124)
        struct.pack_into("<2I", header, 12, height, width)
        row = consume(image, "texture/gui/test.dds", bytes(header))
        if row["reads"] != [32, 128] or row["requested_mips"] != 0:
            raise AssertionError("Native consumer read/mip contract changed")
    print("PASS: 3 synthetic native DDS-consumer cases (no GPU)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, nargs="?")
    parser.add_argument("report", type=Path, nargs="?")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    image = image_bytes()
    if args.self_test:
        self_test(image)
        return
    if args.archive is None or args.report is None:
        parser.error("archive and NEW report required")
    with args.report.open("x", encoding="utf-8") as out, args.archive.open("rb") as stream:
        entries = [entry for entry in read_index(stream)
                   if "서버선택" in entry[0] and entry[0].lower().endswith(".dds")]
        if not entries:
            raise ValueError("No target server-selection DDS fixtures in archive")
        rows = []
        for entry in entries:
            decoded = decode_entry(stream, entry)
            stream.seek(entry[4])
            packed = stream.read(entry[2])
            if len(packed) != entry[2]:
                raise ValueError("Truncated stored payload")
            if entry[5]:
                packed = crypt(packed, entry[5], iv=entry[6])
            native = check(image, packed, decoded)
            rows.append({"name": entry[0], "native_inflate": native,
                         **consume(image, entry[0], decoded)})
        json.dump({"status": "PASS", "archive": str(args.archive),
                   "scope": "native inflate and DDS consumer, Python index/crypto and supplied resources; no auth/state replay or graphics",
                   "assets": rows}, out, indent=2, ensure_ascii=True)
        out.write("\n")
    print(f"PASS: {len(rows)} native server-selection DDS consumer cases; {args.report}")


if __name__ == "__main__":
    main()
