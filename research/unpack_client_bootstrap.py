"""Statically decode this client's aPLib-style bootstrap; never execute the client.

Usage: python -m research.unpack_client_bootstrap ../Audition.exe NEW_OUTPUT_DIRECTORY
       python -m research.unpack_client_bootstrap --self-test
Output is a raw loader memory region, NOT an unpacked/runnable game executable.
"""

import hashlib
import json
import struct
import sys
from pathlib import Path


class Bits:
    def __init__(self, data, offset):
        self.data, self.position = data, offset
        self.tag, self.mask = 0, 0

    def byte(self):
        if self.position >= len(self.data):
            raise ValueError("Truncated compressed stream")
        value = self.data[self.position]
        self.position += 1
        return value

    def bit(self):
        if not self.mask:
            self.tag, self.mask = self.byte(), 128
        value = int(bool(self.tag & self.mask))
        self.mask >>= 1
        return value

    def gamma(self):
        value = 1
        for _ in range(32):
            value = value * 2 + self.bit()
            if not self.bit():
                return value
        raise ValueError("Oversized gamma code")


def depack(data, offset=0, max_output=64 * 1024 * 1024):
    bits = Bits(data, offset)
    output = bytearray()
    previous = None
    state = 2

    def copy(distance, length):
        if distance <= 0 or distance > len(output):
            raise ValueError("Invalid back-reference")
        if len(output) + length > max_output:
            raise ValueError("Output safety limit exceeded")
        for _ in range(length):
            output.append(output[-distance])

    output.append(bits.byte())
    while True:
        if len(output) > max_output:
            raise ValueError("Output safety limit exceeded")
        if not bits.bit():
            output.append(bits.byte())
            state = 2
        elif not bits.bit():
            distance = bits.gamma() - state
            state = 1
            if distance == 0:
                if previous is None:
                    raise ValueError("Missing previous distance")
                copy(previous, bits.gamma())
            else:
                distance = (distance - 1) * 256 + bits.byte()
                length = bits.gamma()
                if distance >= 32000 or distance < 128:
                    length += 2
                elif distance >= 1280:
                    length += 1
                copy(distance, length)
                previous = distance
        elif not bits.bit():
            value = bits.byte()
            distance = value >> 1
            if distance == 0:
                return bytes(output), bits.position
            copy(distance, 2 + (value & 1))
            previous, state = distance, 1
        else:
            distance = 0
            for _ in range(4):
                distance = distance * 2 + bits.bit()
            if distance:
                copy(distance, 1)
            else:
                output.append(0)
            state = 2


def self_test():
    for packed, raw in ((b"A\xc0\0", b"A"), (b"A\x60B\0", b"AB"),
                        (b"A\x6cB\x04\0", b"ABAB"),
                        (b"A\xe1\x80\0", b"A\0"),
                        (b"A\x51B\x02\x80\0", b"ABABAB"),
                        (b"A\x50B\x02C\x8b\0\0", b"ABABABCBCB"),
                        (b"A\xe3\x80\0", b"AA")):
        decoded, end = depack(packed)
        assert decoded == raw and end == len(packed), (packed, decoded, end)
    for packed, limit in ((b"", 100), (b"A\x60", 100),
                          (b"A\x60B\0", 1), (b"A\xd8\xff", 100),
                          (b"A\x80", 100)):
        try:
            depack(packed, max_output=limit)
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid stream accepted")
    print("Self-check passed: literals, short/long matches, zero, truncation and bounds.")


def unpack(source, destination):
    data = source.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if data[:2] != b"MZ":
        raise ValueError("Expected PE file")
    pe = struct.unpack_from("<I", data, 60)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise ValueError("Invalid PE signature")
    count = struct.unpack_from("<H", data, pe + 6)[0]
    optional_size = struct.unpack_from("<H", data, pe + 20)[0]
    if struct.unpack_from("<H", data, pe + 24)[0] != 0x10b:
        raise ValueError("Expected PE32")
    base = struct.unpack_from("<I", data, pe + 24 + 28)[0]
    sections = {}
    for i in range(count):
        record = pe + 24 + optional_size + 40 * i
        name = data[record:record + 8].rstrip(b"\0").decode("ascii")
        sections[name] = struct.unpack_from("<4I", data, record + 8)
    virtual_size, virtual_address, _, _ = sections[".themida"]
    _, boot_va, boot_size, boot_raw = sections[".boot"]
    # shortcut: offsets apply only to the inspected bootstrap, re-derive for other builds.
    entry = struct.unpack_from("<I", data, pe + 24 + 16)[0]
    if entry != boot_va + 0x58 or data[boot_raw + 0x200:boot_raw + 0x206] != bytes.fromhex("5e5a595bffe0"):
        raise ValueError("Unrecognized bootstrap layout")
    position = boot_raw + 0x206
    streams = data[position]
    position += 1
    if streams != 6 or virtual_size > 64 * 1024 * 1024:
        raise ValueError("Unrecognized bootstrap stream count or size")
    loader = bytearray()
    chunks = []
    for i in range(streams):
        start = position
        raw, position = depack(data[:boot_raw + boot_size], position,
                               max_output=virtual_size - len(loader))
        chunks.append({"stream": i, "source_offset": start,
                       "packed_bytes": position - start, "raw_bytes": len(raw),
                       "output_va": base + virtual_address + len(loader),
                       "sha256": hashlib.sha256(raw).hexdigest()})
        loader.extend(raw)
    if len(loader) > virtual_size:
        raise ValueError("Loader exceeds destination section")
    report = {"warning": "Raw bootstrap region only; original client code is still protected.",
              "source": str(source.resolve()), "source_sha256": digest,
              "output_base": base + virtual_address, "output_bytes": len(loader),
              "loader_entry_va": base + virtual_address + 0x1a87f4,
              "sha256": hashlib.sha256(loader).hexdigest(), "streams": chunks}
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "bootstrap.bin").write_bytes(loader)
    (destination / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
        raise ValueError("Source changed during analysis")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        self_test()
    elif len(sys.argv) == 3:
        unpack(Path(sys.argv[1]), Path(sys.argv[2]))
    else:
        raise SystemExit(__doc__)
