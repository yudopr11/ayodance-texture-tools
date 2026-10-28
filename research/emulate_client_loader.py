"""Isolated x86 interpretation of the verified loader; no client/DLL host execution.
Only trusted, read-only system DLL and hardware-registry metadata is read from the host.

Install diagnostic dependencies with:
  uv pip install --python .venv/Scripts/python.exe unicorn pefile
Or expose existing local research dependencies through PYTHONPATH.
Run: python -m research.emulate_client_loader NEW_OUTPUT_DIRECTORY
     python -m research.emulate_client_loader --self-test
This is a research probe, NOT an ACV unpacker or a runnable executable dumper.
"""

import hashlib
import json
import re
import struct
import sys
import time
import winreg
from pathlib import Path
from typing import Any

import pefile
from unicorn.unicorn import Uc, UcError
from unicorn.unicorn_const import (
    UC_ARCH_X86,
    UC_HOOK_BLOCK,
    UC_HOOK_CODE,
    UC_HOOK_INTR,
    UC_HOOK_MEM_INVALID,
    UC_HOOK_MEM_READ,
    UC_HOOK_MEM_WRITE,
    UC_MODE_32,
    UC_PROT_EXEC,
    UC_PROT_READ,
    UC_PROT_WRITE,
)
from unicorn.x86_const import (
    UC_CPU_X86_HASWELL,
    UC_X86_REG_CS,
    UC_X86_REG_DS,
    UC_X86_REG_EAX,
    UC_X86_REG_EBP,
    UC_X86_REG_EBX,
    UC_X86_REG_ECX,
    UC_X86_REG_EDI,
    UC_X86_REG_EDX,
    UC_X86_REG_EFLAGS,
    UC_X86_REG_EIP,
    UC_X86_REG_ES,
    UC_X86_REG_ESI,
    UC_X86_REG_ESP,
    UC_X86_REG_FS,
    UC_X86_REG_GDTR,
    UC_X86_REG_SS,
)

ROOT = Path(__file__).resolve().parents[1]
BASE, LOADER, LOADER_END, ENTRY = 0x400000, 0x3dd4000, 0x418a000, 0x3f7c7f4
CONTEXT_OFFSETS = {"edi": 0x9c, "esi": 0xa0, "ebx": 0xa4, "edx": 0xa8,
                   "ecx": 0xac, "eax": 0xb0, "ebp": 0xb4, "eip": 0xb8,
                   "eflags": 0xc0, "esp": 0xc4}
VEH_RETURN = 0x10000ff0

REGS: dict[str, int] = dict(zip(("eax", "ebx", "ecx", "edx", "esi", "edi", "ebp", "esp", "eip", "eflags"),
                (UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX, UC_X86_REG_EDX, UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP, UC_X86_REG_ESP, UC_X86_REG_EIP, UC_X86_REG_EFLAGS), strict=True))


def aligned(size):
    return (size + 0xfff) & ~0xfff


