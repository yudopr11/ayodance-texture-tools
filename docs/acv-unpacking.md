# AyoDance texture unpacking and repacking

The standalone tools now decode this installation's **plain format and tagged
formats 1, 2, 3, 5 and 6**. They do not launch or patch the game. Original archives
are opened read-only. Repacking creates a separate new file, not an in-place edit.

## Quick start

Run from your project root (for example, `C:/path/to/acv-unpacker-upscaler`).
This project uses its own `.venv`. All examples use PowerShell and untouched
archives under `original-data/DATA`, not already-enhanced installed game data:

```powershell
# Only needed to recreate the environment on another machine:
uv venv
uv pip install --python .venv/Scripts/python.exe -r requirements-acv.txt

# Use this environment for the remaining commands:
.\.venv\Scripts\Activate.ps1

# Extract one archive's DDS textures into a NEW texture-work/262 directory.
python -B -m acv_tools.acv_unpack original-data/DATA/262.acv --textures-only --output texture-work

# Optional: verify DDS decoding without extracting files.
python -B -m acv_tools.acv_unpack original-data/DATA/262.acv --textures-only

# After editing/upscaling some extracted DDS files, rebuild a separate archive.
# Create the staging directory first. Keep the archive's original numeric name.
mkdir staging
python -B -m acv_tools.acv_pack original-data/DATA/262.acv texture-work/262 staging/262.acv

# Check the rebuilt archive's DDS entries without installing it.
python -B -m acv_tools.acv_unpack staging/262.acv --textures-only

# Small synthetic regression tests.
python -B -m unittest tests.test_acv_unpack tests.test_acv_pack tests.test_stage_renderer_pilot tests.test_cli -v
python -B -m acv_tools.acv_crypto
```

If activation is blocked by PowerShell policy, replace `python` in each command
with `.\.venv\Scripts\python.exe`; no policy change or activation is necessary.
Unpacking/repacking require Python 3.10+ and the two dependencies in that requirements
file; the AI upscaler requires Python 3.11+. Use `-m acv_tools.<module>` rather than
executing files inside the package directly.
No Ghidra, emulator, memory dump, game process or commercial tool is needed to
run the completed unpacker/repacker.

A directory is also accepted as the unpacker's source; it processes `*.acv`
separately. Start with **one small archive**, not all 330: full extraction can
consume substantial disk space. Each archive gets a separate output subfolder.
JSON is printed to stdout; exit status 1 indicates an unsupported or failed input.
Existing extraction folders and output archives are refused. Failed operations
may leave partial output, explicitly reported by the CLI; do not install it.

The replacement folder can contain **only the files you changed**, using their
original relative paths. Alternatively, pass the whole extracted texture folder.
Files absent from that folder retain their original stored payload bytes. Unknown,
ambiguous, linked or duplicate replacement paths are rejected. The packer retains
the original entry order, names, metadata, entry tags and IVs, and recalculates
sizes and offsets. It uses the original archive as its format template. The CLI
refuses output into the template's directory; keep originals and staging separate.

## What is verified

- **330/330 original indexes** parsed: **233,158 entries**, including **159,003 DDS
  entries**. See `diagnostics/reports/native-index-scan-v2.json`.
- **984 real payloads** sampled across **all 330 archives** passed zlib checksum/EOF
  and exact-size validation, across all six formats. **933 DDS samples** also had
  the correct DDS magic. The three malformed non-texture records encountered in
  archives 242, 243 and 244 are explicitly logged, not silently treated as decoded.
  See `diagnostics/reports/native-payload-samples-v3.json`.
- Repack/read-back tests passed on real archives **018, 046, 053, 052, 328 and 262**,
  covering every local format. Replacement bytes matched after decoding; every
  unprovided stored payload matched byte-for-byte; source SHA-256s were unchanged.
  See `diagnostics/reports/real-pack-roundtrip-v1.json`.
- A complete texture-only extraction of `262.acv` produced **12 DDS files**
  (**3,278,496 decoded bytes**) in `diagnostics/texture-demo/262`.
- Synthetic tests cover all format variants, small/large entry counts, masked
  length fields, replacement preservation, overwrite refusal, filename collisions,
  size limits, invalid paths, corrupt streams and native cipher padding.

These are container/decoder checks, **not a claim that every DDS image was fully
rendered or that rebuilt archives were accepted by the running game**. Index
validation covers all entries; payload sampling is not an exhaustive verification
of all 233,158 payloads. These figures describe the initial container verification;
subsequent AI output and limited live renderer tests are documented separately in
the [project README](../README.md) and [renderer guide](../renderer_patch/README.md).
The full upscale batch is currently stopped; this is not a claim of 330 enhanced,
game-tested outputs.

## Important limits

Use `--textures-only` for the upscale workflow. Some archives contain deliberately
malformed-looking non-texture/decoy records, including impossible cipher alignment;
full-file extraction without that filter can fail on them. No guessing or silent
corruption is used to decode those records. The repacker preserves unprovided
records as opaque bytes, so texture replacement does not require decoding decoys.

Names are decoded as CP949 with surrogate escape for invalid legacy bytes, and
encoded losslessly when repacking. A few non-texture names therefore look odd.
Unsafe paths, Windows reserved names, traversal, case-insensitive duplicates and
file/directory collisions are rejected before creating an extraction folder.
Individual stored/decoded payloads and decoded indexes are limited to **64 MiB**.
This is a safety limit, not a proven native format limit. Crypto tags outside the
observed variants and archive/key IDs outside 0–499 are deliberately rejected.
Treat the working folder as trusted storage: do not let another process alter it
while extracting or packing. Pure-Python modern cipher operations can be slow
when processing the entire installation.

