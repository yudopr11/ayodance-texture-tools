"""Execute recovered tagged-index reader 5A6250; crypto/heap/OS are modeled.

Native envelope/PRNG/permutation, inflate and record normalization execute.
The outer whole-file mapper, global index hash table, login and UI do not.
"""

import argparse
import hashlib
import json
import struct
from pathlib import Path

from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.x86_const import UC_X86_REG_EAX, UC_X86_REG_EIP, UC_X86_REG_ESP

from acv_tools.acv_unpack import crypt, read_index
from research.emulate_archive_open import Reader
from research.emulate_native_inflate import IMAGE_SHA, LIMIT, align, image_bytes


class IndexReader(Reader):
    def __init__(self, image, tag):
        super().__init__(image, [], 0)
        self.tag = tag
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self.check_heap_write,
                         begin=0x21000000, end=0x38ffffff)
        # Avoid unsupported AVX conversion selected by the recovered host snapshot.
        self.put(0x3CF6914, 0)
        self.index_callbacks = {}
        for index, (slot, count) in enumerate(((0xD440D4, 2), (0xD440D8, 1))):
            address = 0x40001000 + index * 16
            self.put(slot, address)
            self.index_callbacks[address] = count
            self.uc.hook_add(UC_HOOK_CODE, self.callback, begin=address, end=address)

    def check_heap_write(self, uc, access, address, size, value, context):
        if not any(start <= address and address + size <= start + allocation[0]
                   for start, allocation in self.live.items()):
            raise AssertionError(f"Native index write outside live allocation: {address:08x}+{size}")

    def callback(self, uc, address, size, _):
        esp = uc.reg_read(UC_X86_REG_ESP)
        def arg(n):
            return self.get(esp + 4 * (n + 1))
        if address == 0xC39100:
            if arg(4) != self.tag or tuple(arg(i) for i in (3, 5, 6, 7)) != (0, int(self.tag < 0xff060000), int(self.tag >= 0xff060000), 0):
                raise AssertionError("Native index crypto arguments changed")
            data = bytes(uc.mem_read(arg(1), arg(2)))
            decoded = crypt(data, self.tag, header=True)
            if len(decoded) > self.live[arg(0)][0]:
                raise AssertionError("Index crypto output exceeds native allocation")
            uc.mem_write(arg(0), decoded)
            result, pop = len(decoded), 4
        elif address in self.index_callbacks:
            count = self.index_callbacks[address]
            if count == 2:
                if arg(0) != 0:
                    raise AssertionError("Unexpected native shuffle allocation flags")
                result = self.allocate(arg(1), "shuffle")
            else:
                self.release(arg(0))
                result = 1
            pop = 4 + count * 4
        else:
            return super().callback(uc, address, size, _)
        uc.reg_write(UC_X86_REG_EAX, result)
        uc.reg_write(UC_X86_REG_EIP, self.get(esp))
        uc.reg_write(UC_X86_REG_ESP, esp + pop)


def inspect(image, archive):
    with archive.open("rb") as stream:
        entries = read_index(stream)
        # Only the header/index bytes are materialized, not a whole archive mapping.
        base = entries[0][4] if entries else stream.tell()
        stream.seek(0)
        header = stream.read(base)
    tag = struct.unpack_from("<I", header)[0] if header[3] == 255 else 0
    variant = (tag >> 16) & 255
    identity = {"archive": str(archive.resolve()), "index_sha256": hashlib.sha256(header).hexdigest()}
    if variant < 2:
        return {**identity, "status": "NOT_ENCODED", "count": len(entries)}
    native = IndexReader(image, tag)
    uc = native.uc
    source, destination = 0x41000000, 0x45000000
    count = len(entries)
    if len(header) > LIMIT or count * 212 > LIMIT:
        raise ValueError("Index exceeds research size bound")
    uc.mem_map(source, align(len(header)))
    uc.mem_write(source, header)
    uc.mem_map(destination, align(max(count * 212, 1)))
    native.put(0x20003000, destination, destination, destination + count * 212)
    native.put(0x20003100, 8)
    result = native.call(0x5A6250, tag, count, source, 0x20003100,
                         0x20003000, int(archive.stem), 32 if variant == 6 else 0,
                         instruction_limit=100_000_000)
    if result & 255 != 1 or native.get(0x20003100) != base:
        raise AssertionError(f"Native index failed/cursor differs: {archive}, result={result:x}, cursor={native.get(0x20003100)}, expected={base}")
    if native.get(0x20003004) != destination + count * 212:
        raise AssertionError("Native normalized record count differs")
    for index, entry in enumerate(entries):
        row = bytes(uc.mem_read(destination + index * 212, 212))
        name = row[:160].split(b"\0", 1)[0].decode("cp949", errors="surrogateescape")
        metadata, packed, raw, offset, entry_tag = struct.unpack_from("<5I", row, 160)
        if (name, metadata, packed, raw, base + offset, entry_tag) != entry[:6]:
            raise AssertionError(f"Native normalized record mismatch: {archive}, {index}")
        if variant == 6 and row[180:212] != entry[6]:
            raise AssertionError("Native normalized IV differs")
    if native.live:
        raise AssertionError("Native index leaked a modeled allocation")
    return {**identity, "status": "PASS", "count": count,
            "normalized_sha256": hashlib.sha256(bytes(uc.mem_read(destination, count * 212))).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    image = image_bytes()
    with args.report.open("x", encoding="utf-8") as output:
        archives = [args.path] if args.path.is_file() else [args.path / f"{i:03d}.acv" for i in range(1, 331)]
        results = []
        try:
            for archive in archives:
                try:
                    row = inspect(image, archive)
                except Exception as error:
                    results.append({"archive": str(archive.resolve()), "status": "FAIL",
                                    "error": f"{type(error).__name__}: {error}"})
                    raise
                results.append(row)
                print(f"{archive.name}: {row['status']} {row['count']}", flush=True)
        finally:
            json.dump({"scope": __doc__, "image_sha256": IMAGE_SHA,
                       "input_path": str(args.path.resolve()), "rows": results}, output, indent=2)
            output.write("\n")


if __name__ == "__main__":
    main()
