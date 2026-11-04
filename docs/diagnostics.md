# Diagnostics and offline research

Normal extraction, repacking and synthetic Python tests do not need a game
process, Ghidra, a recovered image or private research artifacts. Start with the
[archive guide](acv-unpacking.md) and [developer checks](../CONTRIBUTING.md).
This repository supplies research source, not client images, checkpoints,
analysis databases, dependency snapshots or recorded reports.

## Renderer diagnostics

Use the [matching two-DLL bundle](../renderer_patch/CACHE-TEST.md) for a permitted
live test. The comprehensive prefix/I/O candidate remains inactive; its test
fixtures are not game DLLs. A successful test of one configuration is not proof
of compatibility or every earlier crash's cause.

For minimal logging, clear `ACV_RENDER_TRACE` from the launching environment and
move the sibling `renderer-trace.enabled` marker away before the next launch.
Either trigger enables verbose tracing. The setting is sampled once per session;
a running game does not change modes mid-session. Startup and failure messages
remain; there is no supported fully-off switch. Close the game and launcher
before archiving a log. See [logging controls](../renderer_patch/README.md#minimal-logging-default).

`acv_tools.renderer_diagnostics` maps logged byte-count/CRC fingerprints to
candidate entries in an exact matching archive. Use the installed bytes or a
byte-identical copy, not the untouched source revision. Sampled CRCs can collide;
inspect every match and retain full hashes. See the
[diagnostic procedure](../renderer_patch/README.md#diagnosing-a-broken-screen-failed-load-or-crash).

## Research prerequisites and evidence safety

Run research modules from the repository root with `python -m research.<module>`.
Some need separately installed `unicorn` and `pefile`; native replay also needs
the exact trusted recovered image expected by the script. Check its input and
identity checks before running it. These are additional prerequisites, not files
available in a source checkout. Do not load untrusted checkpoints.

Ghidra and a compatible JDK must be obtained separately. The tracked scripts are
in `research/ghidra_scripts/`. Supply your own matching analysis project, absolute
input/output paths and a new report filename. Use read-only analysis for surveys;
a static report does not establish that a failing session executed that path.
No research result authorizes changing client protection or bypassing its launcher.

Preserve matching checkpoint/bootstrap data, dependency versions and paired
Ghidra project/database files. A recovered image alone may not reproduce a
checkpoint. Reports with different version suffixes can cover different routines;
do not assume the latest supersedes the others. Keep hashes and input identities
with each result, retain partial failures, and ask before destructive changes.

## Bounded native memory-path emulation

`InspectAcvAllocation.java` surveys native allocation/eviction instructions and
startup ordering. `EmulateAcvMemoryPaths.java` checks short instruction slices for
archive-cache ID truncation and the scratch-allocation multiplier. These are
synthetic-state checks, not a complete parser, heap simulation or post-login replay.
Startup ordering alone is not thread quiescence or proof of a patching race.

The native fixtures have distinct boundaries:

| Module | Executed path | Important limitation |
| --- | --- | --- |
| `research.emulate_native_inflate` | Native zlib with bounded modeled allocations | Not the complete resource loader |
| `research.emulate_prelogin_archives` | Preflight loop and resource-owner predicate | Lookup/open/crypto are modeled; starts after index initialization |
| `research.emulate_archive_open` | Native resource open/release, cache tree, eviction and inflate | Index, crypto, heap and Windows APIs are modeled |
| `research.emulate_archive_index` | Tagged index envelope, permutation, inflate and record normalization | Plain/variant-1 indexes are not native-parser passes; no full global initializer |
| `research.emulate_texture_consumer` | DDS reads/seeks/copies and D3DX argument construction | Stops before D3DX; no GPU, complete login or cumulative residency |
| `research.test_archive_cache_patch` | Native cache iterator/erase/free wrappers and neighboring slots | Requires the matching recovered image; Windows cleanup APIs are modeled |

A zero modeled mapping budget is not zero process virtual memory. Planned view
charges are not observed simultaneous mappings. Neither a passing flag nor an
injected allocation failure establishes Windows fragmentation, exception safety,
live compatibility or crash attribution. Do not bypass preflight failure branches.

## Compiled archive-I/O candidate

The [native build](../renderer_patch/README.md#files-and-reproducible-build) generates a separate CPU-only
archive-I/O fixture. `research.test_archive_io` maps its compiled PE sections and
imports into Unicorn; it does not load that fixture into the host or game.
The replay requires both generated fixture binaries and the matching trusted
recovered image. Building the source alone does not supply the image.

The tests cover compiled x86 stack/register-capture thunks, native resource
open/decode/destruction, retained-view reuse, budget refusal, short reads,
allocation/reopen/read failures and encoded-index record normalization. Native
crypto, lookup, client heap and Windows services remain modeled. Logical live/freed
heap accesses and mapped extents are checked for native reads and writes. The
outer global index publisher and plain/variant-1 native parser are not replayed.

Private explicit-offset reads wait for pending completion before closing their
handle. A Windows CPU test checks concurrent reads without changing the original
handle's cursor. Neither establishes native handle-table lifetime or exclusion
of resource borrowers. Catalog helpers compare format-aware wire extents and
report planned page charges. Undecodable opaque entries are marked
`OPAQUE_NOT_REPLAYED`, not counted as passes; DDS decode failures abort. Catalog
checks do not replace receipt/source binding or authorize installation.

Two lifetime fixtures distinguish an in-progress open from an owned decoded
resource: a published index-only prefix can be borrowed past its extent, while a
successfully decoded retained-view fixture survives later view cleanup. These
are bounded tests, not a replay of a live CTD or proof of every indirect consumer.

Safe native coordination must cover complete index initialization (`5A78F0`),
mutually exclusive resource opens (`5AAF60`), presence queries (`5A99C0`) and
teardown (`5AA430`). Configuration/maintenance must not mutate these structures
during reads. A registry lock or normal-return-only unlock is insufficient.

The standalone C SEH boundary and C++ `ArchivePhase` tests check ownership during
first-pass search, handled/continued exceptions, normal return, outward unwind,
nested cleanup and competing callbacks. The C-only MSVC-target COFF object is
built with Zig and linked into GNU-target tests. This primitive has no native
entry-point routing and does not prove installation safety for the I/O hooks.
The [comprehensive activation gate remains false](../renderer_patch/README.md#current-candidate-is-deliberately-inactive).
Never install a test fixture or bypass that gate because offline tests pass.

The early `research.probe_acv` sampler is not the complete decoder. For normal
archive work, use the maintained unpacker and packer, not research probes.