class Probe:
    def __init__(self, log):
        self.uc = Uc(UC_ARCH_X86, UC_MODE_32)
        self.uc.ctl_set_cpu_model(UC_CPU_X86_HASWELL)
        self.log = log
        self.modules, self.exports, self.apis = {}, {}, {}
        self.module_hooks = []
        self.rdtsc_hooks = []
        self.blocks, self.ticks = 0, 0x100000
        self.reason = None
        self.started = time.monotonic()
        self.dispatches = 0
        self.heap_next = 0x30000000
        self.virtual_next = 0x40000000
        self.virtual_allocations = {}
        self.allocations = {}
        self.last_error = 0
        self.vehs = []
        self.exception = None
        self.handles = set()
        self.registry_keys = {}
        self.snapshots = {}
        self.files = {}
        self.mappings = {}
        self.mapping_views = {}
        self.source_bytes = None
        self.current_directory = str(ROOT.parent)

    def say(self, message):
        print(message, file=self.log, flush=True)

    def state(self):
        return {name: self.uc.reg_read(reg) for name, reg in REGS.items()}

    def u32(self, address):
        return struct.unpack("<I", self.uc.mem_read(address, 4))[0]

    def text(self, address):
        value = bytearray()
        for i in range(256):
            c = self.uc.mem_read(address + i, 1)[0]
            if not c:
                return value.decode("ascii")
            value.append(c)
        raise ValueError("Unterminated API string")

    def text_w(self, address):
        value = bytearray()
        for i in range(1024):
            pair = self.uc.mem_read(address + 2 * i, 2)
            if pair == b"\0\0":
                return value.decode("utf-16-le")
            value.extend(pair)
        raise ValueError("Unterminated wide API string")

    def accessible(self, address, size, permission):
        limit = address + size
        if limit > 0x100000000:
            return False
        if not size:
            return True
        cursor = address
        for lo, hi, flags in sorted(self.uc.mem_regions()):
            if hi < cursor:
                continue
            if lo > cursor or flags & permission != permission:
                return False
            cursor = min(limit, hi + 1)
            if cursor == limit:
                return True
        return False

    def sid(self, address):
        revision, count = self.uc.mem_read(address, 2)
        if revision != 1 or count > 15:
            raise ValueError("Invalid SID header")
        return bytes(self.uc.mem_read(address, 8 + count * 4))

    def stop(self, reason):
        self.reason = reason
        self.say("STOP " + reason)
        self.uc.emu_stop()

    def set_error(self, value):
        self.last_error = value
        self.uc.mem_write(0x7ffde034, struct.pack("<I", value))

    def api_return(self, value, argument_bytes):
        esp = self.uc.reg_read(UC_X86_REG_ESP)
        caller = self.u32(esp)
        self.uc.reg_write(UC_X86_REG_EAX, value)
        self.uc.reg_write(UC_X86_REG_ESP, esp + 4 + argument_bytes)
        self.uc.reg_write(UC_X86_REG_EIP, caller)

    def module(self, name):
        if name in self.modules:
            return self.modules[name]
        if not re.fullmatch(r"[a-z0-9_-]+\.dll", name) or len(self.modules) >= 24:
            raise ValueError("Unsupported module: " + name)
        data = Path("C:/Windows/SysWOW64", name).read_bytes()
        pe: Any = pefile.PE(data=data)
        if pe.OPTIONAL_HEADER.Magic != 0x10b or pe.OPTIONAL_HEADER.SizeOfImage > 32 * 1024 * 1024:
            raise ValueError("Expected bounded PE32 system module")
        base = 0x70000000 + len(self.modules) * 0x2000000
        preferred = pe.OPTIONAL_HEADER.ImageBase
        image = bytearray(pe.get_memory_mapped_image(ImageBase=base))
        struct.pack_into("<I", image, pe.OPTIONAL_HEADER.get_field_absolute_offset("ImageBase"), preferred)
        self.uc.mem_map(base, aligned(pe.OPTIONAL_HEADER.SizeOfImage), UC_PROT_READ)
        self.uc.mem_write(base, bytes(image))
        for section in pe.sections:
            flags = section.Characteristics
            permissions = ((UC_PROT_READ if flags & 0x40000000 else 0) |
                           (UC_PROT_WRITE if flags & 0x80000000 else 0) |
                           (UC_PROT_EXEC if flags & 0x20000000 else 0))
            length = aligned(max(section.Misc_VirtualSize, section.SizeOfRawData))
            if length:
                self.uc.mem_protect(base + section.VirtualAddress, length, permissions)
        symbols = {}
        for symbol in pe.DIRECTORY_ENTRY_EXPORT.symbols:
            if not symbol.address:
                continue
            address = base + symbol.address
            symbols["#" + str(symbol.ordinal)] = address
            if symbol.name:
                api = symbol.name.decode("ascii")
                symbols[api] = address
                self.apis[address] = api
        self.modules[name], self.exports[base] = base, symbols
        # Forwarder RVAs are logical API identities here; never interpret any DLL bytes.
        self.uc.hook_add(UC_HOOK_CODE, self.on_api, begin=base, end=base + len(image) - 1)
        self.module_hooks.append((base, base + len(image) - 1))
        self.say(f"MODULE {name} base={base:08x} sha256={hashlib.sha256(data).hexdigest()}")
        return base

    def heap_allocate(self, requested):
        length = aligned(max(requested, 1))
        if length > 64 * 1024 * 1024 or self.heap_next + length > 0x38000000:
            raise ValueError("Heap size budget exceeded")
        result = self.heap_next
        self.uc.mem_map(result, length)
        self.allocations[result] = length
        self.heap_next += length
        return result

    def thread_environment(self):
        gdt, teb, peb = 0x7ffdc000, 0x7ffde000, 0x7ffdf000
        self.uc.mem_map(gdt, 0x4000)
        descriptor = struct.pack("<HHBBBB", 0xfff, teb & 0xffff, (teb >> 16) & 255, 0xf3, 0x40, teb >> 24)
        self.uc.mem_write(gdt + 8, struct.pack("<HHBBBB", 0xffff, 0, 0, 0x9b, 0xcf, 0))
        self.uc.mem_write(gdt + 16, struct.pack("<HHBBBB", 0xffff, 0, 0, 0x93, 0xcf, 0))
        self.uc.mem_write(gdt + 24, struct.pack("<HHBBBB", 0xffff, 0, 0, 0xfb, 0xcf, 0))
        self.uc.mem_write(gdt + 32, struct.pack("<HHBBBB", 0xffff, 0, 0, 0xf3, 0xcf, 0))
        self.uc.mem_write(gdt + 56, descriptor)
        self.uc.reg_write(UC_X86_REG_GDTR, (0, gdt, 0xfff, 0))
        self.uc.reg_write(UC_X86_REG_CS, 8)
        self.uc.reg_write(UC_X86_REG_SS, 16)
        self.uc.mem_map(0x20000000, 0x20000)
        self.uc.reg_write(UC_X86_REG_ESP, 0x20010000 - 20)
        self.uc.mem_write(0x20010000 - 20, struct.pack("<5I", 0x7ffdd010, 0x1b, 0x202, 0x20010000, 0x23))
        self.uc.mem_write(0x7ffdd000, b"\xcf")
        # A real IRET transition updates Unicorn's CPL; writing CS alone leaves CPL=0.
        self.uc.emu_start(0x7ffdd000, 0x7ffdd010, count=1)
        for reg in (UC_X86_REG_DS, UC_X86_REG_ES):
            self.uc.reg_write(reg, 0x23)
        self.uc.reg_write(UC_X86_REG_FS, 0x3b)
        for offset, value in {0: 0xffffffff, 4: 0x20020000, 8: 0x20000000,
                              0x18: teb, 0x20: 1, 0x24: 1, 0x30: peb}.items():
            self.uc.mem_write(teb + offset, struct.pack("<I", value))
        self.uc.mem_write(peb + 8, struct.pack("<I", BASE))
        self.uc.mem_write(peb + 0x18, struct.pack("<I", 0x2f000001))
        self.say("MODEL_TEB thread=1 process=1 stack=20000000..20020000; minimal PEB; FS via GDT")

    def start_exception(self, esp, api_address):
        if self.exception is not None:
            self.stop("Nested exception not modeled")
            return
        code, flags, count, arguments = (self.u32(esp + i) for i in (4, 8, 12, 16))
        if count > 15 or not self.vehs:
            self.stop("Unsupported exception parameters/handler chain")
            return
        scratch, record, context = 0x2d000000, 0x2d000100, 0x2d000200
        self.uc.mem_write(scratch, bytes(0x1000))
        self.uc.mem_write(scratch, struct.pack("<II", record, context))
        header = struct.pack("<5I", code, flags, 0, api_address, count)
        values = bytes(self.uc.mem_read(arguments, count * 4)) if count else b""
        self.uc.mem_write(record, header + values)
        self.uc.mem_write(context, struct.pack("<I", 0x10007))
        state = self.state()
        # shortcut: logical RaiseException caller frame; extend if handlers inspect DLL-internal frames.
        state["eip"], state["esp"] = self.u32(esp), esp + 20
        for name, offset in CONTEXT_OFFSETS.items():
            self.uc.mem_write(context + offset, struct.pack("<I", state[name]))
        for offset, reg in ((0x90, UC_X86_REG_FS), (0x94, UC_X86_REG_ES),
                            (0x98, UC_X86_REG_DS), (0xbc, UC_X86_REG_CS), (0xc8, UC_X86_REG_SS)):
            self.uc.mem_write(context + offset, struct.pack("<I", self.uc.reg_read(reg)))
        self.exception = {"context": context, "scratch": scratch, "stack": esp - 0x100,
                          "handler": 0, "flags": flags}
        self.say(f"EXCEPTION code={code:08x} logical_caller={state['eip']:08x}")
        self.say("VEH_CONTEXT_BEFORE=" + bytes(self.uc.mem_read(context, 0x2cc)).hex())
        self.call_veh()

    def exception_access(self, uc, access, address, size, value, _):
        if self.exception is not None:
            self.say(f"EXCEPTION_ACCESS type={access} pc={uc.reg_read(UC_X86_REG_EIP):08x} address={address:08x} size={size} old={bytes(uc.mem_read(address, size)).hex()} write_value={value:x}")

    def call_veh(self):
        exception = self.exception
        if exception is None or exception["handler"] >= len(self.vehs):
            self.stop("Unhandled vectored exception; SEH dispatch not yet modeled")
            return
        callback = self.vehs[exception["handler"]][1]
        esp = exception["stack"]
        self.uc.mem_write(esp, struct.pack("<II", VEH_RETURN, exception["scratch"]))
        self.uc.reg_write(UC_X86_REG_ESP, esp)
        self.uc.reg_write(UC_X86_REG_EIP, callback)
        self.say(f"CALL_VEH {callback:08x}")

    def veh_return(self):
        exception = self.exception
        if exception is None:
            self.stop("Unexpected VEH return")
            return
        disposition = self.uc.reg_read(UC_X86_REG_EAX)
        self.say(f"VEH_RETURN disposition={disposition:08x}")
        self.say("VEH_CONTEXT_AFTER=" + bytes(self.uc.mem_read(exception["context"], 0x2cc)).hex())
        if disposition == 0xffffffff and not exception["flags"] & 1:
            for name, offset in CONTEXT_OFFSETS.items():
                self.uc.reg_write(REGS[name], self.u32(exception["context"] + offset))
            self.exception = None
        elif disposition == 0:
            exception["handler"] += 1
            self.call_veh()
        else:
            self.stop("Unsupported exception disposition/continuation")

    def on_file_api(self, name, esp):
        uc = self.uc
        a = self.u32(esp + 4)
        if name == "CreateFileW":
            path = Path(self.text_w(a)).resolve()
            access, sharing, security, disposition, flags, template = (self.u32(esp + i) for i in (8, 12, 16, 20, 24, 28))
            if (path != Path(self.current_directory, "Audition.exe").resolve() or self.source_bytes is None
                    or access != 0x80000000 or sharing != 1 or security or disposition != 3 or flags != 0x20 or template):
                self.stop("Unsupported file-open request; only verified client bytes may be read")
                return
            handle = max(self.files, default=0x86000000) + 1
            self.files[handle] = 0
            self.handles.add(handle)
            self.say("API CreateFileW => verified client bytes, read-only")
            self.api_return(handle, 28)
        elif name == "CreateFileMappingW" and a in self.files:
            security, protection, high, low, label = (self.u32(esp + i) for i in (8, 12, 16, 20, 24))
            if security or protection != 2 or high or low or label:
                self.stop("Unsupported file mapping; only unnamed read-only whole-client mapping is modeled")
                return
            handle = max(self.mappings, default=0x87000000) + 1
            self.mappings[handle] = self.source_bytes
            self.handles.add(handle)
            self.say("API CreateFileMappingW => verified client bytes, PAGE_READONLY")
            self.api_return(handle, 24)
        elif name == "MapViewOfFile" and a in self.mappings:
            access, high, low, count = (self.u32(esp + i) for i in (8, 12, 16, 20))
            data = self.mappings[a]
            if access != 4 or high or low or count > len(data):
                self.stop("Unsupported mapping view; only read-only client bytes at offset zero are modeled")
                return
            data = data[:count] if count else data
            length = aligned(len(data))
            result = self.virtual_next
            if not length or result + length > 0x50000000:
                self.stop("Mapping-view allocation budget exceeded")
                return
            uc.mem_map(result, length, UC_PROT_READ)
            uc.mem_write(result, data)
            self.mapping_views[result] = length
            self.virtual_next = (result + length + 0xffff) & ~0xffff
            self.say(f"API MapViewOfFile bytes={len(data)} => {result:08x}, read-only")
            self.api_return(result, 20)
        elif name == "UnmapViewOfFile" and a in self.mapping_views:
            uc.mem_unmap(a, self.mapping_views.pop(a))
            self.say(f"API UnmapViewOfFile released {a:08x}")
            self.api_return(1, 4)
        elif name == "GetFileSize" and a in self.files and self.source_bytes is not None:
            length = len(self.source_bytes)
            high = self.u32(esp + 8)
            if high:
                uc.mem_write(high, struct.pack("<I", length >> 32))
            self.say(f"API GetFileSize => {length} verified bytes")
            self.api_return(length & 0xffffffff, 8)
        else:
            self.stop("Unsupported file API or invalid simulated file handle")

    def on_api(self, uc, address, size, _):
        name = self.apis.get(address)
        if name == "__VEH_RETURN":
            self.veh_return()
            return
        esp = uc.reg_read(UC_X86_REG_ESP)
        a = self.u32(esp + 4)
        try:
            if name in ("CreateFileW", "CreateFileMappingW", "MapViewOfFile", "UnmapViewOfFile", "GetFileSize"):
                self.on_file_api(name, esp)
            elif name == "RaiseException":
                self.start_exception(esp, address)
            elif name in ("GetModuleHandleA", "LoadLibraryA"):
                text = self.text(a).lower() if a else ""
                base = self.modules.get(text)
                if name == "LoadLibraryA" and a:
                    base = self.module(text)
                if not a and name == "GetModuleHandleA":
                    base = BASE
                self.say(f"API {name}({text!r}) => {base}")
                if base is None:
                    if name != "GetModuleHandleA":
                        self.stop("Unsupported module lookup")
                        return
                    self.set_error(126)
                    base = 0
                self.api_return(base, 4)
            elif name == "IsWow64Process" and a == 0xffffffff:
                # shortcut: native Win32 OS persona; add a 64-bit CPU model if the loader requires WOW64.
                uc.mem_write(self.u32(esp + 8), struct.pack("<I", 0))
                self.say("API IsWow64Process => native Win32 OS model, no WOW64 transition")
                self.api_return(1, 8)
            elif name == "RegCloseKey" and a in self.registry_keys:
                self.registry_keys.pop(a)
                self.api_return(0, 4)
            elif name == "RegQueryValueExA" and a in self.registry_keys:
                value = self.text(self.u32(esp + 8))
                key = self.registry_keys[a].lower()
                if value.lower() == "enablelua" and key.endswith(r"policies\system"):
                    data, kind = struct.pack("<I", 1), 4
                elif value.lower() == "systemstartoptions" and key == r"system\currentcontrolset\control":
                    data, kind = b"NOEXECUTE=OPTIN\0", 1
                elif key == r"hardware\description\system":
                    try:
                        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key, 0, winreg.KEY_READ) as host_key:
                            raw, kind = winreg.QueryValueEx(host_key, value)
                    except FileNotFoundError:
                        self.api_return(2, 24)
                        return
                    if isinstance(raw, str):
                        data = (raw + "\0").encode("mbcs")
                    elif isinstance(raw, list) and all(isinstance(item, str) for item in raw):
                        data = ("\0".join(raw) + "\0\0").encode("mbcs")
                    elif kind == winreg.REG_DWORD and isinstance(raw, int):
                        data = struct.pack("<I", raw)
                    elif isinstance(raw, bytes):
                        data = raw
                    else:
                        self.stop("Unsupported hardware metadata value type")
                        return
                    if len(data) > 65536:
                        self.stop("Hardware metadata size limit")
                        return
                else:
                    self.stop("Unsupported simulated policy value " + value)
                    return
                type_pointer, data_pointer, size_pointer = (self.u32(esp + i) for i in (16, 20, 24))
                capacity = self.u32(size_pointer)
                uc.mem_write(size_pointer, struct.pack("<I", len(data)))
                if type_pointer:
                    uc.mem_write(type_pointer, struct.pack("<I", kind))
                if data_pointer and capacity >= len(data):
                    uc.mem_write(data_pointer, data)
                self.say(f"API RegQueryValueExA {value} type={kind} bytes={len(data)}")
                self.api_return(234 if data_pointer and capacity < len(data) else 0, 24)
            elif name == "RegOpenKeyA" and a == 0x80000002:
                key = self.text(self.u32(esp + 8))
                if key.lower() in (r"system\currentcontrolset\control\deviceguard\scenarios\hypervisorenforcedcodeintegrity",
                                   r"system\controlset001\control\class\{4d36e968-e325-11ce-bfc1-08002be10318}\0000"):
                    uc.mem_write(self.u32(esp + 12), bytes(4))
                    self.say("API RegOpenKeyA => optional hardware key absent in native Win32 OS model: " + key)
                    self.api_return(2, 12)
                    return
                if key.lower() not in (r"software\microsoft\windows\currentversion\policies\system",
                                       r"system\currentcontrolset\control", r"hardware\description\system"):
                    if not key.lower().startswith("hardware\\acpi\\"):
                        self.stop("Unsupported registry key " + key)
                        return
                    try:
                        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key, 0, winreg.KEY_READ):
                            pass
                    except FileNotFoundError:
                        uc.mem_write(self.u32(esp + 12), bytes(4))
                        self.say("API RegOpenKeyA => requested ACPI key absent (read-only host metadata)")
                        self.api_return(2, 12)
                        return
                handle = 0x84000001 + len(self.registry_keys)
                self.registry_keys[handle] = key
                uc.mem_write(self.u32(esp + 12), struct.pack("<I", handle))
                self.say("API RegOpenKeyA => simulated key " + key)
                self.api_return(0, 12)
            elif name == "IsUserAnAdmin":
                self.say("API IsUserAnAdmin => synthetic administrator persona only")
                self.api_return(1, 0)
            elif name == "FreeSid" and a in self.allocations:
                uc.mem_unmap(a, self.allocations.pop(a))
                self.say(f"API FreeSid({a:08x})")
                self.api_return(0, 4)
            elif name == "EqualSid":
                equal = self.sid(a) == self.sid(self.u32(esp + 8))
                self.say(f"API EqualSid => {equal}")
                self.api_return(int(equal), 8)
            elif name == "AllocateAndInitializeSid":
                count = self.u32(esp + 8)
                if count > 8:
                    self.stop("Invalid SID subauthority count")
                    return
                sid = bytes((1, count)) + bytes(uc.mem_read(a, 6)) + bytes(uc.mem_read(esp + 12, count * 4))
                result = self.heap_allocate(len(sid))
                uc.mem_write(result, sid)
                uc.mem_write(self.u32(esp + 44), struct.pack("<I", result))
                self.say(f"API AllocateAndInitializeSid subauthorities={count} => {result:08x}")
                self.api_return(1, 44)
            elif name == "GetTokenInformation" and a == 0x83000001:
                info_class, buffer, length, returned = (self.u32(esp + i) for i in (8, 12, 16, 20))
                if info_class != 2:
                    self.stop(f"Unsupported token information class {info_class}")
                    return
                # Synthetic administrator persona, not a host token or a privilege change.
                sids = [b"\1\2" + (5).to_bytes(6, "big") + struct.pack("<II", 32, group) for group in (544, 545)]
                sids.append(b"\1\1" + (1).to_bytes(6, "big") + struct.pack("<I", 0))
                needed = 4 + 8 * len(sids) + sum(map(len, sids))
                uc.mem_write(returned, struct.pack("<I", needed))
                if not buffer or length < needed:
                    self.set_error(122)
                    self.api_return(0, 20)
                    return
                records = bytearray(struct.pack("<I", len(sids)))
                position = buffer + 4 + 8 * len(sids)
                for sid in sids:
                    records.extend(struct.pack("<II", position, 7))
                    position += len(sid)
                uc.mem_write(buffer, bytes(records) + b"".join(sids))
                self.say("API GetTokenInformation => synthetic administrator token groups")
                self.api_return(1, 20)
            elif name == "CloseHandle":
                success = a in self.handles
                if success:
                    self.handles.remove(a)
                    self.snapshots.pop(a, None)
                    self.files.pop(a, None)
                    self.mappings.pop(a, None)
                else:
                    self.set_error(6)
                self.say(f"API CloseHandle({a:08x}) => {success}")
                self.api_return(int(success), 4)
            elif name == "OpenProcessToken" and a == 0xffffffff and self.u32(esp + 8) == 8:
                self.handles.add(0x83000001)
                uc.mem_write(self.u32(esp + 12), struct.pack("<I", 0x83000001))
                self.say("API OpenProcessToken => logical query-only token 83000001")
                self.api_return(1, 12)
            elif name == "OpenThreadToken" and a in (0xfffffffe, 0x81000001):
                self.set_error(1008)
                self.say("API OpenThreadToken => ERROR_NO_TOKEN (not impersonating)")
                self.api_return(0, 16)
            elif name == "GetLastError":
                self.api_return(self.last_error, 0)
            elif name == "SetLastError":
                self.set_error(a)
                self.api_return(0, 4)
            elif name == "GetSystemFirmwareTable" and a == 0x52534d42:
                self.set_error(50)
                self.say("API GetSystemFirmwareTable => ERROR_NOT_SUPPORTED; no SMBIOS exposed by virtual OS")
                self.api_return(0, 16)
            elif name == "lstrcmpiA":
                left, right = self.text(a).lower(), self.text(self.u32(esp + 8)).lower()
                result = (left > right) - (left < right)
                self.say(f"API lstrcmpiA({left!r}, {right!r}) => {result}")
                self.api_return(result & 0xffffffff, 8)
            elif name in ("NtQuerySystemInformation", "ZwQuerySystemInformation"):
                if a != 11:
                    self.stop(f"Unsupported system information class {a}")
                    return
                path = b"\\SystemRoot\\system32\\ntoskrnl.exe"
                module = struct.pack("<5I4H", 0, 0, 0x80400000, 0x700000, 0,
                                     0, 0, 1, path.rfind(b"\\") + 1) + path.ljust(256, b"\0")
                data = struct.pack("<I", 1) + module
                buffer, length, returned = (self.u32(esp + i) for i in (8, 12, 16))
                if returned:
                    uc.mem_write(returned, struct.pack("<I", len(data)))
                if length >= len(data):
                    uc.mem_write(buffer, data)
                self.say("API SystemModuleInformation => isolated native Win32 kernel model")
                self.api_return(0 if length >= len(data) else 0xc0000004, 16)
            elif name == "CheckRemoteDebuggerPresent" and a == 0xffffffff:
                uc.mem_write(self.u32(esp + 8), bytes(4))
                self.say("API CheckRemoteDebuggerPresent => isolated process has no debugger")
                self.api_return(1, 8)
            elif name == "CreateToolhelp32Snapshot":
                pid = self.u32(esp + 8)
                if a & ~0xf or pid not in (0, 1):
                    self.stop("Unsupported Toolhelp snapshot request")
                    return
                handle = 0x85000001 + len(self.snapshots)
                self.snapshots[handle] = {"flags": a, "process_index": -1}
                self.handles.add(handle)
                self.say("API CreateToolhelp32Snapshot => isolated idle/client process model")
                self.api_return(handle, 8)
            elif name in ("Process32First", "Process32FirstW", "Process32Next", "Process32NextW") and a in self.snapshots:
                first = name.startswith("Process32First")
                # The loader skips First's entry; Windows snapshots include Idle before user processes.
                processes = ((0, "System Idle Process"), (1, "Audition.exe"))
                index = 0 if first else self.snapshots[a]["process_index"] + 1
                if index >= len(processes):
                    self.set_error(18)
                    self.api_return(0, 8)
                    return
                wide = name.endswith("W")
                needed = 556 if wide else 296
                output = self.u32(esp + 8)
                if self.u32(output) < needed:
                    self.set_error(24)
                    self.api_return(0, 8)
                    return
                pid, filename = processes[index]
                filename = filename.encode("utf-16-le" if wide else "ascii")
                header = struct.pack("<9I", needed, 0, pid, 0, 0, 1, 0, 8, 0)
                uc.mem_write(output, (header + filename).ljust(needed, b"\0"))
                self.snapshots[a]["process_index"] = index
                self.say(f"API {name} => isolated process {pid}")
                self.api_return(1, 8)
            elif name == "FindWindowA":
                self.say("API FindWindowA class=" + (self.text(a) if a and a > 0xffff else str(a)) + " => empty simulated desktop")
                self.api_return(0, 8)
            elif name in ("IsBadReadPtr", "IsBadWritePtr"):
                permission = UC_PROT_READ if name == "IsBadReadPtr" else UC_PROT_WRITE
                bad = not self.accessible(a, self.u32(esp + 8), permission)
                self.say(f"API {name}({a:08x}) => {bad}")
                self.api_return(int(bad), 8)
            elif name == "VirtualProtect":
                length, protection, old_pointer = (self.u32(esp + i) for i in (8, 12, 16))
                permissions = {1: 0, 2: UC_PROT_READ, 4: UC_PROT_READ | UC_PROT_WRITE,
                               0x10: UC_PROT_EXEC, 0x20: UC_PROT_READ | UC_PROT_EXEC,
                               0x40: UC_PROT_READ | UC_PROT_WRITE | UC_PROT_EXEC}
                if protection not in permissions or not length or length > 64 * 1024 * 1024 or a + length > 0x100000000:
                    self.stop("Unsupported VirtualProtect request")
                    return
                previous = next((flags for lo, hi, flags in uc.mem_regions() if lo <= a <= hi), None)
                if previous is None:
                    self.stop("VirtualProtect on unmapped memory")
                    return
                old = next(key for key, flags in permissions.items() if flags == previous)
                uc.mem_protect(a & ~0xfff, aligned(a + length) - (a & ~0xfff), permissions[protection])
                uc.mem_write(old_pointer, struct.pack("<I", old))
                self.say(f"API VirtualProtect({a:08x}, {length:x}, {protection:x}) old={old:x}")
                self.api_return(1, 16)
            elif name == "VirtualFree":
                length, flags = self.u32(esp + 8), self.u32(esp + 12)
                if a not in self.virtual_allocations or length or flags != 0x8000:
                    self.stop("Unsupported VirtualFree request")
                    return
                uc.mem_unmap(a, self.virtual_allocations.pop(a))
                self.say(f"API VirtualFree released {a:08x}")
                self.api_return(1, 12)
            elif name == "VirtualAlloc":
                requested, flags, protection = self.u32(esp + 8), self.u32(esp + 12), self.u32(esp + 16)
                length = aligned(requested)
                if a or not length or length > 64 * 1024 * 1024 or flags not in (0x1000, 0x3000) or protection != 4:
                    self.stop(f"Unsupported VirtualAlloc({a:x}, {requested:x}, {flags:x}, {protection:x})")
                    return
                result = self.virtual_next
                if result + length > 0x50000000:
                    self.stop("Virtual allocation budget exceeded")
                    return
                uc.mem_map(result, length, UC_PROT_READ | UC_PROT_WRITE)
                self.virtual_allocations[result] = length
                self.virtual_next = (result + length + 0xffff) & ~0xffff
                self.say(f"API VirtualAlloc size={requested} => {result:08x}")
                self.api_return(result, 16)
            elif name == "GetModuleFileNameW" and a in (0, BASE):
                data = str(ROOT.parent / "Audition.exe").encode("utf-16-le")
                capacity = self.u32(esp + 12)
                if capacity:
                    uc.mem_write(self.u32(esp + 8), data[:2 * (capacity - 1)] + b"\0\0")
                truncated = capacity <= len(data) // 2
                self.set_error(122 if truncated else 0)
                result = capacity if truncated else len(data) // 2
                self.say(f"API GetModuleFileNameW capacity={capacity} result={result}")
                self.api_return(result, 12)
            elif name == "SetCurrentDirectoryW":
                directory = self.text_w(a)
                expected = str(ROOT.parent)
                if directory.rstrip("\\").lower() != expected.rstrip("\\").lower():
                    self.stop("Unsupported simulated current directory: " + directory)
                    return
                self.current_directory = directory
                self.say("API SetCurrentDirectoryW simulated only: " + directory)
                self.api_return(1, 4)
            elif name == "GetCurrentDirectoryW":
                data = (self.current_directory + "\0").encode("utf-16-le")
                needed = len(data) // 2
                if a >= needed:
                    uc.mem_write(self.u32(esp + 8), data)
                self.say(f"API GetCurrentDirectoryW capacity={a} needed={needed}")
                self.api_return(needed - 1 if a >= needed else needed, 8)
            elif name == "GetProcessHeap":
                self.say("API GetProcessHeap => synthetic heap 2f000001")
                self.api_return(0x2f000001, 0)
            elif name in ("HeapAlloc", "RtlAllocateHeap") and a == 0x2f000001:
                flags, requested = self.u32(esp + 8), self.u32(esp + 12)
                if flags & ~8:
                    self.stop("Unsupported heap flags")
                    return
                result = self.heap_allocate(requested)
                self.say(f"API {name} flags={flags:x} size={requested} => {result:08x}")
                self.api_return(result, 12)
            elif name in ("HeapFree", "RtlFreeHeap") and a == 0x2f000001:
                flags, pointer = self.u32(esp + 8), self.u32(esp + 12)
                if flags or pointer not in self.allocations:
                    self.stop("Unsupported heap-free request")
                    return
                uc.mem_unmap(pointer, self.allocations.pop(pointer))
                self.say(f"API {name} released {pointer:08x}")
                self.api_return(1, 12)
            elif name in ("AddVectoredExceptionHandler", "RtlAddVectoredExceptionHandler"):
                callback = self.u32(esp + 8)
                if not LOADER <= callback < LOADER_END:
                    self.stop("Unsupported vectored handler address")
                    return
                handle = 0x82000000 + len(self.vehs) + 1
                self.vehs.insert(0 if a else len(self.vehs), (handle, callback))
                self.say(f"API {name} => simulated registration {handle:08x} callback={callback:08x}")
                self.api_return(handle, 8)
            elif name in ("RemoveVectoredExceptionHandler", "RtlRemoveVectoredExceptionHandler"):
                old_count = len(self.vehs)
                self.vehs = [entry for entry in self.vehs if entry[0] != a]
                self.api_return(int(len(self.vehs) < old_count), 4)
            elif name in ("NtSetInformationThread", "ZwSetInformationThread") and a == 0xfffffffe:
                info_class, pointer, length = self.u32(esp + 8), self.u32(esp + 12), self.u32(esp + 16)
                if (info_class, pointer, length) != (17, 0, 0):
                    self.stop(f"Unsupported thread information {info_class}")
                    return
                self.say("API ThreadHideFromDebugger => simulated only, no host thread affected")
                self.api_return(0, 16)
            elif name in ("NtQueryInformationProcess", "ZwQueryInformationProcess") and a == 0xffffffff:
                info_class = self.u32(esp + 8)
                if info_class not in (7, 30, 31):
                    self.stop(f"Unsupported process information class {info_class}")
                    return
                length, returned = self.u32(esp + 16), self.u32(esp + 20)
                if returned:
                    uc.mem_write(returned, struct.pack("<I", 4))
                if length >= 4:
                    uc.mem_write(self.u32(esp + 12), struct.pack("<I", 1 if info_class == 31 else 0))
                status = 0xc0000353 if info_class == 30 else 0
                self.say(f"API {name} class={info_class} => isolated non-debugged process, status={status:08x}")
                self.api_return(status if length >= 4 else 0xc0000004, 20)
            elif name == "GetCommandLineA":
                command = ('"' + str(ROOT.parent / "Audition.exe") + '"\0').encode("ascii")
                uc.mem_write(0x7ffdd000, command)
                self.say("API GetCommandLineA => quoted client path, no login arguments")
                self.api_return(0x7ffdd000, 0)
            elif name == "GetVersion":
                self.say("API GetVersion => synthetic NT 10.0 build 19045")
                self.api_return((19045 << 16) | 10, 0)
            elif name == "GetUserDefaultUILanguage":
                self.say("API GetUserDefaultUILanguage => synthetic en-US 0409")
                self.api_return(0x409, 0)
            elif name == "OpenThread" and self.u32(esp + 12) == 1:
                self.handles.add(0x81000001)
                self.say("API OpenThread => synthetic current-thread handle 81000001")
                self.api_return(0x81000001, 12)
            elif name == "GetThreadContext" and a in (0xfffffffe, 0x81000001):
                context = self.u32(esp + 8)
                if self.u32(context) != 0x10010:
                    self.stop("Only debug-register GetThreadContext currently modeled")
                    return
                uc.mem_write(context + 4, bytes(24))
                self.say("API GetThreadContext => inactive OS thread debug context (all zero, not CPU reset values)")
                self.api_return(1, 8)
            elif name in ("GetCurrentThread", "GetCurrentProcess"):
                result = 0xfffffffe if name == "GetCurrentThread" else 0xffffffff
                self.say(f"API {name} => pseudo handle {result:08x}")
                self.api_return(result, 0)
            elif name == "GetCurrentThreadId":
                self.say("API GetCurrentThreadId => synthetic thread 1")
                self.api_return(1, 0)
            elif name in ("RtlInitializeCriticalSection", "InitializeCriticalSection"):
                uc.mem_write(a, struct.pack("<IiIIII", 0, -1, 0, 0, 0, 0))
                self.say(f"API {name}({a:08x}) single-thread initialization")
                self.api_return(0, 4)
            elif name == "GetProcAddress" and a in self.exports:
                p = self.u32(esp + 8)
                text = "#" + str(p) if p <= 0xffff else self.text(p)
                target = self.exports[a].get(text)
                self.say(f"API GetProcAddress({a:08x}, {text!r}) => {target}")
                if target is None:
                    self.stop("Missing export")
                    return
                self.api_return(target, 8)
            else:
                stack = bytes(uc.mem_read(esp, 48)).hex()
                self.stop(f"Unsupported API {name} at {address:08x} stack={stack}")
        except (ValueError, OSError, UcError, UnicodeError) as error:
            self.stop(f"API model error {error!r}")

    def rdtsc(self, uc, address, size, _):
        self.ticks += 1000
        uc.reg_write(UC_X86_REG_EAX, self.ticks & 0xffffffff)
        uc.reg_write(UC_X86_REG_EDX, self.ticks >> 32)
        uc.reg_write(UC_X86_REG_EIP, address + 2)
        self.say(f"MODEL_RDTSC {self.ticks} pc={address:08x}")

    def block(self, uc, address, size, _):
        self.blocks += 1
        if self.blocks % 100000 == 0:
            self.say(f"PROGRESS blocks={self.blocks} seconds={time.monotonic() - self.started:.1f} pc={address:08x} state={self.state()}")
            if time.monotonic() - self.started > 600 or self.blocks >= 2000000000:
                self.stop("Research time/block budget")
        if address == 0x3fda6ef:
            if self.dispatches < 10:
                self.say(f"DISPATCH {self.dispatches} state={self.state()}")
            self.dispatches += 1
        if not LOADER <= address < LOADER_END and address not in self.apis:
            self.stop(f"Outside recovered loader at {address:08x}")

    def invalid_memory(self, uc, access, address, size, value, _):
        self.say(f"MEMORY_FAULT access={access} address={address:08x} size={size} pc={uc.reg_read(UC_X86_REG_EIP):08x}")
        return False

    def interrupt(self, uc, number, _):
        self.stop(f"Unmodeled interrupt {number}")

    def run(self):
        root = ROOT
        source = root.parent / "Audition.exe"
        disk = source.read_bytes()
        if hashlib.sha256(disk).hexdigest() != "f39a8fad7513f5b30a78bdc8bf82fd48e0bdff4f7940ee7b0a7dabd1eace47eb":
            raise ValueError("Different client; re-derive loader layout first")
        self.source_bytes = disk
        loader = (root / "diagnostics/bootstrap-static/bootstrap.bin").read_bytes()
        if hashlib.sha256(loader).hexdigest() != "51fa7878edf62a901a62e2c0589b5f8e9c360bc6755bd18a7ba267438cbec882":
            raise ValueError("Different bootstrap")
        pe: Any = pefile.PE(data=disk)
        image = pe.get_memory_mapped_image()
        self.uc.mem_map(BASE, aligned(pe.OPTIONAL_HEADER.SizeOfImage))
        self.uc.mem_write(BASE, image)
        self.uc.mem_write(LOADER, loader)
        self.thread_environment()
        self.uc.reg_write(UC_X86_REG_ESP, 0x20010000)
        self.uc.reg_write(UC_X86_REG_EFLAGS, 0x202)
        self.uc.reg_write(UC_X86_REG_EIP, ENTRY)
        self.uc.mem_map(0x10000000, 0x1000)
        self.uc.mem_map(0x2d000000, 0x1000)
        self.uc.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, self.exception_access, begin=0x2d000000, end=0x2d0004cb)
        self.apis[VEH_RETURN] = "__VEH_RETURN"
        self.uc.mem_write(VEH_RETURN, b"\xc3")
        index = 0
        for library in pe.DIRECTORY_ENTRY_IMPORT:
            for entry in library.imports:
                stub = 0x10000000 + index * 16
                index += 1
                if index > 256:
                    raise ValueError("Unexpected import count")
                self.apis[stub] = entry.name.decode("ascii") if entry.name else "#" + str(entry.ordinal)
                self.uc.mem_write(stub, b"\xc3")
                self.uc.mem_write(entry.address, struct.pack("<I", stub))
        self.uc.hook_add(UC_HOOK_CODE, self.on_api, begin=0x10000000, end=0x10000fff)
        self.module("kernel32.dll")
        assert bytes(self.uc.mem_read(0x3e1f70a, 2)) == b"\x0f\x31"
        for match in re.finditer(b"\x0f\x31", loader):
            address = LOADER + match.start()
            self.uc.hook_add(UC_HOOK_CODE, self.rdtsc, begin=address, end=address)
            self.rdtsc_hooks.append(address)
        self.uc.hook_add(UC_HOOK_BLOCK, self.block)
        self.uc.hook_add(UC_HOOK_INTR, self.interrupt)
        self.uc.hook_add(UC_HOOK_MEM_INVALID, self.invalid_memory)
        self.emulate_from(ENTRY)
        if hashlib.sha256(source.read_bytes()).digest() != hashlib.sha256(disk).digest():
            raise ValueError("Source changed during research")
        return bytes(self.uc.mem_read(BASE, aligned(pe.OPTIONAL_HEADER.SizeOfImage)))

    def emulate_from(self, entry):
        try:
            self.uc.emu_start(entry, 0, timeout=610000000)
            if self.reason is None:
                self.reason = "Emulator stopped/timeout"
        except UcError as error:
            self.reason = str(error)
            self.say("ERROR " + self.reason)


