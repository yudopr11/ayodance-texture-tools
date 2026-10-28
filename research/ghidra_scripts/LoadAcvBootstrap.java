// Load the statically decompressed bootstrap into the analysis database only.
// @category ACV

import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.HexFormat;

import ghidra.app.script.GhidraScript;
import ghidra.program.model.mem.MemoryBlock;

public class LoadAcvBootstrap extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 1) throw new IllegalArgumentException("Expected bootstrap.bin path");
        byte[] data = Files.readAllBytes(Path.of(args[0]));
        String hash = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(data));
        if (!hash.equals("51fa7878edf62a901a62e2c0589b5f8e9c360bc6755bd18a7ba267438cbec882")) {
            throw new IllegalArgumentException("Not the verified bootstrap image");
        }
        MemoryBlock block = currentProgram.getMemory().getBlock(".themida");
        if (block == null || block.isInitialized() || block.getSize() != data.length ||
                block.getStart().getOffset() != 0x03dd4000L) {
            throw new IllegalArgumentException("Unexpected target section; refusing to replace it");
        }
        currentProgram.getMemory().convertToInitialized(block, (byte) 0);
        currentProgram.getMemory().setBytes(block.getStart(), data);
        var entry = toAddr(0x03f7c7f4L);
        if (!disassemble(entry)) throw new IllegalStateException("Loader entry did not disassemble");
        if (createFunction(entry, "bootstrap_loader_entry") == null) {
            throw new IllegalStateException("Loader entry function was not created");
        }
        addEntryPoint(entry);
        println("Loaded verified raw bootstrap into project ONLY: " + entry);
    }
}
