# Experimental v64 high-resolution UI renderer pilot

**Experimental, client-specific, and not exhaustively validated.** The scoped two-DLL bundle below has passed a local, owner-reported full-set test. Earlier enhanced-pack crashes, including a recorded out-of-memory upload, remain relevant evidence; success does not establish the cause of every earlier CTD or compatibility with other clients. Never replace the original client EXE.

## Current local validation — 2026-11-04

The owner reported the scoped bundle working with enhanced textures, without
reducing their dimensions or pixels. This is one client's controlled test, not
exhaustive visual/gameplay validation, proof of every earlier crash's cause or
publisher permission. Verify each new client/build configuration independently.

## Scoped cache test bundle

Run the build to generate the separate **[two-DLL cache test bundle](CACHE-TEST.md)**
at `renderer_patch/build/cache-test/`; no prebuilt bundle is shipped. It enables the renderer corrections and
full-width archive-ID cache fix through a loader-lock/thread-exclusion publication
boundary. It preserves all enhanced textures and leaves the prefix/mapping/read/
scratch hooks disabled.

**To use enlarged textures in the supported game client, install both matching
files from `renderer_patch/build/cache-test/` together beside `Audition.exe`:**

```text
AyoDance/
  Audition.exe
  d3d9.dll
  acv_patch_boundary.dll
  DATA/
    ...verified enhanced archives and loose images...
```

- **`d3d9.dll`** uploads enlarged textures while preserving native UI dimensions
  and click areas, and contains this bundle's renderer/cache corrections.
- **`acv_patch_boundary.dll`** provides the guarded publication boundary used to
  install those corrections. **It is required, not an optional diagnostic DLL.**
- Use the pair from the **same build**. Do not substitute `build/d3d9.dll` (inactive)
  or the synthetic `build/boundary-test/d3d9.dll`.
- Offline unpacking, upscaling and repacking need neither DLL. These are runtime
  requirements for this scoped bundle, not requirements of the Python tools.

