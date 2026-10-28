"""Local-only loader research cache; do not load downloaded/untrusted caches.

python -m research.checkpoint_loader run diagnostics/checkpoint-v1
python -m research.checkpoint_loader resume diagnostics/checkpoint-v1 diagnostics/checkpoint-v2
python -m research.checkpoint_loader --self-test

Uses JSON/binary ZIP entries, not executable pickle. Native CPU contexts remain
build-specific trusted local data. Never launches the game or writes its files.
"""

import ctypes
import hashlib
import json
import struct
import sys
import tempfile
import time
import zipfile
from pathlib import Path

from unicorn import (
    UC_HOOK_BLOCK,
    UC_HOOK_CODE,
    UC_HOOK_INTR,
    UC_HOOK_MEM_INVALID,
    UC_HOOK_MEM_READ,
    UC_HOOK_MEM_WRITE,
)
from unicorn.unicorn_py3 import unicorn as implementation
from unicorn.x86_const import (
    UC_X86_REG_EAX,
    UC_X86_REG_EBX,
    UC_X86_REG_EIP,
    UC_X86_REG_ESP,
    UC_X86_REG_XMM0,
)

from .emulate_client_loader import BASE, LOADER, Probe

ROOT = Path(__file__).resolve().parents[1]
CLIENT_SHA256 = "f39a8fad7513f5b30a78bdc8bf82fd48e0bdff4f7940ee7b0a7dabd1eace47eb"
BOOTSTRAP_SHA256 = "51fa7878edf62a901a62e2c0589b5f8e9c360bc6755bd18a7ba267438cbec882"
INTEGER_KEY_MAPS = ("exports", "apis", "virtual_allocations", "allocations", "registry_keys", "snapshots", "files", "mapping_views")


def identity():
    client = hashlib.sha256((ROOT.parent / "Audition.exe").read_bytes()).hexdigest()
    loader = hashlib.sha256((ROOT / "diagnostics/bootstrap-static/bootstrap.bin").read_bytes()).hexdigest()
    if client != CLIENT_SHA256 or loader != BOOTSTRAP_SHA256:
        raise ValueError("Client/bootstrap identity changed; checkpoint cannot be reused")
    return {"schema": 1, "cpu": "x86-32-haswell", "pointer_bits": struct.calcsize("P") * 8,
            "unicorn": list(implementation.uc_version()), "native": hashlib.sha256(Path(implementation.uclib._name).read_bytes()).hexdigest(),
            "client": client, "bootstrap": loader}


def save(probe, directory):
    provenance = identity()
    if probe.source_bytes is None or hashlib.sha256(probe.source_bytes).hexdigest() != CLIENT_SHA256:
        raise ValueError("Checkpoint does not contain the verified source bytes")
    if any(data is not probe.source_bytes for data in probe.mappings.values()):
        raise ValueError("Unexpected mapping data; update checkpoint schema before caching")
    excluded = ("uc", "log", "started", "source_bytes", "mappings", "handles")
    attributes = {key: value for key, value in vars(probe).items() if key not in excluded}
    attributes["handles"] = sorted(probe.handles)
    attributes["mappings"] = list(probe.mappings)
    memory = list(probe.uc.mem_regions())
    cpu = probe.uc.context_save()
    manifest = {"identity": provenance, "attributes": attributes, "memory": memory,
                "cpu_size": cpu.size, "source_size": len(probe.source_bytes)}
    temporary = directory / "checkpoint.tmp"
    with zipfile.ZipFile(temporary, "x", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("cpu.bin", bytes(cpu))
        archive.writestr("source.bin", probe.source_bytes)
        for index, (lo, hi, _) in enumerate(memory):
            archive.writestr(f"memory/{index}.bin", bytes(probe.uc.mem_read(lo, hi - lo + 1)))
    temporary.replace(directory / "checkpoint.zip")


def read_blob(archive, name, size):
    if size < 0 or size > 128 * 1024 * 1024 or archive.getinfo(name).file_size != size:
        raise ValueError("Invalid checkpoint entry length: " + name)
    return archive.read(name)


def restore(directory, log, *, probe_type=Probe):
    expected = identity()
    probe = probe_type(log)
    with zipfile.ZipFile(directory / "checkpoint.zip") as archive:
        if archive.getinfo("manifest.json").file_size > 4 * 1024 * 1024:
            raise ValueError("Checkpoint manifest exceeds budget")
        try:
            manifest = json.loads(archive.read("manifest.json"))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError("Invalid checkpoint JSON manifest") from error
        if manifest["identity"] != expected:
            raise ValueError("Incompatible checkpoint/native Unicorn build")
        source = read_blob(archive, "source.bin", manifest["source_size"])
        if hashlib.sha256(source).hexdigest() != CLIENT_SHA256:
            raise ValueError("Checkpoint source bytes changed")
        attributes = manifest["attributes"]
        if set(attributes) != set(vars(probe)) - {"uc", "log", "started", "source_bytes"}:
            raise ValueError("Probe state fields changed; update checkpoint schema")
        for field in INTEGER_KEY_MAPS:
            attributes[field] = {int(key): value for key, value in attributes[field].items()}
        attributes["handles"] = set(attributes["handles"])
        attributes["mappings"] = dict.fromkeys(attributes["mappings"], source)
        vars(probe).update(attributes)
        probe.source_bytes = source
        probe.reason = None
        probe.started = time.monotonic()
        memory = manifest["memory"]
        if len(memory) > 1024 or sum(hi - lo + 1 for lo, hi, _ in memory) > 1024 * 1024 * 1024:
            raise ValueError("Checkpoint memory exceeds budget")
        for index, (lo, hi, permissions) in enumerate(memory):
            data = read_blob(archive, f"memory/{index}.bin", hi - lo + 1)
            probe.uc.mem_map(lo, len(data), permissions)
            probe.uc.mem_write(lo, data)
        cpu = probe.uc.context_save()
        if manifest["cpu_size"] != cpu.size:
            raise ValueError("Incompatible native CPU context size")
        raw = read_blob(archive, "cpu.bin", cpu.size)
        ctypes.memmove(cpu.context, raw, cpu.size)
        probe.uc.context_restore(cpu)
    for begin, end in probe.module_hooks:
        probe.uc.hook_add(UC_HOOK_CODE, probe.on_api, begin=begin, end=end)
    probe.uc.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, probe.exception_access, begin=0x2d000000, end=0x2d0004cb)
    probe.uc.hook_add(UC_HOOK_CODE, probe.on_api, begin=0x10000000, end=0x10000fff)
    for address in probe.rdtsc_hooks:
        probe.uc.hook_add(UC_HOOK_CODE, probe.rdtsc, begin=address, end=address)
    probe.uc.hook_add(UC_HOOK_BLOCK, probe.block)
    probe.uc.hook_add(UC_HOOK_INTR, probe.interrupt)
    probe.uc.hook_add(UC_HOOK_MEM_INVALID, probe.invalid_memory)
    return probe


