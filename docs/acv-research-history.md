# Historical ACV research — superseded

These are investigation notes from before recovery of the native archive code.
Their statements about missing support are historical, not current limitations.
Superseded emulator dumps and intermediate logs cited below were removed during
cleanup; the final recovery checkpoint, analysis database, native-code reports
and current verification reports were retained.
See [current instructions and validation](acv-unpacking.md).

**Current layout:** maintained Python tools now live in `acv_tools/`, offline probes
in `research/`, Ghidra scripts in `research/ghidra_scripts/`, and tests in `tests/`.
Use `python -m acv_tools.<module>` or `python -m research.<module>` from the project
root. Commands and machine-specific paths below are historical records, not current
setup instructions; they intentionally retain their original locations and findings.
See the [project README](../README.md) for current setup and local-data exclusions,
the [upscaling guide](upscaling.md) for hardware examples, and the
[renderer status](../renderer_patch/README.md#current-local-validation--2026-11-04)
for the later full-set owner-reported test and its limits. Historical single-DLL
instructions do not describe the current matching two-DLL bundle. The RX 9070 XT
is the GPU used on my own machine, not a requirement for other users.

**Diagnostics relocation:** loose JSON/PNG reports now live under
`diagnostics/reports/`, surviving decompilation reports under
`diagnostics/decompilation/`, and cleanup/migration records under
`diagnostics/audit/`. Paths inside the historical text and audit JSON remain as
originally recorded. Check the [diagnostics guide](diagnostics.md) for current
locations; recovered checkpoint/bootstrap/database paths have not changed.

## Original investigation report

This is **not a universal unpacker yet**. `acv_unpack.py` supports the verified
plain format only. Tagged variants 1, 2, 3, 5, and 6 are explicitly rejected.
It does not change source archives, patch the game, or repack textures.

## Usage

Run from `D:/Games/AyoDance/vision-assist` with Python 3.10+; no extra packages:

```sh
# Verify every supported archive entry in memory; no extracted files.
python acv_unpack.py ../DATA

# Extract one supported archive into a NEW destination/001 folder.
python acv_unpack.py ../DATA/001.acv --output texture-work

# Extract supported archives, keeping each archive in a separate folder.
python acv_unpack.py ../DATA --output texture-work

# Tests.
python -m unittest test_acv_unpack -v
```

The command prints JSON results. Exit code 1 means at least one input failed or
was unsupported; it is expected for the whole installation while 302 archives
remain unsupported. `--output` is optional: without it every payload is decoded
and discarded after hashing. Existing per-archive output folders are refused.
Failed extraction may leave a partial output folder; it is reported and never
silently removed. Use a fresh destination to retry.

Names are decoded strictly as CP949. Extraction rejects absolute paths,
traversal, Windows reserved names, unsafe characters, duplicate paths ignoring
case, and file/directory collisions before creating the archive output folder.
Packed and decoded entries are limited to 64 MiB each. This is a safety limit,
not a proven format restriction. Treat the output directory as trusted local
storage: do not let another process alter it during extraction.

## Full local verification

`diagnostics/acv-full-verification.json` records results for all 330 archives:

| Result | Archives | Entries decoded | DDS-named entries |
| --- | ---: | ---: | ---: |
| Verified plain format | 28 | 20,101 | 12,508 |
| Unsupported tagged variants | 302 | Not decoded | Unknown |

The plain archives yielded **1,595,905,559 decoded bytes**. Every payload passed
zlib checksum/EOF validation and exact declared-size checks. This verifies the
container decoding, not full image decoding, texture appearance, or game loading.

Each successful archive has a SHA-256 of its concatenated decoded entries,
each preceded by its 4-byte little-endian decoded length. This is a reproducible
verification digest, not a hash of the original ACV file.

`probe_acv.py` and `diagnostics/acv-sampling.json` retain the earlier deterministic
sampling investigation. The full verification supersedes sampling for plain
payload integrity; the sampling report additionally records sampled DDS headers.

## Confirmed plain layout

All integers are little-endian:

```text
+0 uint32 entry_count
+4 entry_count records of 176 bytes:
    +0   filename[160], NUL-padded, CP949-compatible
    +160 uint32 unknown metadata
    +164 uint32 packed_size
    +168 uint32 raw_size
    +172 uint32 relative_payload_offset
payload_base = 4 + 176 * entry_count
absolute_offset = payload_base + relative_payload_offset
```

Payloads are zlib streams. Records are contiguous, and the last payload ends at
EOF in all 28 verified archives. The unknown metadata field is not needed for
extraction, but its role must be established before claiming safe repacking.

## Remaining reverse-engineering findings

Tagged headers start with `uint16 archive_number`, `uint8 variant`, `0xff`, and
an apparent `uint32 entry_count` at +4. Archive numbers do not reliably identify
formats: older-numbered files may have been replaced with newer variants.

### Variant 1: 13 archives

The index begins at +8 and uses 180-byte records: the plain record plus the
four-byte archive tag. All 9,785 index records validated structurally, but none
of the 160 initial payload samples decoded directly with zlib.

All packed sizes are multiples of 8. Comparing 38 matching-name/raw-size entries
between plain `DATA/033.acv` and variant-1 `DATA/036.acv` found variant-1 packed
sizes equal to plain compressed sizes rounded up to 8 bytes. Across the compared
blocks, repeated 8-byte candidate plaintext blocks mapped consistently to
repeated ciphertext blocks. This supports an ECB-like 8-byte block transform,
**not a particular cipher or key**. No variant-1 payload was decoded.

A broader known-plaintext experiment compared candidate matching-name/raw-size
entries from all plain archives against all variant-1 archives. Across ten
archives (`DATA/039.acv`, `DATA/040.acv`, `DATA/041.acv`, `DATA/042.acv`,
`DATA/046.acv`, `DATA/047.acv`, `DATA/048.acv`, `DATA/049.acv`, `DATA/050.acv`,
`DATA/051.acv`), 71,051 comparisons against previously seen plaintext blocks
had zero cross-archive ciphertext disagreements. This supports a shared
8-byte-block mapping across those archives; it does not recover the cipher.
`DATA/036.acv` had one within-archive disagreement, so was excluded from that
cross-archive map. Matching names/sizes do not guarantee identical contents.

The patcher's two explicit 33-byte strings listed below were tested as standard
Blowfish keys against the 38 candidate pairs in `DATA/033.acv`/`DATA/036.acv`.
Neither matched the first plaintext block, including four input and four output
word/byte-order permutations. This rejects those precise constructions only.

### Variants 2/3: 5 and 43 archives

The uint16 at +8 is divisible by 8 in every archive. A candidate layout places
an opaque index of that length at +10 and payloads afterward. This is not yet a
decoded or fully validated index.

For `DATA/021.acv` (variant 3) and `DATA/053.acv` (variant 2), both with apparent
count 569, the candidate index lengths are 8,672 and 9,120. Both candidate
payload regions are exactly 14,045,640 bytes. All 1,755,705 aligned 8-byte blocks
have matching repeat-position patterns between the archives, despite different
bytes. This is strong block-transform evidence, not plaintext recovery.

### Variants 5/6: 147 and 94 archives

For every archive, with `a = uint32(+8)` and `b = uint32(+12)`:

```text
(a XOR b) AND 0x7fffffff == apparent_entry_count
```

The high bit is present in 235 cases and absent in six: `DATA/254.acv`,
`DATA/290.acv`, `DATA/299.acv`, `DATA/328.acv`, `DATA/329.acv`, `DATA/330.acv`.
Its meaning is unknown. Neither the index nor payload transform is decoded.

A subsequent byte-level experiment found a second candidate masked scalar.
For a word at offset `p`, define `x = uint32(p) XOR a`. Searching byte offsets
`16 <= p <= 16 + 8 * count` for `(x >> 31)` equal to the count field's flag
and `8 * count <= (x & 0x7fffffff) <= 32 * count + 256` yields:

| Candidates per archive | Archives |
| --- | ---: |
| 0 | 3 |
| 1 | 228 |
| 2 | 9 |
| 3 | 1 |

The three exceptions are `DATA/328.acv`, `DATA/329.acv`, `DATA/330.acv`.
All 19 repeated-count groups other than count 1 share exactly one candidate
offset, including groups crossing variants 5 and 6. Examples (decimal):
`DATA/002.acv`: count 182, offset 1292, scalar 3126;
`DATA/254.acv`: count 25, offset 56, scalar 521;
`DATA/299.acv`: count 12, offset 93, scalar 396.
For `DATA/254.acv`, `0x1cf6d321 XOR 0x1cf6d128 == 521`.
These are candidate length-like fields, **not proven lengths or boundaries**.

#### Stronger alignment constraint

All 228 originally unique candidates satisfy `(scalar - count) % 8 == 0`.
More specifically, all 92 unique variant-6 cases satisfy modulus **16**;
variant 5 permits either remainder 0 or 8 modulo 16 (74 and 62 cases).
This suggests different block widths, but does not identify either cipher.
Filtering by 8 for tag 5 and 16 for tag 6 produces **235 unique candidates,
three ambiguous archives, and the same three count-1 exceptions**.
The remaining ambiguous archives are `DATA/100.acv`, `DATA/114.acv`, and
`DATA/155.acv`; repeated-count evidence independently selects offset 2366
for `DATA/114.acv`. The probe reports both the original candidates and
`block_aligned_candidates` so these hypotheses remain distinguishable.

#### Candidate boundary and shared variant-6 bytes

Define `C = scalar - count` and **`B = 16 + C`**, independently of the
masked field's position. This lands at readable, pointer-like material in:

| Archive | Count | Scalar | B |
| --- | ---: | ---: | ---: |
| `DATA/253.acv` | 569 | 7993 | 7440 |
| `DATA/268.acv` | 16 | 464 | 464 |
| `DATA/269.acv` | 17 | 497 | 496 |
| `DATA/270.acv` | 17 | 561 | 560 |

In each, **B + 255** starts with
`d7fb2b80561b8b6e1918410be24b93c9`.
`DATA/268.acv` and `DATA/269.acv` then share 480 consecutive bytes;
`DATA/269.acv` and `DATA/270.acv` share 512. The count-17 pair's B values
and shared sequence both shift by 64 when the scalar changes by 64.
This supports B as a useful boundary, not a universally proved payload start.
The 255-byte windows include partial names, stale-looking fields, pointers,
and a UTF-16 builder temporary-file path; they are not a clean decoded index.

The same 16-byte sequence occurs in the first 2,000,000 bytes of **68 tag-6
archives**, and no other tag/plain archives in that scan. Searching 4096 bytes
after each alignment-qualified B also finds it in those 68 archives, but its
distance from B varies: it is not always 255. The full observations are in
`diagnostics/acv-boundary-research.json`. No marker is treated as a universal
payload signature. A bounded search of those 4096-byte windows (987,136 bytes
including overlapping observations) found no AES-128/192/256 expanded-key
first-two-word recurrence, checking standard/reversed word order. This only
rejects that particular key-schedule-leak hypothesis.

`probe_acv_mask.py` reproduces this scan read-only; run
`python probe_acv_mask.py --self-test` for synthetic checks or
`python probe_acv_mask.py ../DATA` for JSON. The complete local result is
`diagnostics/acv-mask-research.json`. The production extractor is unchanged.

Direct MSVC-rand and standard MT19937 count-seeded offset hypotheses did not
predict these offsets generally. Tested zlib/raw-deflate/gzip decoding at
candidate field/data/end positions also did not establish an index decoder.
`DATA/253.acv` has readable DDS filenames at offsets 7472–7672, spaced 40 bytes
apart, within the 255-byte window described above. This is not proof of a
universal 40-byte index record. Broader count-seeded MT19937, historical MT,
MSVC rand, fixed-seed/output-skip, rejection/scaling and direct count-hash
experiments also failed to recover a general field-position function; best
matches were only a handful of 215 distinct counts. Prefix bytes at +16 vary
between equal-count archives, consistent with chance matches, despite the
stable field position. No general offset formula has been recovered.

### Investigation boundary

Bounded simple XOR, addition, byte reversal and candidate-key trials did not
recover the transforms. Static inspection found protected sections in the client;
no runtime inspection or game execution was performed. Cipher names/constants
found in other components were not tied to ACV processing and are not proof of
the ACV algorithm.

### Follow-up: static-reader boundary and exact position-model exclusions

An installation-wide static pass found no inspectable ACV reader outside the
protected client. `mgx.exe` is a 24,576-byte VB6 program: its visible file path
reads `/data.txt` and constructs a tracking URL, not an evidenced ACV decoder.
`Launcher/` has 15 PNG/JPEG files and a theme INI, with no executable binaries.
`T3CAudio.dll` exposes voice/group functions; `TaskKeyHookWD.dll` exposes keyboard
functions and memory/thread APIs, but neither yielded an ACV reader connection.
All 17 installed EXE/DLL/OCX files had no literal `.acv` in ASCII or UTF-16LE;
this does not exclude constructed names or concealed code.

The five unnamed client sections have entropy 7.789–7.983 bits/byte, and the
`.boot` entry-point section has entropy 7.959. Entry-point RVA is `0x03D8A058`.
This is consistent with the protected code being unavailable as ordinary static
machine code. No game process was launched or inspected.

The shared tag-6 16-byte sequence was absent from installed EXE/DLL binaries.
Its post-boundary distances include 32, 96, 160, 255, 319 and many others;
marker-to-EOF lengths have remainders 0, 13, 14 or 15 modulo 16. These facts
exclude treating that marker as a universal aligned payload start.

A further algebraic position-model test solved complete candidate-state
intervals, rather than guessing seed constants. Across all 215 distinct counts
with candidates, no model matched every observation for seed `count + K`,
`count XOR K`, or `count * K`, with arbitrary constant K, field bases
8/12/16/20 and modulus `8 * count`, within the first 64 outputs of:

- MSVC's 15-bit output generator, multiplier 214013, increment 2531011;
- full 31-bit ANSI LCG, multiplier 1103515245, increment 12345;
- full 32-bit Numerical Recipes LCG, multiplier 1664525, increment 1013904223;
- full 32-bit MSVC LCG state.

The marker's first eight bytes also reject byte-at-a-time output from bits
16–23 of those three LCG states, for all possible relevant initial states.
These are bounded exclusions, not a proof against every PRNG/layout algorithm.

Further cipher identification needs new evidence: an inspectable archive-reader
implementation or an offline, unpacked client memory image. Static absence of
symbols and more failed position models do not themselves supply a decoder.

### Static client/patcher inspection

`D:/Games/AyoDance/Audition.exe` contains protected `.themida`/`.boot`
sections. Static inspection has not recovered its ACV-reading routines.
No game process or downloaded tool was executed.

`D:/Games/AyoDance/Patcher.exe` contains CLR metadata at file offset
`0x570F78` and an embedded native `AmpReader.dll` at `0x3811D`.
The native DLL includes standard Blowfish S-box/P-array constants at outer
file offsets `0x4994D` / `0x4A94D`, with code referencing those tables.
Its exports include `AF_Load`, `AF_GetFile`, `AF_CreateAmp`, and
`AF_DecodePMD`; nearby diagnostics describe AMP/PMD/THD/TBM music processing.
This establishes a real Blowfish implementation, **not an ACV connection**.

Two strings are passed with explicit 33-byte lengths to the same native
routine (outer-file call-site region `0x3FA90`–`0x3FAE7`):

| Outer file offset | String |
| --- | --- |
| `0x4AAF5` | `@@!2366AmpReaderixxngDJ26634@@@A!` |
| `0x4AB19` | `%%#StartA.exeOnlyByEXEfullThis#%%` |

These are unvalidated candidates, possibly music or launcher material.
Neither was demonstrated to reach Blowfish key setup or decode an ACV.
The managed `Blowfish` name at `0x599316` is a SharpZipLib ZIP-encryption enum
member; `RijndaelManaged` at `0x593E27` is associated with SharpZipLib's
`ZipAESTransform`. Neither is evidence of the ACV cipher.

Historical web references describe incompatible generations of ACV tools;
archive numbers in those posts must not be treated as format detection for
this installation. A forum mention of older source associated with `nonclg`
did not lead to a verified public ACV implementation. Several forum pages
were inaccessible (HTTP 403).

A universal unpacker requires actual index/payload decoders for each remaining
variant, followed by full-entry verification across the installation. The next
useful input is an inspectable ACV reader or a working standalone ACV tool for
these variants; music-reader keys and more guessed cipher names are not a
substitute. Do not call encrypted bytes successfully extracted textures.

## Newer-tool research leads (not locally validated)

Three public leads describe support beyond the historical plain extractor:

1. [Mahadi Rafi Winata: Gen 0–6 extractor tested on AyoDance](https://www.facebook.com/Mahadi.Rafi.Winata/posts/40113002434957697).
   The public page metadata states that 122,090 items were extracted, including
   RGM, DDS and SLK. Its screenshot shows 122,090/122,090 items, 76,837 files
   written and archive `326.acv`, with local-disk and browser extraction options.
   These are the author's claims and screenshot, not our verification. No public
   source or download link was found in the retrieved post. This is the most
   directly relevant lead for this installation's tagged variants through 6;
   the author's generation labels have not been mapped to our header tags.
2. [ACV TOOL GEN 6, GEN 8 by Serendipity](https://www.youtube.com/watch?v=QnJQBsVTdOQ).
   The video's actual player-metadata description explicitly lists AyoDance,
   VTC, Aubiz, Thai, Korean, 9YOU and Next Gen; it claims unpack/pack through
   Gen 8 and standalone operation without opening the game. It also describes
   extraction of selected paths. The description directs buyers to
   `https://www.facebook.com/serendipity.22222`; no public binary/source link
   was present. Compatibility and safety remain unverified.
3. [DUMP & ACV UNPACK 8.0 FOR 9YOU AUDITION](http://www.auditiontools.org/2025/10/dump-acv-unpack-80-for-9you-audition.html).
   A commercial listing claims Gen 8 support for 9YOU and other clients, but
   does not establish support for this AyoDance installation or standalone
   operation. It must not be confused with a source release.

Before paying for or running any tool, request a sample extraction of copies
of this installation's unsupported archives (one each of tags 1, 2, 3, 5, 6).
Validate filenames, decoded sizes, DDS headers and actual image decoding.
A repacking claim needs a separate round-trip test preserving paths, metadata,
alpha, pixel format and mipmaps; extraction alone does not establish safe
upscaling/replacement. Use an isolated environment for unknown binaries, not
administrator access or disabled antivirus on the game machine.

Rejected/dead leads: `WarpZephyr/ACVLooseLoader` targets **Armored Core V**,
not Audition. `kengmod/mod-acv` exposes only a `000.acv`, not decoder source.
Both old MediaFire links from the UAE forum tutorial returned HTTP 404.
The tutorial's Blowfish/Mersenne Twister/MD5 descriptions are historical,
unverified claims, not sufficient algorithm specifications or proven mappings
for this installation. No newer decoder was added on this evidence.

## Ghidra: verified first-layer recovery

The bundled `ghidra_12.2_PUBLIC` was run headlessly with the installed JDK 25.
The analysis project is `diagnostics/ghidra-projects/AyoAcvStatic.gpr`, containing
`Audition.exe`. Only this analysis database was modified; the original client
and ACV archives were not executed or changed.

The entry at VA `0x0418A058` calls a stub at `0x0418A1A8`. Ghidra's disassembly
shows that stub reading **six consecutive aPLib-style streams**, decoding them
into the initially uninitialized `.themida` section at VA `0x03DD4000`, then
jumping to VA `0x03F7C7F4`. An independent Python decoder reproduced all six
streams, yielding exactly **3,891,200 bytes**, the destination section's size.
The first five streams each decode to 648,533 bytes; the last to 648,535 bytes.

| Verified artifact | SHA-256 |
| --- | --- |
| Original `../Audition.exe` | `f39a8fad7513f5b30a78bdc8bf82fd48e0bdff4f7940ee7b0a7dabd1eace47eb` |
| Recovered bootstrap region | `51fa7878edf62a901a62e2c0589b5f8e9c360bc6755bd18a7ba267438cbec882` |

Reproduce the data-only decoding with Python's standard library:

```sh
python unpack_client_bootstrap.py --self-test
# Destination must not already exist; offsets are specific to this client build.
python unpack_client_bootstrap.py ../Audition.exe diagnostics/bootstrap-retry
```

The existing output is `diagnostics/bootstrap-static/bootstrap.bin`; its
`report.json` records source offsets, compressed/decoded sizes, destination
addresses and per-stream hashes. This raw region is **not an unpacked executable**.
Self-checks cover literals, short/long/repeated back-references, single-byte
copies, zero insertion, truncated input, invalid references and output bounds.

Ghidra scripts, both compiled and executed successfully inside Ghidra:

- `ghidra_scripts/AcvStaticSurvey.java`: writes a new static report containing
  memory layout, selected strings/references, bounded function disassembly and
  decompilation. Its historical output,
  `diagnostics/ghidra-bootstrap-all-functions.txt`, was removed during the earlier
  cleanup; the retained Ghidra project and later native-code reports remain.
  This absent report is not a current verification artifact.
- `ghidra_scripts/LoadAcvBootstrap.java`: verifies the recovered region's hash
  and destination layout before populating `.themida` in the analysis database.
  It refuses an already-initialized destination. It marks `0x03F7C7F4` as an
  entry point; it does not save modifications into the source executable.

The recovered entry chain includes `0x03F7C7F4 -> 0x03E6ED5A`, then scattered
jump/call stubs, eventually reaching the obfuscated routine at `0x03FD9F70`.
Its indirect dispatch at `0x03FDA6EF` remains unresolved. Ghidra explicitly
warns about stack tracking and unresolved jump tables; that decompilation must
not be treated as a correct high-level implementation.

Independent static scans found WinLicense strings and a full standard CRC-32
lookup table at recovered-region offset `0x1F0E07` / VA `0x03FC4E07`. They did
not identify ACV/DDS strings, standard Blowfish/MT19937 constants, an intact
embedded PE, or a validated zlib stream in that region. CRC-32 alone does not
establish an archive-reader connection; absent signatures do not exclude
concealed code or generated constants.

**Status:** first protection layer recovered and imported into Ghidra; actual
ACV index/payload routines still not recovered. No extra archive variants,
repacking, texture upscaling or game-loading compatibility were implemented.
The next concrete RE target is the loader's unresolved dispatch and subsequent
client-code restoration, rather than another guessed ACV key or cipher.

## External references

- https://zenhax.com/viewtopic.php@t=1182.html — historical plain-format script
  and discussion of limitations in older ACV tools.
- https://forum.ragezone.com/threads/acv-tool-release-acv-unpacker.456025/page-2
  — historical tools/source excerpts; direct access was blocked during research.
- https://github.com/microsoft/DirectXTex/wiki/Texconv — DDS conversion tooling;
  not an ACV unpacker.

Upscaling and repacking remain separate, unimplemented steps. Preserve original
archives and alpha/mipmap/pixel-format information; larger texture acceptance
and online-client policy have not been tested.
