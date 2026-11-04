"""Offline native 5AAF60 open/release with a bounded, modeled Win32 environment.

Executes native cache search/insert/eviction, map-failure fallback and inflate.
Index lookup, crypto, CRT allocation and Win32 handles/VA are modeled. Sparse
views contain only the selected stored payload; their full file sizes count
against the synthetic VA budget. This is not a Windows fragmentation simulator,
login replay, native index/crypto test or proof of a particular CTD's cause.
"""

import argparse
import hashlib
import json
import re
import struct
import zlib
from pathlib import Path

from unicorn import UC_ARCH_X86, UC_HOOK_CODE, UC_MODE_32, Uc
from unicorn.x86_const import UC_X86_REG_EAX, UC_X86_REG_EIP, UC_X86_REG_ESP

from acv_tools.acv_unpack import crypt, decode_entry, read_index
from research.emulate_native_inflate import IMAGE_SHA, LIMIT, align, image_bytes
from research.emulate_prelogin_archives import FORMAT
from research.test_archive_cache_patch import START, patch_bytes


class AllocationFailure(RuntimeError):
    """Stop at throwing operator-new boundary; do not pretend it returned null."""


class Reader:
    def __init__(self, image, fixtures, budget, patched=False, fail_allocation=None):
        self.uc = uc = Uc(UC_ARCH_X86, UC_MODE_32)
        uc.mem_map(0x400000, align(len(image)))
        uc.mem_write(0x400000, image)
        before = patch_bytes("archive_cache_before")
        if bytes(uc.mem_read(START, len(before))) != before:
            raise ValueError("Archive cache patch identity mismatch")
        if patched:
            uc.mem_write(START, patch_bytes("archive_cache_after"))
        for base, size in ((0, 4096), (0x10000000, 0x20000),
                           (0x20000000, 0x10000), (0x40000000, 0x10000)):
            uc.mem_map(base, size)
        self.fixtures = {row["number"]: row for row in fixtures}
        self.by_name = {row["name"].lower(): row for row in fixtures}
        self.budget = budget
        self.fail_allocation = fail_allocation
        self.heap, self.live, self.free_pages = 0x21000000, {}, []
        self.handles, self.views, self.current = {}, {}, None
        self.serial, self.clock = 0x1000, 0
        self.events, self.results = [], []
        self.peak, self.peak_untracked = 0, 0
        self.put(0x3C2A060, 0x20000000, 0)
        self.put(0x20000000, 0x20000000, 0x20000000, 0x20000000, 0x101)
        for base in (0x3C2A080, 0x3C2A850, 0x3C2B020):
            uc.mem_write(base, b"\0" * 2000)
        # Synthetic import targets, never host process handles or OS operations.
        apis = {0xD440FC: ("create_file", 7), 0xD44100: ("file_size", 2),
                0xD44154: ("create_mapping", 6), 0xD4414C: ("map", 5),
                0xD44150: ("unmap", 1), 0xD44108: ("close", 1),
                0xD44144: ("last_error", 0), 0xD44140: ("seek", 4),
                0xD4413C: ("read", 5)}
        self.apis = {}
        for index, (slot, api) in enumerate(apis.items()):
            address = 0x40000000 + index * 16
            self.put(slot, address)
            self.apis[address] = api
        boundaries = (0x5AADB0, 0x5A6130, 0x410F60, 0xCBFB38, 0x4388F0,
                      0xCBFB76, 0xCBFB68, 0xCBFB7B, 0xCD19CC, 0x404070,
                      0x404090, 0x5AA1A0, 0x5A7720, 0xC39100, 0x40C2B0)
        for address in (*boundaries, *self.apis):
            uc.hook_add(UC_HOOK_CODE, self.callback, begin=address, end=address)

    def put(self, address, *values):
        self.uc.mem_write(address, struct.pack("<" + "I" * len(values), *values))

    def get(self, address):
        return struct.unpack("<I", self.uc.mem_read(address, 4))[0]

    def string(self, address):
        return bytes(self.uc.mem_read(address, 256)).split(b"\0", 1)[0].decode("cp949")

    def allocate(self, size, kind):
        if size > LIMIT:
            raise ValueError("Modeled allocation exceeds 64 MiB bound")
        if self.fail_allocation == kind:
            self.events.append({"event": "allocation_failure", "kind": kind, "bytes": size})
            raise AllocationFailure(f"Modeled throwing allocation: {kind}, {size} bytes")
        span = align(max(size, 1))
        index = next((i for i, (_, count) in enumerate(self.free_pages) if count == span), None)
        if index is None:
            address = self.heap
            self.heap += span
            if self.heap > 0x39000000:
                raise ValueError("Modeled heap VA bound exceeded")
            self.uc.mem_map(address, span)
        else:
            address, _ = self.free_pages.pop(index)
        self.uc.mem_write(address, b"\xa5" * span)
        self.live[address] = (size, span, kind)
        return address

    def release(self, pointer):
        if pointer == 0:
            return
        if pointer not in self.live:
            raise AssertionError(f"Double/unknown modeled free: {pointer:x}")
        _, span, _ = self.live.pop(pointer)
        self.uc.mem_write(pointer, b"\xdd" * span)
        self.free_pages.append((pointer, span))

    def map_view(self, number):
        row = self.fixtures[number]
        span = align(row["file_bytes"])
        resident = sum(v[1] for v in self.views.values())
        if resident + span > self.budget:
            self.events.append({"event": "map_failed", "archive": number, "bytes": span})
            return 0
        address = 0x50000000
        for base, (_, size) in sorted(self.views.items()):
            if address + span <= base:
                break
            address = base + size
        if address + span > 0xF0000000:
            raise ValueError("Synthetic mapping address window exhausted")
        self.uc.mem_map(address, span)
        self.uc.mem_write(address + row["offset"], row["stored"])
        self.views[address] = (number, span)
        self.peak = max(self.peak, resident + span)
        return address

    def api(self, name, args):
        if name == "create_file":
            match = re.search(r"(\d+)\.acv$", self.string(args[0]), re.I)
            if not match or int(match[1]) not in self.fixtures:
                raise AssertionError("Unexpected native archive filename")
            number = int(match[1])
            self.serial += 1
            self.handles[self.serial] = ("file", number)
            return self.serial
        if name == "last_error":
            return 8  # Synthetic ERROR_NOT_ENOUGH_MEMORY, not an observed OS error.
        if name == "unmap":
            number, span = self.views.pop(args[0])
            self.uc.mem_unmap(args[0], span)
            self.events.append({"event": "unmap", "archive": number})
            return 1
        if args[0] not in self.handles:
            raise AssertionError(f"Invalid modeled handle in {name}: {args[0]:x}")
        kind, number = self.handles[args[0]]
        row = self.fixtures[number]
        if name == "file_size":
            return row["file_bytes"]
        if name == "create_mapping":
            self.serial += 1
            self.handles[self.serial] = ("mapping", number)
            return self.serial
        if name == "map":
            if kind != "mapping" or args[1:] != [4, 0, 0, row["file_bytes"]]:
                raise AssertionError("Unexpected native whole-file MapViewOfFile request")
            return self.map_view(number)
        if name == "close":
            del self.handles[args[0]]
            return 1
        if name == "seek":
            if args[1:] != [row["offset"], 0, 0]:
                raise AssertionError("Unexpected native fallback seek")
            self.events.append({"event": "fallback_seek", "archive": number})
            return row["offset"]
        if name == "read":
            if args[2] != len(row["decoded"]) or args[4] != 0:
                raise AssertionError("Unexpected native fallback read size")
            # The actual client requests RAW bytes here, not stored bytes.
            self.uc.mem_write(args[1], row["fallback"])
            self.put(args[3], len(row["fallback"]))
            self.events.append({"event": "fallback_read", "archive": number, "bytes": args[2]})
            return 1
        raise AssertionError(f"Unknown modeled API: {name}")

    def callback(self, uc, address, size, _):
        esp = uc.reg_read(UC_X86_REG_ESP)
        def arg(n):
            return self.get(esp + 4 * (n + 1))
        pop, result = 4, 0
        if address in self.apis:
            name, count = self.apis[address]
            result = self.api(name, [arg(i) for i in range(count)])
            pop += count * 4
        elif address in (0x5AADB0, 0x5A6130):
            row = self.by_name.get(self.string(arg(0)))
            if row is None:
                raise AssertionError("Unexpected native resource lookup")
            result = 1 if address == 0x5AADB0 else 0x20001000
            if address == 0x5A6130:
                self.uc.mem_write(result, b"\0" * 72)
                self.put(result + 8, len(row["decoded"]), len(row["stored"]), row["offset"],
                         0, row["tag"], row["offset"], row["number"])
                if row["iv"]:
                    self.uc.mem_write(result + 0x24, row["iv"])
                    self.put(result + 0x44, 1)
            pop = 8  # thiscall RET 4
        elif address == 0x410F60:
            value = f"data/{arg(2):03d}.acv".encode()
            self.uc.mem_write(arg(0), value + b"\0")
            result = len(value)
        elif address in (0xCBFB38, 0x4388F0, 0x404070):
            count = arg(1) * arg(2) if address == 0x404070 else arg(0)
            kind = "zlib" if address == 0x404070 else "object" if address == 0xCBFB38 else "payload"
            result = self.allocate(count, kind)
        elif address in (0xCBFB76, 0xCBFB68, 0x404090):
            self.release(arg(1) if address == 0x404090 else arg(0))
        elif address == 0xCD19CC:
            self.clock += 1
            result = self.clock
        elif address == 0xCBFB7B:
            result = uc.reg_read(UC_X86_REG_EAX)  # Security-cookie helper preserves return value.
        elif address in (0x5AA1A0, 0x5A7720):
            # Cipher factory/selection is modeled, not protection/authentication.
            result = 0
        elif address == 0xC39100:
            row = self.current
            if row is None:
                raise AssertionError("Cipher called without selected fixture")
            if bytes(uc.mem_read(arg(1), arg(2))) != row["stored"] or arg(4) != row["tag"]:
                raise AssertionError("Native cipher arguments differ from stored entry")
            value = crypt(row["stored"], row["tag"], iv=row["iv"])
            capacity = self.live[arg(0)][0]
            if len(value) > capacity:
                raise AssertionError("Modeled crypto would exceed native scratch allocation")
            uc.mem_write(arg(0), value)
            result = len(value)
        else:
            raise RuntimeError(f"Native reader reached error-reporting branch: {address:x}")
        uc.reg_write(UC_X86_REG_EAX, result)
        uc.reg_write(UC_X86_REG_EIP, self.get(esp))
        uc.reg_write(UC_X86_REG_ESP, esp + pop)

    def call(self, address, *args, instruction_limit=10_000_000):
        stack = 0x10010000
        self.put(stack, 0x10000000, *args)
        self.uc.reg_write(UC_X86_REG_ESP, stack)
        self.uc.emu_start(address, 0x10000000, timeout=15_000_000, count=instruction_limit)
        if self.uc.reg_read(UC_X86_REG_EIP) != 0x10000000:
            raise RuntimeError(f"Native instruction/time budget exceeded at {self.uc.reg_read(UC_X86_REG_EIP):08x}")
        return self.uc.reg_read(UC_X86_REG_EAX)

    def run(self, sequence):
        for number in sequence:
            self.current = row = self.fixtures[number]
            self.uc.mem_write(0x20002000, row["name"].encode("cp949") + b"\0")
            self.uc.mem_write(0x20002200, b"rb\0")
            try:
                resource = self.call(0x5AAF60, 0x20002000, 0x20002200)
            except AllocationFailure as error:
                return {"status": "MODELED_ALLOCATION_FAILURE", "first_failure": number,
                        "error": str(error), **self.summary()}
            if not resource or resource not in self.live:
                raise AssertionError("Native reader did not return a live resource")
            data = bytes(self.uc.mem_read(self.get(resource + 8), len(row["decoded"])))
            if data != row["decoded"]:
                raise AssertionError(f"Native open/decode differs for archive {number}")
            self.call(0x5AAE90, resource)
            if any(kind != "object" or count != 24 for count, _, kind in self.live.values()):
                raise AssertionError("Native release leaked a payload/resource/zlib allocation")
            tracked = set()
            head = self.get(0x3C2A060)
            def walk(pointer, head=head, tracked=tracked):
                if pointer == head:
                    return
                walk(self.get(pointer))
                tracked.add(self.get(pointer + 16))
                walk(self.get(pointer + 8))
            walk(self.get(head + 4))
            orphaned = [n for n, _ in self.views.values() if n not in tracked]
            orphan_bytes = sum(span for n, span in self.views.values() if n not in tracked)
            self.peak_untracked = max(self.peak_untracked, orphan_bytes)
            self.results.append({"archive": number, "sha256": hashlib.sha256(data).hexdigest(),
                                 "cache_count": self.get(0x3C2A064), "tracked_ids": sorted(tracked),
                                 "view_ids": sorted(n for n, _ in self.views.values()),
                                 "untracked_ids": sorted(orphaned)})
        return {"status": "PASS", **self.summary()}

    def summary(self):
        return {"completed": len(self.results), "view_budget_bytes": self.budget,
                "peak_view_bytes": self.peak, "peak_untracked_view_bytes": self.peak_untracked,
                "map_failures": sum(e["event"] == "map_failed" for e in self.events),
                "fallback_reads": sum(e["event"] == "fallback_read" for e in self.events),
                "rows": self.results, "events": self.events}


