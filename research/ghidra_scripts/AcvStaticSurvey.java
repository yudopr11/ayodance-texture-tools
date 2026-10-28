// Static ACV investigation report. Does not execute the imported program.
// @category ACV

import java.io.File;
import java.io.PrintWriter;
import java.util.LinkedHashSet;
import java.util.Locale;
import java.util.Set;

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Data;
import ghidra.program.model.listing.DataIterator;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.symbol.Symbol;

public class AcvStaticSurvey extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 1) {
            throw new IllegalArgumentException("Expected a NEW report path");
        }
        File output = new File(args[0]);
        if (!output.createNewFile()) {
            throw new IllegalArgumentException("Report already exists: " + output);
        }
        try (PrintWriter out = new PrintWriter(output, "UTF-8")) {
            out.println("PROGRAM " + currentProgram.getName());
            out.println("LANGUAGE " + currentProgram.getLanguageID());
            out.println("IMAGE_BASE " + currentProgram.getImageBase());
            out.println("FUNCTION_COUNT " + currentProgram.getFunctionManager().getFunctionCount());
            for (MemoryBlock b : currentProgram.getMemory().getBlocks()) {
                out.printf("BLOCK %s %s size=%d initialized=%s execute=%s%n",
                    b.getName(), b.getStart(), b.getSize(), b.isInitialized(), b.isExecute());
            }
            Set<Function> targets = new LinkedHashSet<>();
            var entries = currentProgram.getSymbolTable().getExternalEntryPointIterator();
            while (entries.hasNext()) {
                Address address = entries.next();
                out.println("ENTRY " + address);
                Function f = getFunctionAt(address);
                if (f != null) targets.add(f);
            }
            for (Symbol s : currentProgram.getSymbolTable().getExternalSymbols()) {
                out.println("EXTERNAL " + s.getName(true));
                for (var ref : getReferencesTo(s.getAddress())) {
                    out.println("  REF " + ref.getFromAddress());
                }
            }
            DataIterator data = currentProgram.getListing().getDefinedData(true);
            int stringHits = 0;
            while (data.hasNext() && !monitor.isCancelled()) {
                Data d = data.next();
                if (!d.hasStringValue()) continue;
                String text = String.valueOf(d.getValue());
                String lower = text.toLowerCase(Locale.ROOT);
                if (!lower.matches("(?s).*(acv|blowfish|mersenne|inflate|uncompress|readfile|virtualprotect|loadlibrary|themida|\\.dds).*")) continue;
                out.println("STRING " + d.getAddress() + " " + text);
                stringHits++;
                for (var ref : getReferencesTo(d.getAddress())) {
                    Function f = getFunctionContaining(ref.getFromAddress());
                    out.println("  STRING_REF " + ref.getFromAddress() + " " + f);
                    if (f != null && targets.size() < 12) targets.add(f);
                }
            }
            out.println("INTERESTING_STRINGS " + stringHits);
            FunctionIterator functions = currentProgram.getFunctionManager().getFunctions(true);
            int shown = 0;
            while (functions.hasNext() && shown++ < 100) {
                Function f = functions.next();
                out.println("FUNCTION " + f.getEntryPoint() + " " + f.getName() + " bytes=" + f.getBody().getNumAddresses());
                if (!f.isExternal() && targets.size() < 40) targets.add(f);
            }
            for (int depth = 0; depth < 32; depth++) {
                int before = targets.size();
                for (Function root : new LinkedHashSet<>(targets)) {
                    for (Function called : root.getCalledFunctions(monitor)) {
                        if (targets.size() < 40 && !called.isExternal()) targets.add(called);
                    }
                }
                if (targets.size() == before) break;
            }
            DecompInterface decompiler = new DecompInterface();
            try {
                if (!decompiler.openProgram(currentProgram)) throw new IllegalStateException(decompiler.getLastMessage());
                for (Function f : targets) {
                    if (monitor.isCancelled()) break;
                    out.println("\nTARGET " + f.getEntryPoint() + " " + f.getName());
                    InstructionIterator instructions = currentProgram.getListing().getInstructions(f.getBody(), true);
                    for (int i = 0; instructions.hasNext() && i < 120; i++) {
                        Instruction instruction = instructions.next();
                        out.println(instruction.getAddress() + " " + instruction);
                    }
                    DecompileResults result = decompiler.decompileFunction(f, 15, monitor);
                    if (result.decompileCompleted()) out.println(result.getDecompiledFunction().getC());
                    else out.println("DECOMPILE_FAILED " + result.getErrorMessage());
                }
            } finally {
                decompiler.dispose();
            }
        }
        println("Static report: " + output.getAbsolutePath());
    }
}