def self_test():
    import io
    probe = Probe(io.StringIO())
    probe.thread_environment()
    probe.source_bytes = (ROOT.parent / "Audition.exe").read_bytes()
    probe.uc.mem_map(LOADER, 0x1000)
    probe.uc.mem_write(LOADER, bytes.fromhex("648b1d00000000b810000010ffe0"))  # FS:[0] -> EBX, jump to API.
    probe.uc.mem_map(0x10000000, 0x1000)
    probe.uc.mem_write(0x10000010, b"\xc3")
    probe.files[0x86000001] = 0
    probe.apis[0x10000010] = "GetFileSize"
    probe.apis[0x10000020] = "__checkpoint_test_stop"
    probe.uc.reg_write(UC_X86_REG_ESP, 0x20010000)
    probe.uc.mem_write(0x20010000, struct.pack("<III", 0x10000020, 0x86000001, 0))
    probe.uc.reg_write(UC_X86_REG_EIP, LOADER)
    probe.uc.reg_write(UC_X86_REG_XMM0, 0x123456789abcdef0fedcba9876543210)
    with tempfile.TemporaryDirectory(prefix="checkpoint-test-", dir=ROOT / "diagnostics") as temporary:
        directory = Path(temporary)
        save(probe, directory)
        resumed = restore(directory, io.StringIO())
        resumed.emulate_from(resumed.uc.reg_read(UC_X86_REG_EIP))
        assert resumed.uc.reg_read(UC_X86_REG_EAX) == len(probe.source_bytes)
        assert resumed.uc.reg_read(UC_X86_REG_EBX) == 0xffffffff
        assert resumed.uc.reg_read(UC_X86_REG_XMM0) == 0x123456789abcdef0fedcba9876543210
        assert resumed.reason and "__checkpoint_test_stop" in resumed.reason
    print("Checkpoint self-check passed: fresh engine, FS addressing, XMM state, memory and pending API replay.")


def main():
    if sys.argv[1:] == ["--self-test"]:
        self_test()
        return
    arguments = sys.argv[1:]
    if len(arguments) == 2 and arguments[0] == "run":
        source = None
    elif len(arguments) == 3 and arguments[0] == "resume":
        source = Path(arguments[1])
    else:
        raise SystemExit(__doc__)
    output = Path(arguments[-1])
    output.mkdir(parents=True, exist_ok=False)
    with (output / "trace.txt").open("x", encoding="utf-8") as log:
        if source is None:
            probe = Probe(log)
            image = probe.run()
        else:
            probe = restore(source, log)
            log.write(f"Resumed trusted local checkpoint {source}\n")
            probe.emulate_from(probe.uc.reg_read(UC_X86_REG_EIP))
            image = bytes(probe.uc.mem_read(BASE, 0x3fa3000))
        (output / "image.bin").write_bytes(image)
        save(probe, output)
        print(probe.reason, probe.state())


if __name__ == "__main__":
    main()