def self_test():
    import io
    probe = Probe(io.StringIO())
    probe.thread_environment()
    probe.uc.mem_map(0x10000, 0x2000)
    probe.uc.mem_write(0x10000, b"kernel32.dll\0")
    assert probe.text(0x10000) == "kernel32.dll"
    assert probe.accessible(0x10000, 0x2000, UC_PROT_READ)
    assert not probe.accessible(0x11000, 0x1001, UC_PROT_READ)
    assert probe.accessible(0, 0, UC_PROT_READ)
    assert not probe.accessible(0xfffffff8, 16, UC_PROT_READ)
    probe.uc.mem_write(0x10100, bytes.fromhex("64a10000000050"))
    probe.uc.reg_write(UC_X86_REG_ESP, 0x11100)
    probe.uc.emu_start(0x10100, 0x10107)
    assert probe.uc.reg_read(UC_X86_REG_EAX) == 0xffffffff
    assert probe.uc.reg_read(UC_X86_REG_ESP) == 0x110fc
    assert probe.u32(0x110fc) == 0xffffffff
    probe.uc.reg_write(UC_X86_REG_ESP, 0x11000)
    probe.uc.mem_write(0x11000, struct.pack("<III", 0x1234, 0x70000000, 0x10020))
    probe.uc.mem_write(0x10020, b"GetProcAddress\0")
    probe.apis[0x5000] = "GetProcAddress"
    probe.exports[0x70000000] = {"GetProcAddress": 0x7777}
    probe.on_api(probe.uc, 0x5000, 1, None)
    assert probe.uc.reg_read(UC_X86_REG_EIP) == 0x1234
    assert probe.uc.reg_read(UC_X86_REG_ESP) == 0x1100c
    assert probe.uc.reg_read(UC_X86_REG_EAX) == 0x7777
    probe.uc.reg_write(UC_X86_REG_ESP, 0x11000)
    probe.uc.mem_write(0x11000, struct.pack("<II", 0x1234, 0x10100))
    probe.apis[0x5000] = "RtlInitializeCriticalSection"
    probe.on_api(probe.uc, 0x5000, 1, None)
    assert struct.unpack("<IiIIII", probe.uc.mem_read(0x10100, 24)) == (0, -1, 0, 0, 0, 0)
    probe.uc.reg_write(UC_X86_REG_ESP, 0x11000)
    probe.uc.mem_write(0x11000, struct.pack("<IIII", 0x1234, 0x2f000001, 8, 123))
    probe.apis[0x5000] = "RtlAllocateHeap"
    probe.on_api(probe.uc, 0x5000, 1, None)
    assert probe.uc.reg_read(UC_X86_REG_EAX) == 0x30000000
    assert bytes(probe.uc.mem_read(0x30000000, 123)) == bytes(123)
    probe.uc.reg_write(UC_X86_REG_ESP, 0x11000)
    probe.uc.mem_write(0x11000, struct.pack("<4I", 0x1234, 0x2f000001, 0, 0x30000000))
    probe.apis[0x5000] = "RtlFreeHeap"
    probe.on_api(probe.uc, 0x5000, 1, None)
    assert probe.uc.reg_read(UC_X86_REG_EAX) == 1
    assert 0x30000000 not in probe.allocations
    probe.uc.reg_write(UC_X86_REG_ESP, 0x11000)
    probe.uc.mem_write(0x11000, struct.pack("<III", 0x1234, 0, 0))
    probe.apis[0x5000] = "GetCurrentDirectoryW"
    probe.on_api(probe.uc, 0x5000, 1, None)
    assert probe.uc.reg_read(UC_X86_REG_EAX) > 1
    for suffix, needed, encoding in (("", 296, "ascii"), ("W", 556, "utf-16-le")):
        probe.uc.reg_write(UC_X86_REG_ESP, 0x11000)
        probe.uc.mem_write(0x11000, struct.pack("<III", 0x1234, 2, 0))
        probe.apis[0x5000] = "CreateToolhelp32Snapshot"
        probe.on_api(probe.uc, 0x5000, 1, None)
        handle = probe.uc.reg_read(UC_X86_REG_EAX)
        for operation, pid in (("First", 0), ("Next", 1), ("Next", None), ("First", 0)):
            probe.uc.reg_write(UC_X86_REG_ESP, 0x11000)
            probe.uc.mem_write(0x11000, struct.pack("<III", 0x1234, handle, 0x10200))
            probe.uc.mem_write(0x10200, struct.pack("<I", needed))
            probe.apis[0x5000] = "Process32" + operation + suffix
            probe.on_api(probe.uc, 0x5000, 1, None)
            assert probe.uc.reg_read(UC_X86_REG_EAX) == int(pid is not None)
            if pid is None:
                assert probe.last_error == 18
                assert probe.u32(0x7ffde034) == 18
            else:
                assert probe.u32(0x10208) == pid
                expected = "Audition.exe" if pid else "System Idle Process"
                assert bytes(probe.uc.mem_read(0x10224, len(expected.encode(encoding)))) == expected.encode(encoding)
    probe.uc.mem_map(0x2d000000, 0x1000)
    probe.uc.reg_write(UC_X86_REG_ESP, 0x11000)
    probe.uc.mem_write(0x11000, struct.pack("<5I", 0x1234, 0xc000008e, 0, 0, 0))
    probe.vehs = [(0x82000001, 0x10180)]
    probe.start_exception(0x11000, 0x5000)
    assert probe.uc.reg_read(UC_X86_REG_EIP) == 0x10180
    assert probe.u32(0x2d000200 + 0xb8) == 0x1234
    probe.uc.mem_write(0x2d000200 + 0xb8, struct.pack("<I", 0x2222))
    probe.uc.reg_write(UC_X86_REG_EAX, 0xffffffff)
    probe.veh_return()
    assert probe.exception is None
    assert probe.uc.reg_read(UC_X86_REG_EIP) == 0x2222
    assert probe.uc.reg_read(UC_X86_REG_ESP) == 0x11014
    probe.source_bytes = b"MZfixture"
    path = str(Path(probe.current_directory, "Audition.exe"))
    probe.uc.mem_write(0x10200, path.encode("utf-16-le") + b"\0\0")
    probe.apis[0x5000] = "CreateFileW"
    for access in (0x80000000, 0x40000000):
        probe.uc.reg_write(UC_X86_REG_ESP, 0x11000)
        probe.uc.mem_write(0x11000, struct.pack("<8I", 0x1234, 0x10200, access, 1, 0, 3, 0x20, 0))
        probe.on_api(probe.uc, 0x5000, 1, None)
        if access == 0x80000000:
            handle = probe.uc.reg_read(UC_X86_REG_EAX)
            assert handle in probe.files and probe.files[handle] == 0
            assert probe.uc.reg_read(UC_X86_REG_ESP) == 0x11020
            probe.uc.reg_write(UC_X86_REG_ESP, 0x11000)
            probe.uc.mem_write(0x11000, struct.pack("<7I", 0x1234, handle, 0, 2, 0, 0, 0))
            probe.apis[0x5000] = "CreateFileMappingW"
            probe.on_api(probe.uc, 0x5000, 1, None)
            mapping = probe.uc.reg_read(UC_X86_REG_EAX)
            assert probe.mappings[mapping] == b"MZfixture"
            probe.uc.reg_write(UC_X86_REG_ESP, 0x11000)
            probe.uc.mem_write(0x11000, struct.pack("<6I", 0x1234, mapping, 4, 0, 0, 0))
            probe.apis[0x5000] = "MapViewOfFile"
            probe.on_api(probe.uc, 0x5000, 1, None)
            view = probe.uc.reg_read(UC_X86_REG_EAX)
            assert bytes(probe.uc.mem_read(view, 9)) == b"MZfixture"
            assert probe.accessible(view, 9, UC_PROT_READ)
            assert not probe.accessible(view, 9, UC_PROT_WRITE)
            probe.uc.reg_write(UC_X86_REG_ESP, 0x11000)
            probe.uc.mem_write(0x11000, struct.pack("<III", 0x1234, handle, 0x10280))
            probe.apis[0x5000] = "GetFileSize"
            probe.on_api(probe.uc, 0x5000, 1, None)
            assert probe.uc.reg_read(UC_X86_REG_EAX) == 9 and probe.u32(0x10280) == 0
            for closing in (handle, mapping):
                probe.uc.reg_write(UC_X86_REG_ESP, 0x11000)
                probe.uc.mem_write(0x11000, struct.pack("<II", 0x1234, closing))
                probe.apis[0x5000] = "CloseHandle"
                probe.on_api(probe.uc, 0x5000, 1, None)
                assert probe.uc.reg_read(UC_X86_REG_EAX) == 1
            assert bytes(probe.uc.mem_read(view, 9)) == b"MZfixture"
            probe.uc.reg_write(UC_X86_REG_ESP, 0x11000)
            probe.uc.mem_write(0x11000, struct.pack("<II", 0x1234, view))
            probe.apis[0x5000] = "UnmapViewOfFile"
            probe.on_api(probe.uc, 0x5000, 1, None)
            assert probe.uc.reg_read(UC_X86_REG_EAX) == 1
            assert view not in probe.mapping_views and not probe.accessible(view, 1, UC_PROT_READ)
            probe.apis[0x5000] = "CreateFileW"
        else:
            assert probe.reason == "Unsupported file-open request; only verified client bytes may be read"
    print("Self-check passed: FS/stack, strings, stdcall, critical section, heap, path bounds, process enumeration A/W, VEH context roundtrip, read-only file boundary.")


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        self_test()
    elif len(sys.argv) == 2:
        destination = Path(sys.argv[1])
        destination.mkdir(parents=True, exist_ok=False)
        with (destination / "trace.txt").open("x", encoding="utf-8") as log:
            probe = Probe(log)
            image = probe.run()
            (destination / "image.bin").write_bytes(image)
            (destination / "stack.bin").write_bytes(bytes(probe.uc.mem_read(0x20000000, 0x20000)))
            (destination / "teb.bin").write_bytes(bytes(probe.uc.mem_read(0x7ffde000, 0x2000)))
            for label, allocations in (("heap", probe.allocations), ("virtual", probe.virtual_allocations)):
                for address, length in allocations.items():
                    (destination / f"{label}-{address:08x}.bin").write_bytes(bytes(probe.uc.mem_read(address, length)))
            (destination / "state.json").write_text(json.dumps({"stop_reason": probe.reason, "blocks": probe.blocks, "state": probe.state()}, indent=2) + "\n", encoding="utf-8")
        print(probe.reason, probe.state())
    else:
        raise SystemExit(__doc__)
