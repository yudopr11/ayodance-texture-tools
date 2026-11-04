"""CPU-only compiled mapping-policy tests and native-reader replay; no host DLL loading.

Requires renderer_patch/build.py and the retained unicorn/pefile dependency snapshot.
Native crypto, archive metadata lookup and Win32 services remain modeled.
"""
import hashlib
import json
import struct
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path

import pefile
from unicorn import (
    UC_ERR_READ_UNMAPPED,
    UC_HOOK_CODE,
    UC_HOOK_MEM_READ,
    UC_HOOK_MEM_WRITE,
    UcError,
)
from unicorn.x86_const import (
    UC_X86_REG_EAX,
    UC_X86_REG_EBP,
    UC_X86_REG_EDI,
    UC_X86_REG_EIP,
    UC_X86_REG_ESP,
)

from acv_tools.acv_crypto import MT19937, crypt
from acv_tools.acv_pack import encode_index
from acv_tools.acv_unpack import decode_entry, read_index

from .emulate_archive_open import Reader
from .emulate_native_inflate import image_bytes

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "renderer_patch/build/archive_io_test.exe"
DLL = ROOT / "renderer_patch/build/archive_io_emulator.dll"


def prefix(path, number):
    result = subprocess.run([str(EXE), "--prefix", str(number), str(path)],
                            check=False, capture_output=True, text=True, timeout=10)
    return result.returncode, json.loads(result.stdout)


