// Load a verified offline memory image into an analysis project, never the source client.
// @category ACV

import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.Arrays;
import java.util.HexFormat;

import ghidra.app.script.GhidraScript;
import ghidra.program.model.mem.MemoryBlock;

public class LoadRecoveredClient extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 1) throw new IllegalArgumentException("Expected recovered image path");
        byte[] image = Files.readAllBytes(Path.of(args[0]));
        String hash = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(image));
        if (image.length != 66727936 || !hash.equals("47d0e6b9bcaa3008fa91bd86dd8d3b290aa8ee55bf3391d9cba0a6f43246a73d")) {
            throw new IllegalArgumentException("Not the verified v64 offline image");
        }
        if (currentProgram.getImageBase().getOffset() != 0x00400000L) {
            throw new IllegalArgumentException("Unexpected program base");
        }
        for (MemoryBlock block : currentProgram.getMemory().getBlocks()) {
            long offset = block.getStart().getOffset() - 0x00400000L;
            if (offset < 0 || offset + block.getSize() > image.length) continue;
            if (!block.isInitialized()) currentProgram.getMemory().convertToInitialized(block, (byte) 0);
            currentProgram.getMemory().setBytes(block.getStart(), Arrays.copyOfRange(image, (int) offset, (int) (offset + block.getSize())));
            // Only analyze native game code, not the already investigated protection VM.
            block.setExecute(block.getStart().getOffset() >= 0x00401000L && block.getEnd().getOffset() < 0x00d44000L);
            println("RECOVERED " + block.getName() + " " + block.getStart() + " bytes=" + block.getSize() + " execute=" + block.isExecute());
        }
    }
}
