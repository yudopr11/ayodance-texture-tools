// Bounded, isolated p-code interpretation of loader arithmetic. No host API calls.
// @category ACV

import java.io.File;
import java.io.DataOutputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.io.PrintWriter;
import java.util.HashSet;
import java.util.HexFormat;
import java.util.Set;
import java.util.Map;
import java.util.HashMap;
import java.util.Locale;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

import ghidra.app.emulator.EmulatorHelper;
import ghidra.app.script.GhidraScript;
import ghidra.app.util.PseudoDisassembler;
import ghidra.pcode.memstate.MemoryFaultHandler;
import ghidra.pcode.emulate.BreakCallBack;
import ghidra.pcode.pcoderaw.PcodeOpRaw;
import ghidra.program.model.address.Address;

@SuppressWarnings("removal")
public class TraceAcvBootstrap extends GhidraScript {
    private final Map<Long, String> apiNames = new HashMap<>();
    private final Map<String, Long> modules = new HashMap<>();
    private final Map<Long, Map<String, Long>> moduleExports = new HashMap<>();

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 1) throw new IllegalArgumentException("Expected NEW trace path");
        File output = new File(args[0]);
        if (!output.createNewFile()) throw new IllegalArgumentException("Trace already exists");
        try (PrintWriter out = new PrintWriter(output, "UTF-8")) {
            EmulatorHelper emu = new EmulatorHelper(currentProgram);
            try {
                emu.setMemoryFaultHandler(new MemoryFaultHandler() {
                    public boolean unknownAddress(Address address, boolean write) {
                        throw new IllegalStateException("Unmapped " + address + " write=" + write);
                    }
                    public boolean uninitializedRead(Address address, int size, byte[] bytes, int offset) {
                        throw new IllegalStateException("Uninitialized " + address + " size=" + size);
                    }
                });
                for (var reg : currentProgram.getLanguage().getRegisters()) {
                    if (reg.isBaseRegister() && !reg.isProcessorContext()) emu.writeRegister(reg, 0);
                }
                // Atomic locking has no effect in this isolated single-thread interpreter.
                BreakCallBack atomic = new BreakCallBack() {
                    public boolean pcodeCallback(PcodeOpRaw op) { return true; }
                };
                emu.registerCallOtherCallback("LOCK", atomic);
                emu.registerCallOtherCallback("UNLOCK", atomic);
                emu.registerCallOtherCallback("rdtsc", new BreakCallBack() {
                    private long ticks = 0x100000L;
                    public boolean pcodeCallback(PcodeOpRaw op) {
                        ticks += 1000;
                        emulate.getMemoryState().setValue(op.getOutput(), ticks);
                        out.println("MODEL_RDTSC " + ticks + " at " + emu.getExecutionAddress());
                        return true;
                    }
                });
                emu.writeRegister("EFLAGS", 0x202);
                emu.writeRegister("IF", 1);
                emu.writeRegister("ESP", 0x20010000);
                emu.writeMemory(toAddr(0x20000000L), new byte[0x20000]);
                emu.writeRegister(emu.getPCRegister(), 0x03f7c7f4L);
                loadModuleData("kernel32.dll", emu, out);
                emu.enableMemoryWriteTracking(true);
                out.println("ASSUMPTIONS: zeroed registers/stack, EFLAGS=0x202 and IF=1; single-thread; synthetic monotonic RDTSC; kernel32 export data and module/procedure lookup modeled; DLL instructions never interpreted.");
                PseudoDisassembler disassembler = new PseudoDisassembler(currentProgram);
                Set<Long> seen = new HashSet<>();
                int dispatches = 0;
                for (int step = 0; step < 50000000 && !monitor.isCancelled(); step++) {
                    Address pc = emu.getExecutionAddress();
                    long va = pc.getOffset();
                    String api = va == 0x039d100dL ? "GetModuleHandleA" : apiNames.get(va);
                    if (api != null) {
                        long arg = emu.readStackValue(4, 4, false).longValue();
                        if (api.equals("GetModuleHandleA") || api.equals("LoadLibraryA")) {
                            String name = arg == 0 ? "" : emu.readNullTerminatedString(toAddr(arg), 256).toLowerCase(Locale.ROOT);
                            out.println("MODEL_" + api + " name=" + name);
                            Long base = modules.get(name);
                            if (base == null && api.equals("LoadLibraryA")) base = loadModuleData(name, emu, out);
                            if ((arg == 0 && api.equals("LoadLibraryA")) || (arg != 0 && base == null)) {
                                out.println("STOP unsupported module lookup");
                                break;
                            }
                            apiReturn(emu, arg == 0 ? 0x00400000L : base, 4);
                            continue;
                        }
                        if (api.equals("GetProcAddress") && moduleExports.containsKey(arg)) {
                            long namePointer = emu.readStackValue(8, 4, false).longValue();
                            String name = namePointer <= 0xffff ? "#" + namePointer :
                                emu.readNullTerminatedString(toAddr(namePointer), 256);
                            Long target = moduleExports.get(arg).get(name);
                            out.println("MODEL_GetProcAddress name=" + name + " target=" + target);
                            if (target == null) {
                                out.println("STOP missing export");
                                break;
                            }
                            apiReturn(emu, target, 8);
                            continue;
                        }
                        out.println("STOP unsupported API " + api + " arg0=" + Long.toHexString(arg));
                        out.println("API_STACK=" + HexFormat.of().formatHex(emu.readMemory(toAddr(emu.readRegister("ESP").longValue()), 48)));
                        break;
                    }
                    if (va < 0x03dd4000L || va >= 0x0418a000L) {
                        out.println("STOP outside recovered loader: " + pc);
                        registers(out, emu);
                        break;
                    }
                    if (step % 100000 == 0) {
                        out.println("PROGRESS step=" + step + " pc=" + pc);
                        registers(out, emu);
                        out.println("VM_IP_LE=" + HexFormat.of().formatHex(emu.readMemory(toAddr(0x03f9afedL), 4)));
                        out.flush();
                    }
                    if (seen.add(va) && seen.size() <= 10000) {
                        var instruction = disassembler.disassemble(pc);
                        out.printf("%d %s %s%n", step, pc, instruction);
                        if (instruction == null) break;
                        String mnemonic = instruction.getMnemonicString();
                        if (mnemonic.equals("INT") || mnemonic.startsWith("SYS")) {
                            out.println("STOP: OS interaction is not modeled");
                            break;
                        }
                    }
                    if (va == 0x03fda6efL) {
                        out.println("DISPATCH " + dispatches + " at step=" + step);
                        registers(out, emu);
                        if (!emu.step(monitor)) {
                            out.println("ERROR " + emu.getLastError());
                            break;
                        }
                        out.println("DISPATCH_TARGET " + emu.getExecutionAddress());
                        dispatches++;
                        continue;
                    }
                    if (!emu.step(monitor)) {
                        out.println("ERROR step=" + step + " pc=" + pc + " " + emu.getLastError());
                        break;
                    }
                    if (step == 49999999) out.println("STOP instruction budget exhausted");
                }
                registers(out, emu);
                // Big-endian uint32 address/length pairs followed by raw changed bytes.
                Path changes = Path.of(args[0] + ".writes");
                try (DataOutputStream dump = new DataOutputStream(Files.newOutputStream(changes, StandardOpenOption.CREATE_NEW))) {
                    for (var range : emu.getTrackedMemoryWriteSet().getAddressRanges()) {
                        long start = range.getMinAddress().getOffset();
                        if (!range.getMinAddress().getAddressSpace().equals(currentProgram.getAddressFactory().getDefaultAddressSpace()) ||
                                start < 0x00400000L || start >= 0x0418a000L) continue;
                        int length = Math.toIntExact(range.getLength());
                        out.printf("WRITE_RANGE %08x bytes=%d%n", start, length);
                        dump.writeInt((int) start);
                        dump.writeInt(length);
                        dump.write(emu.readMemory(range.getMinAddress(), length));
                    }
                }
            } finally {
                emu.dispose();
            }
        }
        println("Isolated p-code trace: " + output.getAbsolutePath());
    }

    private void apiReturn(EmulatorHelper emu, long result, int argumentBytes) throws Exception {
        long caller = emu.readStackValue(0, 4, false).longValue();
        emu.writeRegister("EAX", result);
        emu.writeRegister("ESP", emu.readRegister("ESP").longValue() + 4 + argumentBytes);
        emu.writeRegister(emu.getPCRegister(), caller);
    }

    private int u32(byte[] bytes, int offset) {
        return ByteBuffer.wrap(bytes, offset, 4).order(ByteOrder.LITTLE_ENDIAN).getInt();
    }

    private String ascii(byte[] bytes, int start) {
        int end = start;
        while (end < bytes.length && end - start < 512 && bytes[end] != 0) end++;
        if (end >= bytes.length || bytes[end] != 0) throw new IllegalArgumentException("Invalid export string");
        return new String(bytes, start, end - start, StandardCharsets.US_ASCII);
    }

    private long loadModuleData(String name, EmulatorHelper emu, PrintWriter out) throws Exception {
        if (!name.matches("[a-z0-9_-]+\\.dll") || modules.size() >= 24) {
            throw new IllegalArgumentException("Unsupported module: " + name);
        }
        long base = 0x70000000L + modules.size() * 0x02000000L;
        Map<String, Long> exports = new HashMap<>();
        byte[] disk = Files.readAllBytes(Path.of("C:/Windows/SysWOW64/" + name));
        int pe = u32(disk, 60), optional = pe + 24;
        ByteBuffer fields = ByteBuffer.wrap(disk).order(ByteOrder.LITTLE_ENDIAN);
        if (fields.getShort(0) != 0x5a4d || u32(disk, pe) != 0x4550 || fields.getShort(optional) != 0x10b) {
            throw new IllegalArgumentException("Expected local PE32 DLL");
        }
        int size = u32(disk, optional + 56);
        if (size <= 0 || size > 32 * 1024 * 1024) throw new IllegalArgumentException("Invalid module size");
        byte[] image = new byte[size];
        int headers = u32(disk, optional + 60);
        System.arraycopy(disk, 0, image, 0, headers);
        int count = Short.toUnsignedInt(fields.getShort(pe + 6));
        int sectionTable = optional + Short.toUnsignedInt(fields.getShort(pe + 20));
        for (int i = 0; i < count; i++) {
            int section = sectionTable + i * 40;
            int rva = u32(disk, section + 12), rawSize = u32(disk, section + 16), raw = u32(disk, section + 20);
            System.arraycopy(disk, raw, image, rva, rawSize);
        }
        int directory = u32(image, optional + 96);
        int functions = u32(image, directory + 28), names = u32(image, directory + 32);
        int ordinals = u32(image, directory + 36), ordinalBase = u32(image, directory + 16);
        int functionCount = u32(image, directory + 20), nameCount = u32(image, directory + 24);
        ByteBuffer mapped = ByteBuffer.wrap(image).order(ByteOrder.LITTLE_ENDIAN);
        for (int i = 0; i < functionCount; i++) {
            int rva = u32(image, functions + 4 * i);
            if (rva != 0) exports.put("#" + (ordinalBase + i), base + rva);
        }
        for (int i = 0; i < nameCount; i++) {
            String symbol = ascii(image, u32(image, names + 4 * i));
            int ordinal = Short.toUnsignedInt(mapped.getShort(ordinals + 2 * i));
            long address = base + u32(image, functions + 4 * ordinal);
            exports.put(symbol, address);
            apiNames.put(address, symbol);
        }
        if (name.equals("kernel32.dll") && !exports.containsKey("GetProcAddress")) {
            throw new IllegalArgumentException("Missing expected export");
        }
        emu.writeMemory(toAddr(base), image);
        modules.put(name, base);
        moduleExports.put(base, exports);
        out.println("MODULE_DATA " + name + " base=" + Long.toHexString(base) + " bytes=" + size + " sha256=" +
            HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(disk)));
        return base;
    }

    private void registers(PrintWriter out, EmulatorHelper emu) {
        for (String reg : new String[]{"EIP", "EAX", "EBX", "ECX", "EDX", "ESI", "EDI", "EBP", "ESP"}) {
            out.printf("%s=%08x ", reg, emu.readRegister(reg).longValue() & 0xffffffffL);
        }
        out.println();
        out.flush();
    }
}