class CandidateReader(Reader):
    """Execute the compiled active thunks inside Unicorn, never in the host process."""
    def __init__(self, row, failure=None):
        super().__init__(image_bytes(), [row], 0, patched=True)
        self.failure, self.error, self.error_cleanup = failure, 0, False
        self.index_wire, self.position = None, 0x13579
        self.pending_reads = {}
        self.index_tag: int | None = None
        self.view_lengths = {}
        self.index_callbacks = {}
        for i, (slot, count) in enumerate(((0xD440D4,2),(0xD440D8,1))):
            address = 0x40003000+16*i
            self.put(slot,address)
            self.index_callbacks[address] = count
            self.uc.hook_add(UC_HOOK_CODE,self.callback,begin=address,end=address)
        self.put(0x3CF6914,0)
        pe = pefile.PE(str(DLL))
        header = getattr(pe, "OPTIONAL_HEADER", None)
        if header is None:
            raise ValueError("Missing emulator fixture PE optional header")
        base = header.ImageBase
        if base != 0x07000000:
            raise ValueError("Unexpected emulator fixture image base")
        self.uc.mem_map(base, (header.SizeOfImage + 4095) & ~4095)
        self.uc.mem_write(base, bytes(pe.get_memory_mapped_image()))
        export_table = getattr(pe, "DIRECTORY_ENTRY_EXPORT", None)
        if export_table is None:
            raise ValueError("Missing emulator fixture exports")
        exports = {e.name.decode().lstrip("_").split("@")[0]:base+e.address
                   for e in export_table.symbols if e.name}
        self.imports = {}
        for descriptor in getattr(pe, "DIRECTORY_ENTRY_IMPORT", ()):
            for imported in descriptor.imports:
                name = imported.name.decode() if imported.name else f"ordinal{imported.ordinal}"
                address = 0x40001000 + 16*len(self.imports)
                self.imports[address] = name
                self.put(imported.address, address)
                self.uc.hook_add(UC_HOOK_CODE, self.callback, begin=address, end=address)
        native = {name:address for address, (name, _) in self.apis.items()}
        self.call(exports["InitArchiveIoTest"], 0xCCB6F0, 0xCBFB76,
                  native["read"], native["map"], native["unmap"])
        read_thunk, map_thunk, scratch_thunk = (self.call(exports["ArchiveIoTestThunk"],i) for i in range(3))
        self.map_thunk, self.unmap_thunk = map_thunk, exports["UnmapArchiveIoTest"]
        self.put(0xD4413C, read_thunk)
        self.put(0xD4414C, map_thunk)
        self.put(0xD44150, exports["UnmapArchiveIoTest"])
        self.uc.mem_write(0x5AB705, b"\xe8"+struct.pack("<I", (scratch_thunk-0x5AB70A)&0xFFFFFFFF))
        for address in (0xCCB6F0, 0x5AB475, 0x5AB421):
            self.uc.hook_add(UC_HOOK_CODE, self.callback, begin=address, end=address)
        self.uc.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, self.bounds)

    def bounds(self, uc, _access, address, size, _value, _context):
        for base, logical in self.view_lengths.items():
            span = (logical+4095)&~4095
            if address < base+span and address+size > base and not (base <= address and address+size <= base+logical):
                raise AssertionError("Native/compiled access beyond mapped file extent")
        for base, (logical, span, _) in self.live.items():
            if address < base+span and address+size > base and not (base <= address and address+size <= base+logical):
                raise AssertionError(f"Native/compiled access outside logical allocation: {address:x}+{size}")
        # Released pages are still mapped in this model; do not mistake them for live allocations.
        for base, span in self.free_pages:
            if address < base+span and address+size > base:
                raise AssertionError("Native/compiled access to released allocation")

    def finish(self, result, pop):
        esp = self.uc.reg_read(UC_X86_REG_ESP)
        self.uc.reg_write(UC_X86_REG_EAX, result)
        self.uc.reg_write(UC_X86_REG_EIP, self.get(esp))
        self.uc.reg_write(UC_X86_REG_ESP, esp+pop)

    def callback(self, uc, address, size, _):
        esp = uc.reg_read(UC_X86_REG_ESP)
        def arg(n):
            return self.get(esp+4*(n+1))
        if self.index_tag is not None and address == 0xC39100:
            tag = self.index_tag
            if arg(4) != tag or tuple(arg(i) for i in (3,5,6,7)) != (0,int(tag<0xFF060000),int(tag>=0xFF060000),0):
                raise AssertionError("Native index crypto mode changed")
            data = crypt(bytes(uc.mem_read(arg(1),arg(2))),tag,header=True)
            if len(data) > self.live[arg(0)][0]:
                raise AssertionError("Native index crypto output exceeds scratch")
            uc.mem_write(arg(0),data)
            self.finish(len(data),4)
            return
        if address in self.index_callbacks:
            count = self.index_callbacks[address]
            if count == 2:
                if arg(0):
                    raise AssertionError("Unexpected index allocation flags")
                result = self.allocate(arg(1),"shuffle")
            else:
                self.release(arg(0))
                result = 1
            self.finish(result,4+count*4)
            return
        if address == 0x5AB421:
            if self.failure == "mismatch":
                self.put(esp+4, 0x20003000)  # Wrong request pointer; owned resource buffer stays valid.
            return
        if address == 0x5AB475:
            self.error_cleanup = True
            uc.emu_stop()
            return
        if address == 0xCCB6F0:
            result = 0 if self.failure == "allocation" else self.allocate(arg(0), "payload")
            self.finish(result, 4)
            return
        if address not in getattr(self, "imports", {}):
            return super().callback(uc, address, size, _)
        name = self.imports[address]
        if name == "VirtualQuery":
            pointer = arg(0)
            region = next(((start,end) for start,end,_ in uc.mem_regions() if start <= pointer <= end), None)
            if region is None:
                self.finish(0, 16)
                return
            start, end = region
            self.put(arg(1), start, start, 4, end-start+1, 0x1000, 4, 0x20000)
            self.finish(28, 16)
        elif name == "GetFileSizeEx":
            _, number = self.handles[arg(0)]
            uc.mem_write(arg(1), struct.pack("<Q", self.fixtures[number]["file_bytes"]))
            self.finish(1, 12)
        elif name == "ReOpenFile":
            if self.handles[arg(0)][0] != "file" or [arg(i) for i in (1,2,3)] != [0x80000000,7,0x50000000]:
                raise AssertionError("Expected private overlapped read-only reopen")
            if self.failure == "reopen":
                self.error = 5
                self.finish(0xFFFFFFFF,20)
            else:
                self.serial += 1
                self.handles[self.serial] = ("private",self.handles[arg(0)][1])
                self.finish(self.serial,20)
        elif name == "GetOverlappedResult":
            pending = self.pending_reads[arg(0)]
            if arg(1) != pending["operation"] or arg(3) != 1:
                raise AssertionError("Expected completion wait on private read")
            pending["completed"] = True
            if self.failure != "completion":
                uc.mem_write(pending["destination"],pending["data"])
                self.put(arg(2),len(pending["data"]))
            self.error = 1117 if self.failure == "completion" else 0
            self.finish(0 if self.failure == "completion" else 1,20)
        elif name == "CloseHandle":
            if self.handles[arg(0)][0] != "private":
                raise AssertionError("Compiled helper closed the native shared handle")
            pending = self.pending_reads.get(arg(0))
            if pending is not None and not pending["completed"]:
                raise AssertionError("Private handle closed with outstanding read")
            self.pending_reads.pop(arg(0),None)
            del self.handles[arg(0)]
            self.finish(1,8)
        elif name == "SetFilePointerEx":
            raise AssertionError("Compiled read changed the shared native file position")
        elif name == "SetLastError":
            self.error = arg(0)
            self.finish(0, 8)
        elif name == "GetLastError":
            self.finish(self.error, 4)
        elif name in ("memchr", "_memchr"):
            where = bytes(uc.mem_read(arg(0),arg(2))).find(bytes([arg(1)&255]))
            self.finish(0 if where < 0 else arg(0)+where, 4)  # cdecl
        elif name in ("AcquireSRWLockExclusive", "ReleaseSRWLockExclusive"):
            self.finish(0, 8)  # Single-thread model, not a concurrency proof.
        else:
            raise AssertionError(f"Unmodeled compiled import reached: {name}")

    def index_map(self, wire):
        self.index_wire = wire
        row = self.current = next(iter(self.fixtures.values()))
        number = row["number"]
        self.handles.update({0x1234:("file",number), 0x1235:("mapping",number)})
        self.put(0x3C2A080+4*number,0x1234)
        self.put(0x3C2A850+4*number,0x1235)
        self.uc.mem_write(0x119F9C8,b"\x01\x25")
        bp, sp = 0x1000FFF0, 0x1000F000
        self.put(bp-0x2F4,self.map_thunk)  # Actual cached-local call site, not an invented caller.
        self.put(sp,0x1235,4,0,0,0)
        self.uc.reg_write(UC_X86_REG_ESP,sp)
        self.uc.reg_write(UC_X86_REG_EBP,bp)
        self.uc.reg_write(UC_X86_REG_EDI,number)
        self.uc.emu_start(0x5A7ACF,0x5A7AD5,timeout=15_000_000,count=1_000_000)
        if self.uc.reg_read(UC_X86_REG_EIP) != 0x5A7AD5 or self.uc.reg_read(UC_X86_REG_ESP) != sp+20:
            raise AssertionError("Compiled index thunk did not preserve native stdcall boundary")
        self.index_wire = None
        return self.uc.reg_read(UC_X86_REG_EAX)

    def api(self, name, args):
        if self.index_wire is not None and name == "map":
            row = self.fixtures[self.handles[args[0]][1]]
            expected = row["file_bytes"] if row["number"] in (1,37) else len(self.index_wire)
            if args[1:] != [4,0,0,expected]:
                raise AssertionError("Compiled mapping extent or captured owner differs")
            span = (expected+4095)&~4095
            base = 0x50000000
            self.uc.mem_map(base,span)
            self.uc.mem_write(base,self.index_wire)
            if expected == row["file_bytes"]:
                self.uc.mem_write(base+row["offset"],row["stored"])
            self.views[base] = (row["number"],span)
            self.view_lengths[base] = expected
            self.events.append({"event":"candidate_map","bytes":expected})
            return base
        if name != "read":
            result = super().api(name, args)
            if name == "unmap" and result:
                self.view_lengths.pop(args[0],None)
            return result
        kind, number = self.handles[args[0]]
        row = self.fixtures[number]
        if kind != "private" or args[3] or not args[4] or self.get(args[4]+12) or self.get(args[4]+16):
            raise AssertionError("Expected explicit-offset read on a private handle")
        offset = self.get(args[4]+8)
        if self.index_wire is not None:
            data = self.index_wire[offset:offset+args[2]]
        else:
            if args[2] != len(row["stored"]) or offset != row["offset"]:
                raise AssertionError("Compiled hook did not request exact stored bytes/offset")
            data = row["stored"]
            if self.failure == "short":
                data = data[:-1]
            self.events.append({"event":"fallback_read", "bytes":args[2]})
        if self.failure == "read":
            self.error = 1117
            return 0  # A hard failure does not leave an asynchronous operation outstanding.
        completed = self.failure not in ("pending","completion")
        self.pending_reads[args[0]] = {"operation":args[4],"destination":args[1],
                                     "data":data,"completed":completed}
        if completed:
            self.uc.mem_write(args[1],data)
        self.error = 0 if completed else 997
        return int(completed)


