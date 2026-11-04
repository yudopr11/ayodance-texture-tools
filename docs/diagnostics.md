# Local diagnostics and retained evidence

`diagnostics/` is local, Git-ignored storage. It is **not included with a source
checkout** and Git is not its backup. Normal extraction/repacking does not need
these files. Preserve a separate copy if you need offline recovery or provenance.

## Layout

```text
diagnostics/
  reports/                 ACV scans, validation reports and texture comparisons
  decompilation/           Native archive and renderer Ghidra text reports
  audit/                   Cleanup, migration and organization records
  bootstrap-static/        Recovered bootstrap bytes and identity report
  checkpoint-v64/          Matching CPU checkpoint, recovered image and trace
  ghidra-projects/         Retained Ghidra analysis databases
  python-re-libs/          Original offline research dependency snapshot
  texture-demo/262/        Twelve extracted example DDS files
  texture-demo-staging/    Example rebuilt 262.acv
```

The organization pass moved **20 loose files** into the first three directories.
All **238 pre-existing files** were SHA-256 checked before/after; no files were
deleted or changed. `audit/organization.json` records the moved paths and hashes.
The manifest itself is a new file, not part of that 238-file comparison.

## Current renderer status and logging

The scoped **two-DLL** bundle has a full-set owner-reported working outcome on
2026-11-04; the comprehensive prefix/I/O candidate remains inactive. See
[the renderer status](../renderer_patch/README.md#current-local-validation--2026-11-04)
and [current two-DLL instructions](../renderer_patch/CACHE-TEST.md), not old
single-DLL deployment notes or a test-fixture DLL.

Local install/backup evidence is under
`renderer-upload-diagnostics/full-enhanced-test-20261104T213508/`; native backup
refresh evidence is under `native-backup-refresh/20261104T050243/`. The organization
counts in this guide describe their historical pass, not today's tree size. Earlier CTD
logs and offline receipts remain unchanged; a later successful test does not
rewrite their results or establish every crash's cause.

For a small `renderer-patch.log`, disable both verbose triggers before the next
launch: clear `ACV_RENDER_TRACE` from the launching environment and archive the
sibling `renderer-trace.enabled` marker. Tracing is latched per session, so a running
game does not change modes mid-session. Startup and failure logging remains;
there is no supported fully-off switch. Close the game/launcher before archiving
an old log to start a fresh one. See [minimal logging](../renderer_patch/README.md#minimal-logging-default).

## Reports

- `reports/native-index-scan-v2.json`: all 330 original archive indexes.
- `reports/native-payload-samples-v3.json`: sampled decoded payload validation.
- `reports/real-pack-roundtrip-v1.json`: real archive replacement/preservation tests.
- `reports/texture-path-preflight.json`: texture extraction path checks.
- `reports/acv-sampling.json`, `acv-full-verification.json`,
  `acv-boundary-research.json`, `acv-mask-research.json`: earlier format research.
  These describe their historical probe capabilities, not the current unpacker's limits.
- `reports/effect-original-vs-ai.json` and its matching PNG: texture comparison.
- `decompilation/ghidra-native-acv-v1.txt` through `v3.txt`: different archive-code
  investigation coverage, not interchangeable duplicate logs.
- `decompilation/ghidra-render-v1.txt` through `v3.txt`: different renderer-code coverage.

`audit/` preserves the original cleanup/migration records unchanged. Embedded paths
record where files existed at the time; they were not rewritten during organization.
Historical documentation may also use old locations. The absent
`ghidra-bootstrap-all-functions.txt` was removed in an earlier cleanup and is not a
current report.

## What not to delete casually

- Keep **all** of `checkpoint-v64/`: `image.bin` does not replace `checkpoint.zip`.
  Checkpoint restoration validates the client, bootstrap, Unicorn binary/version
  and host pointer width. Reinstalling a different emulator build can make it incompatible.
- Keep `bootstrap-static/` at its existing path; current research code hashes it.
- Keep each Ghidra `.gpr` with its associated `.rep` directory. A zero-byte `.gpr`
  is not evidence that the project is empty or disposable.
- Keep `python-re-libs/` if checkpoint reproducibility matters. Production tools do
  not import it. Its presence alone does not install those packages into `.venv`.
- Reports and traces with different version suffixes can cover different routines;
  do not assume the latest version supersedes the others.

The retained tree was about **410 MiB** before adding the organization manifest;
98.6% was checkpoint/bootstrap/database/dependency data. No surviving scratch logs
were proven redundant, so this cleanup organized evidence rather than deleting it.

## Running research commands

Run from the project root; do not run an old root-level script path:

```powershell
$env:PYTHONPATH = "$PWD/diagnostics/python-re-libs"
.\.venv\Scripts\python.exe -B -m research.checkpoint_loader --self-test
.\.venv\Scripts\python.exe -B -m research.unpack_client_bootstrap --self-test
.\.venv\Scripts\python.exe -B -m research.probe_acv --self-test
```

Actual recovery/resume requires the exact trusted client/evidence used to create
that checkpoint. Do not load downloaded/untrusted checkpoints. Ghidra scripts are
under `research/ghidra_scripts/`; their caller-supplied output paths should now point
to `diagnostics/decompilation/` for new text reports. Ghidra and a JDK are separate
local installations.

### Retired background memory pilot

The lower-resolution background pilot was rejected and is not a supported
workflow. Its source and test were archived locally under `diagnostics/audit/`
with a hash-verified cleanup record; existing pilot outputs and reports remain
historical evidence. Do not install them or substitute them for enhanced textures.

### Bounded native memory-path emulation

`research/ghidra_scripts/InspectAcvAllocation.java` records selected native
allocation/eviction instructions and the archive-setup-to-D3D startup sequence.
Use it in place of `EmulateAcvMemoryPaths.java` in the command below to produce
instruction evidence. Startup call ordering is not a proof of thread quiescence;
the [comprehensive default candidate refuses activation](../renderer_patch/README.md#current-candidate-is-deliberately-inactive),
unlike the separately guarded scoped cache bundle. `research/ghidra_scripts/EmulateAcvMemoryPaths.java` executes two
short instruction slices with Ghidra's `EmulatorHelper` and asserts their results:
archive-cache ID truncation and the resource scratch-allocation multiplier.
These are synthetic-state checks, **not** an ACV parser test, heap simulation,
post-login replay, or proof of a particular crash's cause. Cache erasure and
allocator calls are deliberately not executed.

Use the recovered native project, not the protected static project. Supply
absolute paths and a new report filename; keep the project read-only:

```powershell
& "$PWD/ghidra_12.2_PUBLIC/support/analyzeHeadless.bat" `
  "$PWD/diagnostics/ghidra-projects" AyoAcvNative `
  -process -readOnly -noanalysis `
  -scriptPath "$PWD/research/ghidra_scripts" `
  -postScript EmulateAcvMemoryPaths.java "$PWD/diagnostics/decompilation/memory-paths-new.txt"
```

The installation directory above is a local example, not a shipped dependency.
Neither script executes the game or changes installed archives. Actual crash
attribution still requires evidence of the failing session's executed path.

### Native decode, preflight, cache cleanup and DDS-consumer fixtures

The following offline checks use Unicorn from the retained research dependency
snapshot and the SHA-256-verified recovered v64 image. They do not launch the
client, use credentials, contact a server, change protection or install a patch.
Run from the repository root with its own `.venv`:

```powershell
$env:PYTHONPATH = "$PWD/diagnostics/python-re-libs"
.\.venv\Scripts\python.exe -B -m research.emulate_native_inflate --self-test
.\.venv\Scripts\python.exe -B -m research.emulate_prelogin_archives --self-test
.\.venv\Scripts\python.exe -B -m research.emulate_prelogin_archives `
  original-data/DATA diagnostics/reports/prelogin-slk-new.json
.\.venv\Scripts\python.exe -B -m unittest research.test_archive_cache_patch research.test_archive_readers -v
.\.venv\Scripts\python.exe -B -m research.emulate_archive_open --self-test
.\.venv\Scripts\python.exe -B -m research.emulate_archive_open `
  original-data/DATA diagnostics/reports/native-open-new.json
.\.venv\Scripts\python.exe -B -m research.emulate_archive_index `
  original-data/DATA diagnostics/reports/native-index-new.json
.\.venv\Scripts\python.exe -B -m research.emulate_texture_consumer --self-test
.\.venv\Scripts\python.exe -B -m research.emulate_texture_consumer `
  original-data/DATA/226.acv diagnostics/reports/server-consumer-new.json
```

Use a **new** report filename. `research.emulate_native_inflate` executes native
zlib with bounded modeled allocation callbacks. The cache regression executes
native iterator/erase/free wrappers and cleanup tables, substituting only
Win32 HeapFree/unmap/close; it reads the exact patch bytes from
`renderer_patch/archive_cache_patch.hpp`. It checks low/high IDs, the `<500`
guard, single/seven-node trees, stack/register/filename preservation and every
neighboring cleanup slot.

`research.emulate_prelogin_archives` executes the native `44..330` preflight
loop and resource-to-archive ID predicate for `배포금지%03d.slk`. It verifies
distinct payloads with native inflate, using Python for index/crypto and modeled
formatting, index membership and resource open/release. Its four self-checks cover
success, missing resource, wrong owner and failed open. It starts **after** native
index initialization and stops **before** timers, UI and later failure handling.
A passing flag does not test native mapping/eviction, allocation failure, Windows
fallback I/O or prove what happened in the failing session. Do not bypass the
client's preflight flag or failure branch.

`research.emulate_archive_open` executes native resource open/release, cache tree
operations, eviction and inflate for all 287 preflight resources. It compares
original and corrected eviction under four **mapped-view** budgets. Index nodes,
crypto, CRT allocation and Win32 operations are modeled. Zero view budget forces
mapping failure but leaves modeled heap available: it is not zero process VA.
Sparse mappings charge whole-file sizes without loading entire archives. An
injected allocation failure stops at the throwing-allocation boundary; report
mode preserves the failure and exits nonzero. A PASS is not a heap-safety verdict
or a Windows fragmentation/exception test.

`research.emulate_archive_index` executes native tagged-index envelope handling,
PRNG/permutation, inflate and 212-byte record normalization. It compares names,
metadata, sizes, offsets, tags and variant-6 IVs with the maintained decoder.
Plain/variant-1 indexes are explicitly `NOT_ENCODED`, not native-parser passes.
Header crypto and heap/OS boundaries are modeled; native crypto mode arguments
are checked. Native writes to the modeled heap must remain within a live logical
allocation, but native reads and the full global index initializer are not
validated. It records input paths and exact index-byte hashes, preserves completed
rows and a failure row on exceptions, and refuses to overwrite reports. Neither
reader replays login or establishes the installed client's available VA.

`research.emulate_texture_consumer` checks the server-selection DDS namespace
in the supplied archive. It compares native inflate output with the maintained
Python decoder, then drives native `0x00ca5880` through DDS reads, seeks, copy and
D3DX argument construction. Archive lookup/path normalization, resource objects,
heap success and renderer limits are synthetic. It stops **before** D3DX: neither
the shim, GPU, heap exhaustion, complete login transition nor actual asset
access order is replayed. A passing fixture does not prove live compatibility.

### Compiled archive-I/O candidate

The build creates both the deliberately inactive comprehensive candidate and a
separate scoped cache bundle. For the prefix/I/O research tests, use its CPU-only
fixture, never either game DLL:

```powershell
.\.venv\Scripts\python.exe -B renderer_patch/build.py
$env:PYTHONPATH = "$PWD/diagnostics/python-re-libs"
.\.venv\Scripts\python.exe -B -m unittest research.test_archive_io -v
# Read-only inspection; OWNER must match a tagged archive's ID.
.\renderer_patch\build\archive_io_test.exe --prefix 267 original-data/DATA/267.acv
# New reports only; these read archive data but never install it.
.\.venv\Scripts\python.exe -B -c "from research.test_archive_io import catalog_report; print(catalog_report('original-data/DATA', 'staging/DATA', 'diagnostics/reports/archive-prefix-new.json'))"
.\.venv\Scripts\python.exe -B -c "from research.test_archive_io import fallback_catalog_report; print(fallback_catalog_report('original-data/DATA', 'staging/DATA', 'diagnostics/reports/archive-fallback-new.json'))"
```

`archive_io_emulator.dll` is an isolated test image, **not a game DLL**. Python
maps its PE sections/imports into Unicorn; it does not load it into the host or
client. The tests execute its actual compiled x86 stack/register-capture thunks
alongside native resource open/decode/release, short-read/allocation/reopen/read
failures, retained-view reuse and budget refusal. The private explicit-offset
read model covers pending completion and rejects closing an outstanding read;
a separate Windows CPU test checks 256 reads across four threads without moving
the native handle's cursor. This does not prove native handle-table lifetime,
index/resource exclusion or borrower drain. Native crypto, index lookup, client heap
and Win32 services remain modeled. Logical live/freed heap accesses and mapped
file extents are checked for both native reads and writes. The encoded-index
fixtures continue through native `5A6250` and verify normalized records and
allocation cleanup, then explicitly unmap the view. They do **not** execute the
outer global index publisher/cleanup or the plain/variant-1 native parser.

The catalog comparison checks format-aware wire extents against the maintained
reader for 330 archives in each directory. Recorded page charges are planned
sizes, not observed simultaneous mappings. The fallback report replays valid
stored-larger-than-raw entries, verifies decoded bytes and native destruction,
and explicitly records undecodable non-DDS entries as `OPAQUE_NOT_REPLAYED`.
Such rows are not silently discarded or counted as native passes; a DDS decode
failure still aborts. Partial reports are retained on errors. The staged catalog
can include historical outputs such as the superseded 262; these checks do not
replace receipt/source binding or authorize installation.

Two lifetime regressions distinguish a live resource from an in-progress open:

- Publishing an index-only prefix in the native global view table and then
  entering resource open reproduces an unmapped read: the native loader borrows
  that prefix without calling the mapping wrapper. This is a deliberately unsafe
  candidate interleaving, not a replay of the retained game CTD.
- A successfully decoded retained-view fixture survives native view cleanup;
  subsequent native read/seek/tell/getc and resource destruction use owned decoded
  storage. This bounds that fixture's source-view borrow to open/decode, not the
  entire resource lifetime. It does not establish every indirect consumer.

Coordination must cover complete native index initialization (`5A78F0`, including
publication and cleanup), resource opens (`5AAF60`, mutually exclusive because of
shared cache/crypto/handles), public presence queries (`5A99C0`) and teardown
(`5AA430`). Configuration/maintenance must not mutate these structures during the
read phase. Native exceptions must release ownership on unwind; a normal-return
unlock or a recursive registry lock is insufficient. These are implementation
requirements, not an installed synchronization mechanism.

`renderer_patch/build.py` also runs the standalone C SEH boundary and C++
`ArchivePhase` CPU tests. The small C-only MSVC-target COFF object is built with
Zig and no SDK headers/libraries, then linked into the GNU-target test executable.
Tests verify ownership remains held during first-pass exception search, internally
handled exceptions and continued execution, and releases on normal return or
outward unwind in nested order. Expected outward/refusal exception codes are
checked rather than accepting unrelated faults. Event-coordinated competing
threads test exclusion, followed by 1,024 serialized callbacks. This primitive
has no native entry-point routing yet; it is not a solution to installing hooks
while unhooked native callers may already be running. No texture data changes.

No prefix/I/O fixture proves exclusion of concurrent index/resource borrowers,
safe startup for those hooks or available Windows VA. The [comprehensive default activation gate remains false](../renderer_patch/README.md#current-candidate-is-deliberately-inactive);
never install the test fixture or use a passing test to bypass that gate.

The early `research.probe_acv` sampler is **not** the complete archive decoder. Its
default report destination is `diagnostics/reports/acv-sampling.json`, and it refuses
to overwrite an existing report. For normal work, use the
[maintained unpacker and packer](acv-unpacking.md).
