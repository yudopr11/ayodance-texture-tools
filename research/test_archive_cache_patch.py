"""Offline execution of native cache erase/cleanup and the exact proposed patch.

Runs original x86 tree erase, free wrappers, cleanup-array indexing and clearing.
Only Win32 HeapFree/unmap/close are modeled. No live game, OS handles or login are used.
"""

import re
import struct
import unittest

from unicorn import UC_ARCH_X86, UC_HOOK_CODE, UC_MODE_32
from unicorn.unicorn import Uc
from unicorn.x86_const import (
    UC_X86_REG_EAX,
    UC_X86_REG_EBP,
    UC_X86_REG_EBX,
    UC_X86_REG_EDI,
    UC_X86_REG_EIP,
    UC_X86_REG_ESI,
    UC_X86_REG_ESP,
)

from research.emulate_native_inflate import ROOT, align, image_bytes

START, STOP = 0x5AB1AB, 0x5AB234


def patch_bytes(name):
    text = (ROOT / "renderer_patch/archive_cache_patch.hpp").read_text()
    match = re.search(rf"{name}\[\] = \{{(.*?)\}};", text, re.S)
    if not match:
        raise ValueError("Patch definition missing")
    return bytes(int(value, 16) for value in re.findall(r"0x([0-9a-fA-F]{2})", match[1]))


def run_cleanup(image, candidate, patched, keys=None):
    uc = Uc(UC_ARCH_X86, UC_MODE_32)
    uc.mem_map(0x400000, align(len(image)))
    uc.mem_write(0x400000, image)
    before, after = patch_bytes("archive_cache_before"), patch_bytes("archive_cache_after")
    if len(before) != 38 or len(after) != 38 or bytes(uc.mem_read(START, 38)) != before:
        raise ValueError("Native cache patch guard mismatch")
    if patched:
        uc.mem_write(START, after)
    uc.mem_map(0x10000000, 0x20000)
    uc.mem_map(0x20000000, 0x10000)
    frame, stack, head, node = 0x10010000, 0x10008000, 0x20000000, 0x20000100

    def put(address, *values):
        uc.mem_write(address, struct.pack("<" + "I" * len(values), *values))

    def get(address):
        return struct.unpack("<I", uc.mem_read(address, 4))[0]

    keys = [candidate] if keys is None else keys
    nodes = {key: node + index * 0x100 for index, key in enumerate(keys)}

    def tree(ordered, parent):
        if not ordered:
            return head
        middle = len(ordered) // 2
        address = nodes[ordered[middle]]
        left = tree(ordered[:middle], address)
        right = tree(ordered[middle + 1:], address)
        put(address, left, parent, right, 1, ordered[middle], 123)
        return address

    # Perfect all-black trees (one or seven nodes) have equal black heights.
    root = tree(sorted(keys), head)
    put(head, nodes[min(keys)], root, nodes[max(keys)], 0x101)
    put(0x3C2A060, head, len(keys))
    node = nodes[candidate]
    filename = b"DATA\\330.acv\x00" + b"\xa5" * 64
    uc.mem_write(frame - 0x310, filename)
    for register, value in ((UC_X86_REG_EBP, frame), (UC_X86_REG_ESP, stack),
                            (UC_X86_REG_ESI, node), (UC_X86_REG_EBX, 0x11112222),
                            (UC_X86_REG_EDI, 0x33334444)):
        uc.reg_write(register, value)
    for table, base in ((0x3C2B020, 0x51000000), (0x3C2A850, 0x52000000),
                        (0x3C2A080, 0x53000000)):
        for index in range(500):
            put(table + index * 4, base + index)
    unmap, close, heap_free = 0x20001000, 0x20001010, 0x20001020
    put(0xD44150, unmap)
    put(0xD44108, close)
    put(0xD44248, heap_free)
    put(0x3CF706C, 0x1234)
    calls = []

    def callback(uc, address, size, _):
        esp = uc.reg_read(UC_X86_REG_ESP)
        value = get(esp + 4)
        if address == 0xCBFB68:
            if value != node or get(esp + 8) != 24:
                raise AssertionError("Native erase returned unexpected node/free size")
            return  # Execute the actual cdecl sized-delete/free wrappers.
        if address == heap_free:
            if value != 0x1234 or get(esp + 8) != 0 or get(esp + 12) != node:
                raise AssertionError("Unexpected native HeapFree arguments")
            calls.append(("free", node))
            uc.mem_write(node, b"\xdd" * 24)  # Reject reliance on freed key memory.
            pop = 16  # stdcall HeapFree(heap, flags, allocation).
        else:
            calls.append(("unmap" if address == unmap else "close", value))
            pop = 8  # Win32 stdcall, one argument.
        uc.reg_write(UC_X86_REG_EAX, 1)
        uc.reg_write(UC_X86_REG_EIP, get(esp))
        uc.reg_write(UC_X86_REG_ESP, esp + pop)

    for address in (0xCBFB68, unmap, close, heap_free):
        uc.hook_add(UC_HOOK_CODE, callback, begin=address, end=address)
    uc.emu_start(START, STOP, timeout=2_000_000, count=100_000)
    if uc.reg_read(UC_X86_REG_EIP) != STOP:
        raise AssertionError("Native cache cleanup exceeded emulation budget")
    if (uc.reg_read(UC_X86_REG_ESP) != stack or uc.reg_read(UC_X86_REG_EBP) != frame
            or uc.reg_read(UC_X86_REG_EBX) != 0x11112222 or uc.reg_read(UC_X86_REG_EDI) != 0x33334444):
        raise AssertionError("Cache patch changed stack or preserved register")
    if bytes(uc.mem_read(frame - 0x310, len(filename))) != filename:
        raise AssertionError("Cache patch corrupted adjacent archive filename")
    if get(0x3C2A064) != len(keys) - 1:
        raise AssertionError("Native tree erase did not update count")
    visited = set()

    def survivors(address, parent):
        if address == head:
            return []
        if address in visited or address == node or get(address + 4) != parent:
            raise AssertionError("Native tree erase broke survivor links")
        visited.add(address)
        return survivors(get(address), address) + [get(address + 16)] + survivors(get(address + 8), address)

    if survivors(get(head + 4), head) != sorted(set(keys) - {candidate}):
        raise AssertionError("Native tree erase lost another resource")
    remaining = sorted(set(keys) - {candidate})
    if get(head) != (nodes[remaining[0]] if remaining else head) or get(head + 8) != (nodes[remaining[-1]] if remaining else head):
        raise AssertionError("Native tree extrema incorrect")
    index = uc.reg_read(UC_X86_REG_ESI)
    for table in (0x3C2B020, 0x3C2A850, 0x3C2A080):
        for current in range(500):
            if (get(table + current * 4) == 0) != (current == index and index < 500):
                raise AssertionError("Unexpected archive cleanup-array mutation")
    return index, calls


class ArchiveCachePatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image = image_bytes()

    def test_original_and_patch(self):
        for candidate in (0, 6, 74, 255, 256, 262, 329, 330, 499, 500, 0xffffffff):
            for patched in (False, True):
                with self.subTest(candidate=candidate, patched=patched):
                    index, calls = run_cleanup(self.image, candidate, patched)
                    expected = candidate if patched else candidate & 255
                    self.assertEqual(index, expected)
                    expected_calls = [("free", 0x20000100)]
                    if expected < 500:
                        expected_calls += [("unmap", 0x51000000 + expected),
                                           ("close", 0x52000000 + expected),
                                           ("close", 0x53000000 + expected)]
                    self.assertEqual(calls, expected_calls)

    def test_balanced_tree_erase(self):
        keys = [6, 74, 255, 256, 262, 330, 499]
        for candidate in keys:
            for patched in (False, True):
                with self.subTest(candidate=candidate, patched=patched):
                    index, _ = run_cleanup(self.image, candidate, patched, keys)
                    self.assertEqual(index, candidate if patched else candidate & 255)

    def test_guard_refuses_changed_native_bytes(self):
        image = bytearray(self.image)
        image[START - 0x400000] ^= 1
        with self.assertRaisesRegex(ValueError, "guard mismatch"):
            run_cleanup(bytes(image), 330, True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
