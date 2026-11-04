# Scoped archive-cache DLL test

**Locally tested, not exhaustively validated or a universally proven CTD fix.**
The controlled A/B procedure below is the starting point for each client or
changed build; a successful test elsewhere does not establish compatibility. This bundle preserves all
texture/archive bytes, dimensions, formats and level-zero pixels. It tests the
full-width archive-ID eviction correction together with the existing tagged
renderer, single-mip and alpha-allocation corrections. It does **not** activate
the experimental prefix views, mapping budget, fallback-I/O or scratch hooks.
The comprehensive default build remains deliberately inactive.

## Bundle

Build from the repository root with:

```powershell
.venv/Scripts/python.exe -B renderer_patch/build.py
```

Use **both files from `renderer_patch/build/cache-test/`**:

- `d3d9.dll`
- `acv_patch_boundary.dll`

**Both matching DLLs are required together beside `Audition.exe` when using this
bundle with enlarged textures.** The helper is not optional. Neither is needed
for offline unpacking, upscaling or repacking.

That folder also contains `TESTING.md` and `manifest.json` with SHA-256 hashes.
Do not use `build/d3d9.dll` (inactive), or anything in `build/boundary-test/`
(a synthetic CPU-test host, not a renderer).

The build runs seven standalone CPU test executables, including real Windows
loader/thread-exclusion tests and injected publication/rollback failures. The
native cache instruction replay is separate research and requires a matching,
trusted recovered client image that is not supplied in this repository. See the
[research prerequisites](../docs/diagnostics.md).

With the game, GPU jobs and full-screen D3D applications closed, the optional
GPU/export check is:

```powershell
& "$PWD/renderer_patch/build/gpu_probe.exe" "$PWD/../d3dx9_43.dll" "$PWD/renderer_patch/build/cache-test/d3d9.dll"
```

That probe must refuse native installation into its own unsupported executable;
its upload checks are not live-client validation.

## What makes publication different

The renderer prepares signatures, original pointers and the alpha trampoline,
then loads its sibling helper from an explicit path. The helper's process-attach
callback holds the Windows loader lock, excluding normal new application-thread
entry. Inside that boundary the renderer enumerates and suspends other existing
threads, checks their instruction pointers and ordinary stacks, and checks the
installing caller's stack for the affected native instruction sequences.
Stack inspection walks to the TEB's stack base across readable committed regions.
Pending native returns into the cache correction are refusal witnesses too.

With execution excluded, it rechecks every dependency signature and original
patch destination, publishes the three changes, checks instruction-cache flushes
and restores protections. Recoverable failures roll back before threads resume.
The helper requires an acknowledged, one-shot request on the installing thread;
successful `LoadLibrary` alone is not activation.

This is specific to the recovered x86 client and ordinary Windows 10/11 thread
startup/call stacks, including the locally tested WOW64 environment. It does not
support injected/context-redirected threads, native thread-creation exemptions,
fibers or other alternate execution contexts. Missing APIs, inaccessible threads,
more than 512 enumerated threads, unreadable/guarded or over-16-MiB stack scans,
changed signatures or an unexpected cache count cause refusal, not a protection
bypass. The scoped publisher's three destinations occupy distinct pages.

**Fail-stop risk:** if rollback cannot restore a coherent instruction/protection
state, or a suspended thread cannot be resumed, the candidate terminates its own
process rather than continuing with a partial patch. It does not swallow crashes.
The broader address-space/residency problem may still cause a CTD even when this
cache defect is corrected.

## Manual A/B test (not performed by the build)

Only test where publisher permission permits it; the
[modification ban and account risk](../docs/modding-policy.md) still apply.
Do not bypass the normal launcher or protection. No EXE or system DLL changes.

1. Close the game/launcher and finish any texture job. Back up the identified
   older copy of **this shim**, its log and any same-project boundary helper
   outside the game folder. Do not replace an unrelated wrapper/vendor DLL.
2. Keep the existing archive configuration unchanged for the first A/B test.
   Do not change texture settings, downscale assets or expand the archive set
   during this DLL-only comparison.
3. Manually place the bundle's **two DLLs together beside `Audition.exe`** in
   the permitted test copy. Nothing in this repository installs them for you.
4. Launch normally. In that session's `renderer-patch.log`, require both:

   ```text
   ACTIVE_V64: tagged-only texture dimensions and ABI-preserving alpha query enabled
   CACHE_TEST_V1: full-width eviction IDs; loader-boundary publication; archive I/O unchanged
   ```

   `INACTIVE` means **stop and restore the previous DLL configuration**, not
   continue with enhanced textures lacking layout support. A missing or stale
   helper is not optional. An activation log is not proof the CTD is fixed.
5. Reproduce the same hall, ranking, mall and studio route. Check layout,
   hover/click areas, transparency and gameplay, and retain the new log and
   precise outcome/time. Use `renderer-trace.enabled` beside the DLL only for a
   short reproduction if per-upload tracing is needed.
6. Close the game before rollback. Restore the backed-up shim/log configuration
   and remove only this test's helper if no helper existed before. Leave DATA
   untouched for this DLL-only comparison. Preserve the failure log separately.

For a new configuration, do not expand the archive set until this controlled
comparison has a recorded outcome. Neither a successful test, the CPU tests nor
a standalone GPU probe proves every enhanced pack fits in the x86 client's
address space.

## Minimal logging

No rebuild or DLL replacement is needed to disable verbose tracing. Before the
next launch, clear `ACV_RENDER_TRACE` from the launching environment and move
`renderer-trace.enabled` away from the DLLs. Either trigger enables verbose mode;
`ACV_RENDER_TRACE=0` does not override a present marker. The current running session
keeps its sampled setting until exit. Keep both DLLs installed.

The next session should report `trace=0` plus `ACTIVE_V64` and `CACHE_TEST_V1`.
Startup/refusal/failure messages remain; no supported switch disables all logging.
Old log contents are not automatically removed. After closing the game/launcher,
archive the old log if you want the next launch to start a small new one. See the
[logging guide](README.md#minimal-logging-default) for details.