def fixtures(directory):
    rows = []
    for number in range(44, 331):
        archive = directory / f"{number:03d}.acv"
        with archive.open("rb") as stream:
            entries = [e for e in read_index(stream) if e[0] == FORMAT % number]
            if len(entries) != 1:
                raise ValueError(f"Expected unique SLK in {archive}")
            entry = entries[0]
            decoded = decode_entry(stream, entry)
            stream.seek(entry[4])
            stored = stream.read(entry[2])
            if len(stored) != entry[2]:
                raise ValueError("Truncated SLK payload")
            stream.seek(entry[4])
            fallback = stream.read(entry[3])
        rows.append({"number": number, "name": entry[0], "decoded": decoded,
                     "stored": stored, "fallback": fallback, "tag": entry[5], "iv": entry[6],
                     "offset": entry[4], "file_bytes": archive.stat().st_size})
    return rows


def self_test(image):
    data = b"SLK synthetic fixture\0" * 20
    packed = zlib.compress(data)
    rows = [{"number": i, "name": FORMAT % i, "decoded": data, "stored": packed,
             "fallback": packed.ljust(len(data), b"\0"),
             "tag": 0, "iv": None, "offset": 4096, "file_bytes": 1 << 20}
            for i in range(250, 281)]
    tests = []
    for patched in (False, True):
        for budget in (0, 8 << 20, 64 << 20):
            result = Reader(image, rows, budget, patched).run(list(range(250, 281)))
            if result["status"] != "PASS" or result["completed"] != 31:
                raise AssertionError("Native open self-test failed")
            if budget == 0 and result["fallback_reads"] != 31:
                raise AssertionError("Forced map failures did not exercise fallback")
            if budget == 64 << 20 and bool(result["peak_untracked_view_bytes"]) == patched:
                raise AssertionError("Cache patch did not change high-ID orphaned views")
            tests.append({k: v for k, v in result.items() if k not in ("rows", "events")})
    failure = Reader(image, rows, 0, fail_allocation="payload").run([250])
    if failure["status"] != "MODELED_ALLOCATION_FAILURE":
        raise AssertionError("Allocation failure did not stop at modeled throwing boundary")
    return {"status": "PASS", "cases": 7, "runs": tests, "allocation_failure": failure}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, nargs="?")
    parser.add_argument("report", type=Path, nargs="?")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    image = image_bytes()
    if args.self_test:
        print(json.dumps(self_test(image)))
        return
    if args.directory is None or args.report is None:
        parser.error("directory and NEW report required")
    with args.report.open("x", encoding="utf-8") as output:
        rows = fixtures(args.directory)
        results = []
        for budget in (0, 128 << 20, 512 << 20, 1536 << 20):
            for patched in (False, True):
                result = Reader(image, rows, budget, patched).run(list(range(44, 331)))
                result["patched"] = patched
                results.append(result)
        json.dump({"scope": __doc__, "image_sha256": IMAGE_SHA,
                   "directory": str(args.directory), "runs": results}, output, indent=2)
        output.write("\n")
    for result in results:
        print({k: v for k, v in result.items() if k not in ("rows", "events")})
    if any(result["status"] != "PASS" for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
