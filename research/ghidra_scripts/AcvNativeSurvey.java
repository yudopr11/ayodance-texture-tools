// Targeted static decompilation of archive functions in the recovered native image.
// @category ACV

import java.io.File;
import java.io.PrintWriter;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HexFormat;
import java.util.LinkedHashSet;
import java.util.Set;

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.InstructionIterator;

public class AcvNativeSurvey extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 1) throw new IllegalArgumentException("Expected NEW report path, optional hex function roots");
        // The protected static project contains different bytes at the same addresses.
        byte[] expected = HexFormat.of().parseHex("558bec81ec0c010000a1a410c20333c5");
        byte[] actual = new byte[expected.length];
        currentProgram.getMemory().getBytes(toAddr(0x00ca2aa0L), actual);
        if (!Arrays.equals(expected, actual)) throw new IllegalArgumentException("Expected recovered v64 native image, not the protected static project");
        File output = new File(args[0]);
        if (!output.createNewFile()) throw new IllegalArgumentException("Report already exists");
        Set<Long> queued = new LinkedHashSet<>();
        if (args.length > 1) {
            for (int i = 1; i < args.length; i++) queued.add(Long.parseLong(args[i], 16));
        } else {
            for (long root : new long[]{0x5a6a30, 0x5a78f0, 0x5a9aa0, 0x5aa5e0, 0x5aaf60}) queued.add(root);
        }
        ArrayList<Long> pending = new ArrayList<>(queued);
        DecompInterface decompiler = new DecompInterface();
        try (PrintWriter out = new PrintWriter(output, "UTF-8")) {
            if (!decompiler.openProgram(currentProgram)) throw new IllegalStateException(decompiler.getLastMessage());
            for (int index = 0; index < pending.size() && index < 45 && !monitor.isCancelled(); index++) {
                Address entry = toAddr(pending.get(index));
                Function function = getFunctionAt(entry);
                if (function == null) {
                    disassemble(entry);
                    function = createFunction(entry, "acv_native_" + entry);
                }
                if (function == null) {
                    out.println("NO_FUNCTION " + entry);
                    continue;
                }
                out.println("\nTARGET " + entry + " " + function.getName());
                InstructionIterator instructions = currentProgram.getListing().getInstructions(function.getBody(), true);
                while (instructions.hasNext()) {
                    var instruction = instructions.next();
                    if (!instruction.getFlowType().isCall()) continue;
                    for (Address called : instruction.getFlows()) {
                        long address = called.getOffset();
                        out.println("CALL " + instruction.getAddress() + " -> " + called);
                        if (address >= 0x00401000L && address < 0x00d44000L && pending.size() < 45 && queued.add(address)) pending.add(address);
                    }
                }
                var result = decompiler.decompileFunction(function, 30, monitor);
                if (result.decompileCompleted()) out.println(result.getDecompiledFunction().getC());
                else out.println("DECOMPILE_FAILED " + result.getErrorMessage());
                out.flush();
            }
        } finally {
            decompiler.dispose();
        }
        println("Native archive report: " + output.getAbsolutePath());
    }
}
