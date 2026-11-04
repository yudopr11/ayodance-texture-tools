"""Replay the recovered 44..330 SLK preflight loop, not a login or full loader.

Native loop, filename normalization and archive-ID predicate execute. Formatting,
index lookup and resource open/release are modeled from local archives; Python
handles index/crypto and recovered zlib verifies each distinct payload. Mapping,
cache eviction, OS, authentication and the subsequent exit path are NOT executed.
"""

import argparse
import hashlib
import json
import struct
from pathlib import Path

from unicorn import UC_ARCH_X86, UC_HOOK_CODE, UC_MODE_32, Uc
from unicorn.x86_const import (
    UC_X86_REG_EAX,
    UC_X86_REG_EBP,
    UC_X86_REG_EIP,
    UC_X86_REG_ESP,
)

from acv_tools.acv_unpack import crypt, decode_entry, read_index
from research.emulate_native_inflate import IMAGE_SHA, align, check, image_bytes

FORMAT = "배포금지%03d.slk"


def replay(image, catalog, failed_open=None):
    uc = Uc(UC_ARCH_X86, UC_MODE_32)
    uc.mem_map(0x400000, align(len(image)))
    uc.mem_write(0x400000, image)
    uc.mem_map(0x10000000, 0x20000)
    frame, node, handle = 0x10010000, 0x10011000, 0x10012000
    uc.reg_write(UC_X86_REG_EBP, frame)
    uc.reg_write(UC_X86_REG_ESP, frame - 0x28)
    lookups, opened, released = [], [], []

    def get(address):
        return struct.unpack("<I", uc.mem_read(address, 4))[0]

    def name(address):
        data = bytes(uc.mem_read(address, 256))
        if b"\0" not in data:
            raise AssertionError("Unterminated native resource request")
        return data.split(b"\0", 1)[0].decode("cp949")

    def callback(uc, address, size, _):
        esp = uc.reg_read(UC_X86_REG_ESP)
        arg = get(esp + 4)
        if address == 0x410F60:  # sprintf, cdecl; caller later removes arguments.
            if name(get(esp + 8)) != FORMAT:
                raise AssertionError("Native preflight format changed")
            value = (FORMAT % get(esp + 12)).encode("cp949")
            if len(value) >= 32:
                raise AssertionError("Native preflight filename buffer exceeded")
            uc.mem_write(arg, value + b"\0")
            result = len(value)
        elif address == 0x5A99C0:
            lookups.append({"name": name(arg), "requested_archive": get(esp + 8)})
            return  # Execute actual native normalization and archive-ID comparison.
        elif address in (0x5AADB0, 0x5A6130):
            owner = catalog.get(name(arg))
            if address == 0x5AADB0:  # Modeled global index membership.
                result = int(owner is not None)
            else:
                if owner is None:
                    raise AssertionError("Native lookup requested an absent catalog node")
                uc.mem_write(node + 0x20, struct.pack("<I", owner))
                result = node
        elif address == 0x5AAF60:
            resource = name(arg)
            if resource not in catalog or name(get(esp + 8)) != "rb":
                raise AssertionError("Unexpected native resource-open request")
            opened.append(resource)
            result = 0 if resource == failed_open else handle
        else:  # Resource release boundary; no decoded allocation lives in this replay.
            if arg != handle:
                raise AssertionError("Unexpected native resource release")
            released.append(arg)
            result = 0
        uc.reg_write(UC_X86_REG_EAX, result)
        uc.reg_write(UC_X86_REG_EIP, get(esp))
        # The two index members are thiscall (RET 4); other boundaries are cdecl.
        uc.reg_write(UC_X86_REG_ESP, esp + (8 if address in (0x5AADB0, 0x5A6130) else 4))

    for address in (0x410F60, 0x5A99C0, 0x5AADB0, 0x5A6130, 0x5AAF60, 0x5AAE90):
        uc.hook_add(UC_HOOK_CODE, callback, begin=address, end=address)
    # Skip the TLS guard and index initialization; stop before timers/UI/exit code.
    uc.emu_start(0x9C4F9F, 0x9C5014, timeout=5_000_000, count=5_000_000)
    if uc.reg_read(UC_X86_REG_EIP) != 0x9C5014:
        raise RuntimeError("Native preflight instruction/time budget exceeded")
    if [row["requested_archive"] for row in lookups] != list(range(44, 331)):
        raise AssertionError(f"Native preflight requests differ: {len(lookups)} lookups, {lookups[:2]}, {lookups[-2:]}")
    if len(released) != len(opened) - int(failed_open in opened):
        raise AssertionError("Native preflight resource release mismatch")
    return {"validation_flag": int(uc.mem_read(0x3C4848C, 1)[0]),
            "lookups": lookups, "resource_opens": len(opened), "resource_releases": len(released)}


def self_test(image):
    catalog = {FORMAT % i: i for i in range(44, 331)}
    missing = dict(catalog)
    del missing[FORMAT % 262]
    wrong_owner = dict(catalog)
    wrong_owner[FORMAT % 262] = 6
    for entries, failed_open, expected in (
        (catalog, None, 1), (missing, None, 0), (wrong_owner, None, 0),
        (catalog, FORMAT % 262, 0),
    ):
        result = replay(image, entries, failed_open=failed_open)
        if result["validation_flag"] != expected:
            raise AssertionError(f"Native preflight flag {result['validation_flag']}, expected {expected}")
    return {"status": "PASS", "cases": 4, "native_lookup_attempts": 1148}


def inspect(image, directory):
    wanted = {FORMAT % i for i in range(44, 331)}
    catalog, rows, checked = {}, [], set()
    for number in range(1, 331):
        archive = directory / f"{number:03d}.acv"
        with archive.open("rb") as stream:
            for entry in read_index(stream):
                if entry[0] not in wanted:
                    continue
                if entry[0] in catalog:
                    raise ValueError(f"Duplicate preflight resource: {entry[0]}")
                decoded = decode_entry(stream, entry)
                stream.seek(entry[4])
                packed = stream.read(entry[2])
                if len(packed) != entry[2]:
                    raise ValueError(f"Truncated payload: {archive}: {entry[0]}")
                if entry[5]:
                    packed = crypt(packed, entry[5], iv=entry[6])
                key = (hashlib.sha256(packed).hexdigest(), hashlib.sha256(decoded).hexdigest())
                if key not in checked:
                    check(image, packed, decoded)
                    checked.add(key)
                catalog[entry[0]] = number
                rows.append({"name": entry[0], "archive": number, "decoded_bytes": len(decoded),
                             "sha256": key[1]})
    result = replay(image, catalog)
    result.update(entries=rows, distinct_native_inflate_executions=len(checked),
                  status="PASS" if result["validation_flag"] == 1 else "FAIL")
    return result


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
        parser.error("directory and NEW report are required")
    with args.report.open("x", encoding="utf-8") as output:
        result = inspect(image, args.directory)
        result.update(image_sha256=IMAGE_SHA, directory=str(args.directory), scope=__doc__)
        json.dump(result, output, indent=2, ensure_ascii=True)
        output.write("\n")
    print(f"{result['status']}: flag={result['validation_flag']}, {result['resource_opens']} opens; {args.report}")
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