Follow [the two-DLL instructions](CACHE-TEST.md); no files are installed
automatically. It has the local test outcome above, but is not a universally proven
CTD fix. For low-volume logging, see [minimal logging](#minimal-logging-default).

## Current candidate is deliberately inactive

The default comprehensive source build refuses `AcvInstallV64` **before reading fixed client
addresses or writing native patches**. Its log says
`INACTIVE: candidate native patches withheld; archive-loader quiescence is not established`.
It still forwards system Direct3D calls, but **does not enable enhanced-texture
layout support**. Do not install it with enhanced assets or replace a previously
installed experimental build with it. The offline candidate and its tests remain
available for investigation; there is no runtime override for this gate.

Recovered startup calls archive setup at `0x006AE34E` and conditionally calls
archive maintenance at `0x006AE361`, before graphics initialization at `0x006AE4D2`
and the resolved D3D factory call at `0x00CBBA64`. There is also a concrete
pre-graphics resource-open path: `0x006AE405 → 0x006BC5F0 → 0x00548F20 →
0x005AAF60` attempts `sound/cheers.mp3` and `sound/flameoutloop.mp3`. These are
static call paths, not observed runtime loads. Startup ordering alone proves
neither quiescence nor a patching race. The known index-reading/preflight worker is
launched later by IntroScene at `0x009C4EF4`, which is a promising ordering but
not complete caller/thread-exclusion proof. Installing in `DllMain` or before
the real D3D factory is not independently a demonstrated workaround.
A cooperative startup point or exclusion-and-drain protocol must first be proven;
merely changing the source gate is not that proof.

The archive-phase serialization building block now has an actual-unwind-safe
cleanup boundary. `archive_phase_seh.c` is compiled as a C-only MSVC-target COFF
object with the pinned Zig compiler, without SDK headers/libraries, and linked
into the GNU-target CPU tests. This avoids that compiler's broken GNU-target x86
`__finally` emission; it does not install or download another toolchain.
`ArchivePhase` holds one critical section across the entire callback and rejects
same-thread recursion, rather than allowing a resource load to borrow a published
index prefix recursively. Its cleanup runs on normal return or outward SEH
unwind, **not** during first-pass exception search or an internally handled or
continued exception. Tests check these timings, nested cleanup order, recursive
refusal and 1,024 serialized calls across four threads. There is no crash-swallowing
handler in this boundary.

This component is not yet routed through the client's index/open/query/teardown
entry points. It therefore does not establish native synchronization or safe
installation, and it does not make the inactive DLL ready for a game test.
Enhanced archive bytes, DDS dimensions and formats are unchanged by this work.

## Renderer behavior (enabled in the scoped bundle)

The recovered client uses a DDS's dimensions for both its GPU texture and its logical UI layout. Its alpha hit-test also assumes the two dimensions are identical. Enlarging the DDS alone therefore enlarges/misaligns UI geometry and its clickable mask.

This x86 `d3d9.dll` shim forwards Direct3D to Windows and, for the specifically identified v64 texture loaders:

- reads native dimensions from a marker in the DDS reserved header fields;
- uploads the actual enlarged texture, but reports the original dimensions to the UI loader;
- keeps a native-size alpha hit-test cache, using the strongest alpha in each corresponding enlarged pixel block;
- leaves unmarked textures and other D3DX callers unchanged;
- keeps single-level marked UI textures single-level when the recognized managed
  loader requests zero mips, instead of generating an extra full mip chain;
- for version-2 P8/depth-one volume tags only, prepares a validated temporary upload:
  expands P8 palette indices to BGRA8, or clears depth-one volume header fields without
  changing DXT blocks. The archive payload itself is not modified by the shim.

This is not a Start/Exit or archive-001 special case: the recovered ordinary DDS loaders apply the same logic to every marked DDS, regardless of filename, widget or archive. The main `acv_tools/acv_upscale.py` pipeline now adds the marker after image-cache publication; the cache stays untagged. Use verified native source payloads, never already-enhanced archives. Always verify the exact source and receipt hashes; a backup's folder name does not establish that it is untouched. GDI-generated text and code-generated textures use separate paths; this does not sharpen them or prove every UI draw path is covered.

The marker is `ACVUI2X\0` at DDS offset 32. Version 1 covers ordinary 2D DDS
(including offline-flattened PSD composites); version 2 covers validated P8 and
depth-one DXT1/3/5 volumes. Both retain real enlarged width/height fields. New
receipts use renderer contract `v64-tagged-dimensions-v2`; use a compatible scoped
bundle for these outputs, not an older version-1-only shim or the inactive default. The new shim still
accepts older ordinary version-1 textures. This is **not** a header-only resolution
disguise or a downscale.

P8 uses one RGBA palette for all mip levels on disk and expands to BGRA8 GPU memory;
palette color and partial-alpha quantization is lossy. The original alpha is resized
outside the AI model. Depth-one volume uploads contain exactly one slice; true 3D
volumes are not flattened or handled by this hook and remain native in the pipeline.
Temporary upload buffers and complete mip payloads are bounded at 64 MiB. The hook
addresses and client signature guard are unchanged. These formats passed offline
GPU upload checks, not live-client call-site/layout validation.

The installer checks the loaded executable's x86 image shape, entry point,
loader/call-site bytes, alpha routine, client malloc entry/body, CRT-release thunk
and archive-cache eviction/ABI bytes before writing memory. The scoped bundle
publishes its three changes inside the documented loader/thread-exclusion boundary,
rechecking dependencies and a cache count of at most 12 while execution is excluded;
it does not require an empty cache. This does not authorize late injection or
installing over a running game. It does not change the EXE on disk. Different client
builds are refused. Neither static matching nor an `ACTIVE_V64` log proves complete
visual/gameplay correctness; see the [boundary assumptions](CACHE-TEST.md#what-makes-publication-different).

## Files and reproducible build

From the project root:

```powershell
# Compiler is local to this project, not a global installation.
uv pip install --target tools/zig-python --no-deps ziglang==0.14.1
.venv/Scripts/python.exe -B renderer_patch/build.py
.venv/Scripts/python.exe -B -m unittest tests.test_acv_unpack tests.test_acv_pack tests.test_stage_renderer_pilot tests.test_cli -q
```

The basic Python command above does not run the GPU integration tests. Full Python
test discovery can launch the installed converter and ESRGAN; see the
[project README](../README.md). Use package/module commands from the project root.
Downloaded tools and build outputs are Git-ignored and obtained or built separately.

The build runs standalone C SEH and C++ archive-phase serialization,
metadata/alpha, archive-I/O policy, CPU-only diagnostic and fake-COM
lifecycle/patch-rollback and loader-boundary tests: **seven CPU executables** in
all. It then creates:

- `renderer_patch/build/cache-test/` (scoped two-DLL bundle, `TESTING.md` and hash manifest)
- `renderer_patch/build/d3d9.dll` (deliberately inactive)
- `renderer_patch/build/gpu_probe.exe`
- `renderer_patch/build/archive_io_test.exe` (CPU tests and read-only prefix inspection)
- `renderer_patch/build/archive_io_emulator.dll` (Unicorn-only test fixture, **never install**)

Run the standalone graphics check **with the game and full-screen D3D applications closed**:

```powershell
& "$PWD/renderer_patch/build/gpu_probe.exe" "$PWD/../d3dx9_43.dll" "$PWD/renderer_patch/build/d3d9.dll"
```

Optional additional arguments are paths to tagged DDS samples; each is passed
through the shared `create_tagged` implementation to check physical dimensions,
logical metadata, mip count and native-size alpha construction on the GPU. The
built-in probe also compares a 640×1024 single-level BGRA8 upload with D3DX's
zero-mip default: 11 versus 1 GPU levels, identical level-zero RGBA, and 3,495,244
versus 2,621,440 texel bytes. These are texture-storage counts, not measured total
process-memory savings. For
example, append the paths to a staged P8 and depth-one volume DDS after the DLLs.

This probe loads the built DLL, checks its exports and refusal of the wrong executable, parses a marked DDS with the actual D3DX DLL, and attempts real texture creation/locking. It does not launch, attach to, or modify the game. Exit 0 is a pass; exit 3 means GPU checks were blocked. A passing probe still does not validate the live game's patched x86 call sites.

## Minimal logging (default)

The shim has two existing modes; **there is no supported fully-off switch**:

- **Minimal:** startup/activation/refusal messages and failed tagged uploads only.
  Successful per-upload records and per-line trace flushing are disabled.
- **Verbose:** those messages plus each tagged upload's stages; useful only for a
  short reproduction. It can grow quickly and slow loading.

To return to minimal mode, clear **both** trace triggers before the next launch:

```powershell
# In the PowerShell that will launch the normal launcher:
Remove-Item Env:ACV_RENDER_TRACE -ErrorAction SilentlyContinue
# Beside Audition.exe/d3d9.dll: move renderer-trace.enabled to your evidence backup.
# Do not delete renderer-patch.log or overwrite DLLs while the game is running.
```

`ACV_RENDER_TRACE=1` **or** a non-directory `renderer-trace.enabled` beside the DLL
turns tracing on. Setting the variable to `0` does not override a present marker.
The setting is sampled once during shim initialization: moving the marker while
playing affects the **next launch**, not the active session. Restart the normal
launcher too if it inherited `ACV_RENDER_TRACE=1`; clear a persistent user/system
setting only if you previously created one. No protection changes are needed.

At the next launch, require `SESSION: renderer diagnostics v1 trace=0`; the scoped
bundle should still report `ACTIVE_V64` and `CACHE_TEST_V1`. The log appends across
sessions and does not automatically shrink. After the game and launcher are closed,
archive/move the old log outside the game folder; the next session creates a new
one. Preserve failure evidence rather than deleting it. Never rotate an active log.

## Diagnosing a broken screen, failed load or crash

The shim appends `renderer-patch.log` beside itself. New diagnostic builds add UTC
timestamps, PID/TID and a session marker. Tagged-upload failures include the stage,
HRESULT, caller address, native/physical dimensions, requested format/pool/mips,
effective `upload_mips`, actual GPU format/pool/mip count, device-cap query
result/limits, and available process virtual/system memory.
`CAPS_END` failure is **not** a proven oversized texture: older builds combined
that failure with dimension-limit rejection in one misleading message.

For a permitted diagnostic test, first finish the upscale job and preserve the
existing log. The current bundle needs **no rebuild or DLL replacement just to
change tracing**. If testing a different build, close the game/launcher, back up
the identified older bundle, and follow [the two-DLL test guide](CACHE-TEST.md).
Nothing here installs it automatically. Enable detailed tracing only for a short
reproduction:

```powershell
$env:ACV_RENDER_TRACE = '1'
# Start your usual, authorized launcher FROM THIS PowerShell so it inherits the variable.
# Do not bypass the normal launcher/protection or change system DLLs.
```

If the normal launcher does not pass the environment variable to the game, create
an empty `renderer-trace.enabled` file beside this shim instead. Its presence
enables tracing at the next session start; remove it after the short test. No
launcher/protection changes are needed. A directory with that name is ignored.

The next session should say `SESSION: renderer diagnostics v1 trace=1`. Tracing
adds per-upload `CAPS`, `CREATE`, `DESC`, `METADATA` and `UPLOAD_OK` records, and
`FORWARD` records for tagged uploads from other call sites (still forwarded
unchanged). Match stages by PID, TID and `id`; before/after records bracket the
calls. The last unmatched begin is a clue, **not proof of causation**: termination,
a hang or logging failure can also prevent an end record. Tracing covers tagged
DDS uploads reaching this hook, not archive decoding, every graphics API, later
draws, or exceptions elsewhere. It does not catch/swallow crashes or retry loads.

Each record contains `bytes`, `crc_bytes` and `crc32`: IEEE CRC32 of the first
`min(bytes, 4096)` bytes of the **original tagged DDS**, before runtime conversion.
The hook has no archive/texture filename. Map its fingerprint back to candidate
paths using the exact installed archive bytes or a byte-identical staged revision,
not untouched source bytes. For offline mapping, extract the needed archive from
your verified package first:

```powershell
# Substitute bytes= and crc32= from the log. A directory searches its top-level ACVs.
.venv/Scripts/python.exe -B -m acv_tools.renderer_diagnostics staging/DATA/261.acv --bytes 123456 --crc32 deadbeef
```

The read-only mapper returns every matching archive entry and its full SHA-256.
Size plus sampled CRC can collide; candidates must not be treated as unique
identities or proof of the crash. Start with suspected archives rather than
scanning a large directory during a GPU job. It reports decode failures rather
than silently dropping candidates.

After reproduction, close the launcher/game, preserve the log and remove the
variable in the launching PowerShell with `Remove-Item Env:ACV_RENDER_TRACE` and
remove `renderer-trace.enabled` if it was used.
Detailed trace logs can grow quickly and flushing each line slows loading; normal
failure logging remains available with tracing off. Logs contain no texture pixels
or account credentials, but may reveal memory addresses and local session details.

If needed, also preserve the relevant Windows Application Error/Windows Error
Reporting event and exact failure time. A graphics probe that releases one texture
at a time does not test cumulative game residency. The x86 client has finite virtual
address space even on a high-VRAM GPU; 2x dimensions usually cost about 4x storage,
with extra managed-resource and alpha-cache copies. Memory pressure is a hypothesis
until the HRESULT/memory evidence supports it. Never raise limits or remove format
checks to hide a failure. Narrow suspect archives in a closed, permitted test copy
using verified known-working backups; don't overwrite active outputs or assume a
folder called `original-data` is still pristine.

## Memory-pressure corrections and remaining limits

A retained menu-navigation failure reported `CREATE_END hr=0x8007000e`
(`E_OUTOFMEMORY`) with about 51 MiB available process virtual memory, despite
roughly 27 GiB available physical RAM and a successful device-cap query. This
supports x86 address-space exhaustion/fragmentation for that session; it does not
identify every retained allocation, prove a leak, or explain every other CTD.

The scoped bundle enables these four narrow corrections; they are also covered
by offline tests. The comprehensive default build remains inactive:

- A recognized tagged upload with at most one stored mip, requested `mips=0`,
  usage zero, managed pool, no color key and no DDS mip skipping uses one GPU
  level. Explicit mip requests, multi-level payloads and other callers are not
  changed. Zero otherwise tells D3DX to generate a full chain; see the
  [Microsoft parameter documentation](https://learn.microsoft.com/en-us/windows/win32/direct3d9/d3dxcreatetexturefromfileinmemoryex).
  For large 2D textures this avoids roughly 25% of full-chain texel storage,
  without changing level-zero pixels, physical resolution, format, pool or alpha.
- The alpha cache uses the signature-checked client malloc entry, not the
  client's throwing operator-new wrapper. The native destructor retains its
  matching free. Cache and handled input allocations are bounded at 64 MiB;
  a returned allocation failure does not publish a partially built alpha cache.
  Client CRT new-handler policy still applies if `_newmode` is enabled; this is
  not an unconditional nonthrowing-allocation guarantee.
- Handled upload failures leave deterministic native dimensions and a null
  texture, rather than uninitialized caller image-info dimensions. CPU lifecycle
  tests exercise caps/upload/descriptor/metadata failures, reference ownership,
  alpha allocation failure and cache reuse/cleanup.

- Archive-cache eviction preserves the full archive ID in callee-saved ESI
  across native tree erase/free, rather than saving only its low byte. Previously,
  evicting archive 262 could remove its cache entry but clean archive 6's mapping,
  leaving 262's view outside cache accounting. The existing unsigned `<500`
  cleanup guard remains. The patch does not widen the byte local next to the
  filename buffer, change call targets, or change stack cleanup. Exact original
  bytes and relevant callee ABI signatures are required. Installation failure
  rolls back the scoped transaction before threads resume; successful scoped
  activation adds `CACHE_TEST_V1: full-width eviction IDs; loader-boundary publication; archive I/O unchanged`.
  Offline native instruction tests cover single-node and seven-node tree erasure,
  allocator/Win32 boundaries, filename and neighboring table preservation.
  CPU tests cover guard bytes, the separate empty-cache offline policy, and
  publication/write/rollback failures. An empty-cache check and installer interlock
  alone do not establish quiescence: the scoped bundle instead uses the
  [loader/thread-exclusion boundary](CACHE-TEST.md#what-makes-publication-different),
  including pending-return checks. The multi-byte write is not inherently atomic;
  the boundary's assumptions and fail-stop limits still apply.

### Archive mapping and fallback-I/O candidate

The candidate also implements a non-evicting, **256 MiB page-charged view
budget**, independently of the client's cache count. It does not reduce texture
resolution, change DDS formats, switch D3D pools or change archive bytes:

- Recognized resource-load calls create no new whole-file views. They use the
  client's fallback path, corrected to read the exact **stored** byte count at
  an explicit offset, not the decoded/raw count. Each candidate read reopens a
  private overlapped handle and waits for completion before releasing it; it
  never seeks or closes the native shared handle. Stored data can exceed raw data.
  A larger input uses the same client malloc/free family and stays owned by the
  native resource destructor. Short reads, failed reopens/reads/allocations and invalid
  metadata clear the owned input pointer before native decoding; returning a
  failed `ReadFile` result alone is insufficient because the client ignores it.
- Temporary index views cover the validated wire index extent for formats
  0/1/2/3/5/6. The two IDs retained by the native loader (snapshot: 1 and 37)
  retain **whole-file** views because subsequent resource loads borrow
  `view + payload_offset`. They are never silently downgraded to prefixes.
- Views are tracked by base address and page charge. Budget exhaustion refuses
  a new mapping; it never evicts a potentially borrowed view. Failed unmaps
  retain their charge, and unowned unmaps do not decrement this accounting.
- The native decode scratch allocation is at least the larger of its original
  `float(raw) * 1.2f` size and the stored size. Raw/stored inputs remain bounded
  at 64 MiB; the existing multiplier can require about 76.8 MiB of scratch.
- x86 thunks capture the original caller stack and ESI/EDI/EBP before entering
  C++. Recognized callers and exact native ABI bytes are checked; unrelated
  I/O callers forward unchanged. Installation covers seven writes with reverse
  rollback, not an atomic multi-write transaction.

**This remains disabled.** An empty mapping table does not establish safe
startup. The index loader caches its mapping function pointer, so late IAT
replacement is not sufficient. Prefix-view lifetime, index/resource phase
exclusion, native handle-table lifetime and borrower drain must be proven before
activation. Private explicit-offset reads remove this helper's shared-cursor
race, not these other hazards. Reopening can fail on access/sharing restrictions;
a stalled filesystem can block the synchronous completion wait. The accounting
lock protects the registry, not native users.

A negative native replay publishes an index-only prefix in the shared view table
and then opens a resource: the native loader skips `MapViewOfFile` and reads
beyond that prefix. This reproduces an unsafe candidate interleaving, **not the
retained live CTD**. Locking only the mapping wrapper cannot exclude this path.
A separate retained-view fixture successfully reads, seeks and destroys its
owned decoded resource after native view cleanup, narrowing that fixture's
borrow to open/decode rather than the entire resource-object lifetime. It does
not prove all direct-field users or concurrent cleanup safe.
Do not change the source gate merely because these offline tests pass.

Compiled-thunk replay tests exercise native open/decode/destruction, retained
view reuse, failure cleanup and the native encoded-index parser with exact
prefix/logical-allocation read/write bounds. Plain/variant-1 prefix checks do not
replay their outer native parser; neither test replays global index publication,
Windows fragmentation, concurrent native callers or login. A separate Windows
CPU test verifies 256 reads across four threads and an unchanged original cursor;
modeled pending-I/O tests require completion before private-handle close and
buffer release. Neither test establishes native loader synchronization.
Catalog checks compare wire extents and report planned page charges, not observed
live memory peaks. Undecodable opaque records are explicitly marked not replayed,
not counted as native passes.
See [offline commands and scope](../docs/diagnostics.md#compiled-archive-io-candidate).

These changes **do not guarantee the full enhanced pack fits**. Doubling both
axes still increases base texture storage about fourfold; managed backing,
P8/BC7 expansion, transient buffers and client objects remain. The native loaders
ignore failed upload HRESULTs, and deterministic outputs do not make every later
null-texture consumer safe. No automatic downscale, lossy format change,
allocation retry, pool switch or crash-swallowing handler is added.

A successful test of one configuration is not precise CTD attribution or a general
memory-fit guarantee.
Preserve native backups, earlier failure evidence and each new test outcome. A
standalone probe is not live-client validation.

## Controlled in-game tests and rollback

Follow [the scoped two-DLL guide](CACHE-TEST.md), not an old single-DLL procedure
or the inactive default build. Only use a permitted test copy: [AyoDance rule 6
prohibits game modification and lists permanent blocking](../docs/modding-policy.md).
A successful technical test is not permission. No protection bypass is provided.

Close the game/launcher before archive or DLL replacement. Keep verified native
backups and the exact pre-test configuration, including both same-project DLLs.
Test layout, hover/click areas, transparency, menus and gameplay; record the route,
log session and result. Stop and roll back on refusal, a broken screen or crashes.
Never overwrite unrelated wrappers, vendor/system DLLs or the client EXE.

For DLL-only rollback, keep DATA unchanged and restore the recorded prior bundle.
For full native rollback, restore every changed archive **and loose image** from
its verified backup before removing this shim/helper; enhanced assets without
layout support are not a valid native baseline. Use the actual installation
journal for the affected set, not an unrelated test's archive list. Preserve
newer/generated user files and do not blindly copy an older backup over them.
No EXE restoration is needed: this workflow never edits it.
