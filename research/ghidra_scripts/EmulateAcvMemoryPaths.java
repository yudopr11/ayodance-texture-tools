// Bounded native instruction slices, not a whole-game or heap simulation.
// @category ACV
import java.io.PrintWriter;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.Arrays;
import java.util.HexFormat;
import ghidra.app.emulator.EmulatorHelper;
import ghidra.app.script.GhidraScript;

@SuppressWarnings("removal")
public class EmulateAcvMemoryPaths extends GhidraScript {
    private void step(EmulatorHelper emu) throws Exception {
        if (!emu.step(monitor)) throw new IllegalStateException(emu.getLastError());
    }
    private void runUntil(EmulatorHelper emu, long end, int budget) throws Exception {
        for (int i = 0; i < budget; i++) {
            if (emu.getExecutionAddress().getOffset() == end) return;
            step(emu);
        }
        throw new IllegalStateException("Instruction budget exceeded");
    }
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 1) throw new IllegalArgumentException("Expected NEW report path");
        byte[] expected = HexFormat.of().parseHex("558bec81ec0c010000a1a410c20333c5");
        byte[] actual = new byte[expected.length];
        currentProgram.getMemory().getBytes(toAddr(0xca2aa0L), actual);
        if (!Arrays.equals(actual, expected)) throw new IllegalArgumentException("Expected recovered v64 native image");
        try (PrintWriter out = new PrintWriter(Files.newBufferedWriter(Path.of(args[0]), StandardOpenOption.CREATE_NEW))) {
            out.println("ASSUMPTIONS: selected original instructions; synthetic stack/cache node/resource object; no OS, heap, server or Direct3D model. No host game execution. Does not establish failing-session reachability.");
            for (int id : new int[]{6, 74, 255, 256, 262, 330}) {
                EmulatorHelper emu = new EmulatorHelper(currentProgram);
                try {
                    long node = 0x20000000L, frame = 0x20010000L;
                    emu.writeMemory(toAddr(node), new byte[0x100]);
                    emu.writeMemory(toAddr(frame - 0x1000), new byte[0x2000]);
                    emu.writeMemoryValue(toAddr(node + 0x10), 4, id);
                    emu.writeRegister("ESI", node);
                    emu.writeRegister("EBP", frame);
                    emu.writeRegister("ESP", frame - 0x800);
                    emu.writeRegister("EAX", 0);
                    emu.writeRegister(emu.getPCRegister(), 0x5ab1abL);
                    // Stop before the real tree-erase call; preserve its unrelated modeled effect.
                    runUntil(emu, 0x5ab1baL, 12);
                    emu.writeRegister(emu.getPCRegister(), 0x5ab1c7L);
                    step(emu);
                    long actualId = emu.readRegister("ESI").longValue();
                    if (actualId != (id & 255)) throw new IllegalStateException("Unexpected eviction semantics");
                    out.printf("EVICTION candidate=%d cleanup_array_index=%d wrong_archive=%s%n", id, actualId, actualId != id);
                } finally { emu.dispose(); }
            }
            for (int sizeField : new int[]{0, 128, 1048576, 4194304, 67108864}) {
                EmulatorHelper emu = new EmulatorHelper(currentProgram);
                try {
                    long object = 0x20000000L, stack = 0x20010000L;
                    emu.writeMemory(toAddr(object), new byte[0x100]);
                    emu.writeMemory(toAddr(stack - 0x1000), new byte[0x2000]);
                    emu.writeMemoryValue(toAddr(object + 0xc), 4, sizeField);
                    emu.writeRegister("EDI", object);
                    emu.writeRegister("ESP", stack);
                    emu.writeRegister(emu.getPCRegister(), 0x5ab6ddL);
                    runUntil(emu, 0x5ab705L, 16);
                    long requested = emu.readRegister("EAX").longValue() & 0xffffffffL;
                    if (requested != (int)(sizeField * 1.2f)) throw new IllegalStateException("Unexpected scratch allocation");
                    out.printf("SCRATCH resource_size_field_0xc=%d allocator_request=%d%n", sizeField, requested);
                } finally { emu.dispose(); }
            }
            out.println("PASS: bounded p-code slices and explicit expected-result assertions; cache erase and allocator calls not executed.");
        }
    }
}