def fixture(data=b"tinytxt", number=267, variant=5):
    tag = 0xFF000000 | variant<<16 | number
    packed = zlib.compress(data)
    alignment = 32 if variant == 6 else 8
    packed += bytes(-len(packed) % alignment)
    stored = crypt(packed,tag,decrypt=False)
    return {"number":number, "name":f"test{number}.slk", "decoded":data, "stored":stored,
            "fallback":stored[:len(data)], "tag":tag, "iv":None, "offset":4096, "file_bytes":4096+len(stored)}


class ArchiveIoTests(unittest.TestCase):
    def test_compiled_prefix_formats_and_bad_headers(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/"267.acv"
            for variant in (0,1,2,3,5,6):
                for count in (0,1,4,5,17):
                    tag = 0xFF000000 | variant<<16 | 267 if variant else 0
                    width = 176 if not tag else 212 if variant == 6 else 180
                    wire = encode_index(bytes(width*count),tag,count)
                    path.write_bytes(wire+b"PAYLOAD")
                    code, result = prefix(path,267)
                    self.assertEqual((code,result.get("prefix")),(0,len(wire)), (variant,count))
                    path.write_bytes(wire[:-1])
                    self.assertNotEqual(prefix(path,267)[0],0)
            for variant in (5,6):
                count, mask, length = 17, 0xA5639021, 256
                random = MT19937(count)
                for _ in range(4):
                    random.next()
                replacement = random.between(8,count*8-8)
                encoded = bytearray(length)
                struct.pack_into("<I",encoded,replacement,mask^length)
                wire = struct.pack("<4I",0xFF000000|variant<<16|267,count,(count|0x80000000)^mask,mask)+encoded
                path.write_bytes(wire)
                self.assertEqual(prefix(path,267),(0,{"status":"PASS","prefix":len(wire)}))
            for data in (b"", b"xxx", struct.pack("<2I",0xFF04010B,1),
                         struct.pack("<I",0x7FFFFFFF),struct.pack("<2I",0xFF010106,1)):
                path.write_bytes(data)
                self.assertNotEqual(prefix(path,267)[0],0)

    def test_active_compiled_read_scratch_and_native_destructor(self):
        for variant in (2,3,5,6):
            for number in (37,255,256,262,267,330,499):
                row = fixture(number=number,variant=variant)
                result = CandidateReader(row).run([number])
                self.assertEqual((result["status"],result["completed"]),("PASS",1))
                self.assertFalse(result["peak_view_bytes"])
        row = fixture(b"compressible data\0"*100)
        self.assertGreater(len(row["decoded"]),len(row["stored"]))
        self.assertEqual(CandidateReader(row).run([267])["status"],"PASS")

    def test_active_index_thunk_prefix_retained_reuse_and_budget_refusal(self):
        for number in (1,37,255,256,267,330,499):
            row = fixture(number=number)
            wire = encode_index(bytes(180),0xFF050000|number,1)
            reader = CandidateReader(row)
            base = reader.index_map(wire)
            self.assertEqual(base,0x50000000)
            if number in (1,37):
                reader.put(0x3C2B020+4*number,base)
                self.assertEqual(reader.run([number])["status"],"PASS")
                self.assertFalse(any(e["event"] == "fallback_read" for e in reader.events))
            self.assertEqual(reader.call(reader.unmap_thunk,base),1)
            self.assertFalse(reader.views)
        row = fixture(number=37)
        row["file_bytes"] = 257*1024*1024
        reader = CandidateReader(row)
        self.assertFalse(reader.index_map(encode_index(bytes(180),0xFF050025,1)))
        self.assertFalse(reader.views)  # No retained-view eviction or silent prefix downgrade.
        reader = CandidateReader(fixture())
        self.assertFalse(reader.index_map(struct.pack("<2I",0xFF04010B,1)))
        self.assertFalse(reader.views)

    def test_published_index_prefix_is_not_safe_for_resource_borrowers(self):
        wire = encode_index(bytes(180),0xFF05010B,1)
        reader = CandidateReader(fixture())
        base = reader.index_map(wire)
        reader.put(0x3C2B020+4*267,base)  # Same global pointer the native index initializer publishes.
        with self.assertRaises(UcError) as failure:
            reader.run([267])
        self.assertEqual(failure.exception.errno,UC_ERR_READ_UNMAPPED)
        self.assertFalse(any(e["event"] == "fallback_read" for e in reader.events))
        # After temporary-view cleanup, the same resource takes the bounded fallback.
        reader = CandidateReader(fixture())
        base = reader.index_map(wire)
        self.assertEqual(reader.call(reader.unmap_thunk,base),1)
        reader.put(0x3C2B020+4*267,0)
        self.assertEqual(reader.run([267])["status"],"PASS")
        self.assertTrue(any(e["event"] == "fallback_read" for e in reader.events))

    def test_decoded_resource_survives_retained_view_cleanup(self):
        row = fixture(data=b"decoded resource survives archive-view cleanup",number=37)
        reader = CandidateReader(row)
        base = reader.index_map(encode_index(bytes(180),row["tag"],1))
        reader.put(0x3C2B020+4*37,base)
        reader.current = row
        reader.uc.mem_write(0x20002000,row["name"].encode()+b"\0")
        reader.uc.mem_write(0x20002200,b"rb\0")
        resource = reader.call(0x5AAF60,0x20002000,0x20002200)
        self.assertTrue(resource)
        self.assertEqual(reader.get(resource+4),base+row["offset"])
        self.assertNotEqual(reader.get(resource+8),base+row["offset"])
        self.assertFalse(any(e["event"] == "fallback_read" for e in reader.events))
        # Only after open/decode returns: native read/seek/tell use the owned decoded buffer.
        reader.call(0x5AA540,37)
        self.assertFalse(reader.views)
        self.assertEqual(reader.get(0x3C2B020+4*37),0)
        self.assertEqual(reader.call(0x5ABA90,0x20004000,1,len(row["decoded"]),resource),len(row["decoded"]))
        self.assertEqual(bytes(reader.uc.mem_read(0x20004000,len(row["decoded"]))),row["decoded"])
        self.assertEqual(reader.call(0x5ABB50,resource),len(row["decoded"]))
        self.assertEqual(reader.call(0x5ABAF0,resource,0,0),0)
        self.assertEqual(reader.call(0x5ABB50,resource),0)
        self.assertEqual(reader.call(0x5AAF20,resource),row["decoded"][0])
        self.assertEqual(reader.call(0x5ABB50,resource),1)
        reader.call(0x5AAE90,resource)
        self.assertTrue(all(kind == "object" and logical == 24 for logical,_,kind in reader.live.values()))

    def test_native_encoded_parser_consumes_compiled_prefix_with_exact_bounds(self):
        for variant in (2,3,5,6):
            for count in (1,5):
                tag = 0xFF000000 | variant<<16 | 267
                records = b"".join(f"file{i}.dds".encode().ljust(160,b"\0")+
                                   struct.pack("<5I",0,16,7,i*16,tag)+(bytes(32) if variant==6 else b"")
                                   for i in range(count))
                wire = encode_index(records,tag,count)
                reader = CandidateReader(fixture(variant=variant))
                base = reader.index_map(wire)
                self.assertTrue(base)
                destination = 0x45000000
                reader.uc.mem_map(destination,4096)
                reader.put(0x20003000,destination,destination,destination+212*count)
                reader.put(0x20003100,8)
                reader.index_tag = tag
                result = reader.call(0x5A6250,tag,count,base,0x20003100,0x20003000,267,32 if variant==6 else 0)
                self.assertEqual(result&255,1)
                self.assertEqual(reader.get(0x20003100),len(wire))
                self.assertEqual(reader.get(0x20003004),destination+212*count)
                width = 212 if variant==6 else 180
                for i in range(count):
                    self.assertEqual(bytes(reader.uc.mem_read(destination+212*i,width)),records[width*i:width*(i+1)])
                self.assertFalse(reader.live)
                self.assertEqual(reader.call(reader.unmap_thunk,base),1)
                self.assertFalse(reader.views)

    def test_active_failure_clears_native_decode_gate_and_frees_resource(self):
        for failure in ("allocation","reopen","read","completion","short","mismatch"):
            reader = CandidateReader(fixture(),failure)
            row = reader.current = reader.fixtures[267]
            reader.uc.mem_write(0x20002000,row["name"].encode()+b"\0")
            reader.uc.mem_write(0x20002200,b"rb\0")
            with self.assertRaises(RuntimeError):
                reader.call(0x5AAF60,0x20002000,0x20002200)
            self.assertTrue(reader.error_cleanup, failure)
            self.assertTrue(all(kind == "object" and logical == 24 for logical,_,kind in reader.live.values()), failure)
            self.assertFalse(any(kind == "private" for kind,_ in reader.handles.values()), failure)
            self.assertFalse(reader.pending_reads, failure)
            self.assertEqual(reader.position,0x13579)

    def test_model_rejects_close_before_pending_completion(self):
        reader = CandidateReader(fixture())
        reader.handles[0x1234] = ("private",267)
        reader.pending_reads[0x1234] = {"completed":False}
        close = next(address for address,name in reader.imports.items() if name == "CloseHandle")
        with self.assertRaisesRegex(AssertionError,"outstanding read"):
            reader.call(close,0x1234)

    def test_pending_private_read_completes_without_touching_native_cursor(self):
        reader = CandidateReader(fixture(),"pending")
        self.assertEqual(reader.run([267])["status"],"PASS")
        self.assertFalse(any(kind == "private" for kind,_ in reader.handles.values()))
        self.assertFalse(reader.pending_reads)
        self.assertEqual(reader.position,0x13579)


def catalog_report(original, enhanced, report):
    """Read-only 660-archive prefix comparison; reported sizes are plans, not observed mappings."""
    rows = []
    with Path(report).open("x",encoding="utf-8") as output:
        try:
            for directory in (Path(original),Path(enhanced)):
                for number in range(1,331):
                    path = directory/f"{number:03d}.acv"
                    with path.open("rb") as stream:
                        read_index(stream)
                        expected = stream.tell()
                        stream.seek(0)
                        digest = hashlib.sha256(stream.read(expected)).hexdigest()
                    code, result = prefix(path,number)
                    if code or result.get("prefix") != expected:
                        raise AssertionError(f"Compiled index prefix differs: {path}")
                    planned = path.stat().st_size if number in (1,37) else expected
                    rows.append({"path":str(path),"prefix":expected,"index_sha256":digest,
                                 "planned_view_bytes":planned,"planned_page_charge":(planned+4095)&~4095})
        finally:
            json.dump({"status":"PASS" if len(rows)==660 else "INCOMPLETE", "rows":rows},output,indent=2)
    return {"status":"PASS","archives":len(rows)}


def fallback_catalog_report(original, enhanced, report):
    """Replay every real stored>raw entry through compiled hooks plus the native reader."""
    rows = []
    status = "INCOMPLETE"
    with Path(report).open("x",encoding="utf-8") as output:
        try:
            for directory in (Path(original),Path(enhanced)):
                for number in range(1,331):
                    path = directory/f"{number:03d}.acv"
                    with path.open("rb") as stream:
                        for entry in read_index(stream):
                            name, _, stored_size, raw, offset, tag, iv = entry
                            if stored_size <= raw:
                                continue
                            try:
                                decoded = decode_entry(stream,entry)
                            except ValueError as error:
                                if name.lower().endswith(".dds"):
                                    raise
                                rows.append({"path":str(path),"name":name,"raw":raw,"stored":stored_size,
                                             "status":"OPAQUE_NOT_REPLAYED","reason":str(error)})
                                continue
                            stream.seek(offset)
                            stored = stream.read(stored_size)
                            fixture_row = {"number":number,"name":name,"stored":stored,"decoded":decoded,
                                           "fallback":stored[:raw],"tag":tag,"iv":iv,"offset":offset,
                                           "file_bytes":path.stat().st_size}
                            result = CandidateReader(fixture_row).run([number])
                            if result["status"] != "PASS":
                                raise AssertionError(f"Compiled fallback failed for {path}: {name}")
                            rows.append({"path":str(path),"name":name,"raw":raw,"stored":stored_size,
                                         "decoded_sha256":hashlib.sha256(decoded).hexdigest(),"status":"PASS"})
            status = "COMPLETED_WITH_UNREPLAYED_OPAQUE" if any(r["status"] != "PASS" for r in rows) else "PASS"
        finally:
            json.dump({"status":status,"rows":rows},output,ensure_ascii=True,indent=2)
    return {"status":status,"resources":len(rows),"replayed":sum(r["status"] == "PASS" for r in rows)}


if __name__ == "__main__":
    unittest.main()
