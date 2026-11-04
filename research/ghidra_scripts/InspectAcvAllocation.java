// Read-only instruction evidence for native archive allocation, eviction and startup ordering.
// @category ACV
import java.io.PrintWriter;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.Arrays;
import java.util.HexFormat;
import ghidra.app.script.GhidraScript;

public class InspectAcvAllocation extends GhidraScript {
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 1) throw new IllegalArgumentException("Expected NEW report path");
        byte[] expected = HexFormat.of().parseHex("558bec81ec0c010000a1a410c20333c5");
        byte[] actual = new byte[expected.length];
        currentProgram.getMemory().getBytes(toAddr(0xca2aa0L), actual);
        if (!Arrays.equals(actual, expected))
            throw new IllegalArgumentException("Expected recovered v64 native image");
        try (PrintWriter out = new PrintWriter(Files.newBufferedWriter(Path.of(args[0]), StandardOpenOption.CREATE_NEW))) {
            for (long[] range : new long[][]{{0x5ab140L, 0x5ab2e0L}, {0x5ab6d0L, 0x5ab728L},
                    {0x5ab300L, 0x5ab365L}, {0x6ae34eL, 0x6ae4deL}, {0xcbb860L, 0xcbba70L}}) {
                if (range[0] == 0x6ae34eL || range[0] == 0xcbb860L) disassemble(toAddr(range[0]));
                out.println("RANGE " + Long.toHexString(range[0]) + ".." + Long.toHexString(range[1]));
                var instruction = getInstructionAt(toAddr(range[0]));
                if (instruction == null) instruction = getInstructionAfter(toAddr(range[0]));
                while (instruction != null && instruction.getAddress().getOffset() < range[1]) {
                    out.println(instruction.getAddress() + " " + instruction);
                    instruction = instruction.getNext();
                }
            }
            for (long address : new long[]{0xd48328L, 0xd45290L, 0xd440fcL, 0xd44100L, 0xd44154L, 0xd4414cL}) {
                byte[] data = new byte[16];
                currentProgram.getMemory().getBytes(toAddr(address), data);
                out.println("DATA " + Long.toHexString(address) + " " + HexFormat.of().formatHex(data));
            }
        }
    }
}