## Upscaling DDS safely

The ACV tools handle the container, not image enhancement. Export a DDS to a
lossless editable image with a DDS-capable tool, upscale it, then save it back as
DDS **with the same relative filename, appropriate compression, alpha and full
mipmap chain**. A PNG renamed to `.dds` is not a DDS texture.

Start with one non-UI texture and 2× scaling. Doubling both dimensions uses roughly
4× the texture memory. UI atlases can use hard-coded pixel coordinates; larger
textures may break layout even if packing is correct. The client may also enforce
texture-size, archive-integrity or online modification rules. Keep backups and
check the game's policy; no checks or anti-cheat mechanisms were bypassed in the
running client. Do not replace installed archives until you have independently
validated a staged copy and have a recovery plan.

### Automated UltraSharp staging

See the [UltraSharp setup and commands](../README.md#ultrasharpv2-comparison-cpu-reference)
for the optional CPU DAT backend. `--model` applies the selected 4× RGB DAT model
(e.g. UltraSharpV2) to all DDS textures, including UI, text and fonts. Final size
remains 2×, capped at 2048 on the longest side, with original alpha resized
independently. The abandoned PyTorch/DirectML experiment produced artifacts;
CPU reference output is not proof of GPU compatibility.

The workflow decodes with texconv, enhances RGB, restores original alpha and DDS
pixel format/mip policy, tags native dimensions, packs, then verifies replacement
bytes and untouched stored payloads. Cache entries are untagged; receipts include
tool/model/code/backend fingerprints. Keep one current output folder, `staging/`;
never mix models into it or clean it while a job is running. Tagged sources are
rejected: use `original-data/DATA`, not installed enhanced archives. Tiled DAT can
introduce seams, and AI may change small lettering; inspect comparison images and
use the compatible renderer shim before considering installation. No automatic
installation occurs.

## Recovered format and implementation

All integer fields are little-endian. A plain archive starts with a 32-bit count
and 176-byte records. Tagged archives start with `uint32 tag, uint32 count`, where
`tag = 0xff000000 | variant << 16 | archive_id`. Records have the same initial
176 bytes, followed by the entry's 32-bit crypto tag; variant 6 adds 32 bytes of
per-entry IV material, producing 212-byte records.

Common record fields:

```text
+0    filename[160], NUL-terminated
+160  uint32 preserved metadata
+164  uint32 stored payload size
+168  uint32 decoded payload size
+172  uint32 payload-relative offset
+176  uint32 entry crypto tag              (tagged records only)
+180  byte[32] IV material                 (variant 6 only)
```

Payload offsets are relative to the end of the stored index. Local payloads are
contiguous and end at EOF; the reader validates these bounds.

| Format | Index | Payload transform |
| --- | --- | --- |
| Plain | Raw 176-byte records | zlib |
| 1 | Raw 180-byte records | Standard Blowfish ECB, then zlib |
| 2 | 16-bit length, encrypted compressed records | Standard Blowfish ECB, then zlib |
| 3 | 16-bit length, encrypted compressed records | Custom-table Blowfish ECB, then zlib |
| 5 | Masked length/count, optional permutation, encrypted compressed records | Custom-table Blowfish ECB, then zlib |
| 6 | Same envelope, 212-byte decoded records | Rijndael-256 CBC, then zlib |

Index and payload keys differ. The legacy/custom Blowfish keys use a zero-filled
56-byte key buffer. Custom P/S tables come from the game's exact MT19937 bounded
sampling and sparse permutation table; treating the sparse table as consecutive
primes does not work. Rijndael uses **256-bit blocks**, not AES's 128-bit blocks.
The payload factory ignores its nominal supplied key and selects a separate
zero-padded default key; its IV comes from the entry, not the index's fixed IV.

For variants 5/6, the count seeds MT19937. The masked count's high bit enables
index shuffling; that choice consumes random draws before selecting the embedded
length-field position. The original ciphertext bytes overwritten by that field
are restored from the outer mask before decryption. The packer leaves optional
shuffling off and emits the corresponding native-reader envelope. Cipher padding
can contain arbitrary leftover bytes in original archives; only a final partial
cipher block is permitted after the valid zlib stream. New output is zero-padded.

`acv_tools/acv_crypto.py` contains the recovered tables, keys and transformations.
`acv_tools/acv_unpack.py` performs structural validation and extraction.
`acv_tools/acv_pack.py` rebuilds indexes and selectively replaces payloads.
Regression tests are in `tests/`; offline probes and Ghidra scripts are in
`research/` and `research/ghidra_scripts/`. Local binaries, input archives,
outputs and recovered evidence remain outside Git.

## Reverse-engineering provenance

The protected client was studied offline using Ghidra and isolated Unicorn CPU
emulation, without launching `Audition.exe` or its DLLs as host programs. A raw
recovered memory image was imported into the analysis database; it is not a
replacement playable executable. The client itself was not modified.

Relevant recovered routines include index reader `005a6250`, index/record writer
`005a6a30`, key selector `005a7720`, table generator `005aa1a0`, decrypt wrapper
`00c39100`, encrypt wrapper `00c39ca0`, cipher factory `00c39f90`, and MT routines
`00cb7700`/`00cb7850`. The archive routines and callees are recorded in
`diagnostics/decompilation/ghidra-native-acv-v1.txt`,
`diagnostics/decompilation/ghidra-native-acv-v2.txt` and
`diagnostics/decompilation/ghidra-native-acv-v3.txt`.
See the [diagnostics guide](diagnostics.md) for preserved evidence and report locations.

Earlier internet leads, failed hypotheses and bootstrap work are retained in
[historical research notes](acv-research-history.md); their old unsupported-status
statements are superseded by this document and the tests above.
