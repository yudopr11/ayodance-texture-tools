# Experimental v64 high-resolution UI renderer pilot

**Experimental, client-specific, and not exhaustively validated.** On my local test installation, `ACTIVE_V64` and corrected Start/Exit glow sizing were confirmed, followed by working tested UI screens. This does not prove every UI/HUD or gameplay path. The all-archive upscale job is currently stopped at my request; no command below resumes it automatically. Never replace the original client EXE.

## What it changes

The recovered client uses a DDS's dimensions for both its GPU texture and its logical UI layout. Its alpha hit-test also assumes the two dimensions are identical. Enlarging the DDS alone therefore enlarges/misaligns UI geometry and its clickable mask.

This x86 `d3d9.dll` shim forwards Direct3D to Windows and, for the specifically identified v64 texture loaders:

- reads native dimensions from a marker in the DDS reserved header fields;
- uploads the actual enlarged texture, but reports the original dimensions to the UI loader;
- keeps a native-size alpha hit-test cache, using the strongest alpha in each corresponding enlarged pixel block;
- leaves unmarked textures and other D3DX callers unchanged;
- for version-2 P8/depth-one volume tags only, prepares a validated temporary upload:
  expands P8 palette indices to BGRA8, or clears depth-one volume header fields without
  changing DXT blocks. The archive payload itself is not modified by the shim.

This is not a Start/Exit or archive-001 special case: the recovered ordinary DDS loaders apply the same logic to every marked DDS, regardless of filename, widget or archive. The main `acv_tools/acv_upscale.py` pipeline now adds the marker after image-cache publication; the cache stays untagged. Use `original-data/DATA`, not already-enhanced installed archives, as the source. GDI-generated text and code-generated textures use separate paths; this does not sharpen them or prove every UI draw path is covered.

The marker is `ACVUI2X\0` at DDS offset 32. Version 1 covers ordinary 2D DDS
(including offline-flattened PSD composites); version 2 covers validated P8 and
depth-one DXT1/3/5 volumes. Both retain real enlarged width/height fields. New
receipts use renderer contract `v64-tagged-dimensions-v2`; rebuild this DLL for these
outputs, rather than retaining the older version-1-only shim. The new shim still
accepts older ordinary version-1 textures. This is **not** a header-only resolution
disguise or a downscale.

P8 uses one RGBA palette for all mip levels on disk and expands to BGRA8 GPU memory;
palette color and partial-alpha quantization is lossy. The original alpha is resized
outside the AI model. Depth-one volume uploads contain exactly one slice; true 3D
volumes are not flattened or handled by this hook and remain native in the pipeline.
Temporary upload buffers and complete mip payloads are bounded at 64 MiB. The hook
addresses and client signature guard are unchanged. These formats passed offline
GPU upload checks, not live-client call-site/layout validation.

The installer checks the loaded executable's x86 image shape, entry point, loader/call-site bytes, alpha routine and CRT-release thunk before writing memory. It does not change the EXE on disk. Different client builds are refused. Live logs confirm hook execution in this client, but neither static matching nor an `ACTIVE_V64` log proves complete visual/gameplay correctness.

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

The build runs the standalone C++ metadata/alpha tests and creates:

- `renderer_patch/build/d3d9.dll`
- `renderer_patch/build/gpu_probe.exe`

Run the standalone graphics check **with the game and full-screen D3D applications closed**:

```powershell
& "$PWD/renderer_patch/build/gpu_probe.exe" "$PWD/../d3dx9_43.dll" "$PWD/renderer_patch/build/d3d9.dll"
```

Optional additional arguments are paths to tagged DDS samples; each is passed
through the shared `create_tagged` implementation to check physical dimensions,
logical metadata, mip count and native-size alpha construction on the GPU. For
example, append the paths to a staged P8 and depth-one volume DDS after the DLLs.

This probe loads the built DLL, checks its exports and refusal of the wrong executable, parses a marked DDS with the actual D3DX DLL, and attempts real texture creation/locking. It does not launch, attach to, or modify the game. Exit 0 is a pass; exit 3 means GPU checks were blocked. A passing probe still does not validate the live game's patched x86 call sites.

Recorded result on my own machine (not a hardware requirement): the probe was rerun with the game closed and got `CreateDevice HRESULT: 0x00000000` and the full GPU PASS, including physical texture size, logical metadata, alpha mapping and the negative forwarding path. The earlier `0x88760868` (`D3DERR_DEVICELOST`) run was blocked; its precise cause was not established.

## Repacked and installed archives

On my local installation, `staging-renderer-pilot/001.acv` through `021.acv`, plus `262.acv`, were repacked, verified and installed in `../DATA`: **22 archives, 5,675 marked DDS textures**. The installed client EXE and already-tested DLL were unchanged during this expansion. All archive output hashes were checked after copying. The pre-test enhanced archives and an installation manifest are in `../renderer_patch/test-backup/`; these are not the untouched original archives.

`staging-renderer-pilot/ui-hud-audit.json` inventories all **330 original ACV indexes**. Its UI/HUD labels are filename heuristics, not a runtime coverage guarantee or a tagging whitelist. The oversized yellow spectator icon was traced to `texture/gui/방송국/관전아이콘.dds` in `009.acv` (32×32 native, 64×64 enhanced); it is now marked by the same shared mechanism.

Receipt status intentionally remains `experimental-verified-repack-not-game-tested`: this records offline verification, not an unearned claim that every installed archive passed live testing.

To generate another pilot from verified existing ESRGAN output (no AI rerun):

```powershell
.venv/Scripts/python.exe -B -m acv_tools.stage_renderer_pilot original-data/DATA/001.acv staging-esrgan-2x/001.acv staging-renderer-pilot/001.acv
```

The stager refuses existing output, source/receipt hash mismatches, unsupported DDS formats, and occupied reserved fields. The command above will therefore refuse the already-staged pilot rather than overwrite it.

## Controlled in-game test — only after the graphics check passes

Use only a permitted test copy, not a live multiplayer session. An offline copy is not itself permission: [AyoDance rule 6 prohibits game modification and lists permanent blocking](../docs/modding-policy.md). Obtain written permission before deployment. This DLL changes executable memory and may be rejected or flagged by game protection. There is no protection bypass here.

1. Keep a known-working client/data backup. Close the game before any archive or DLL replacement. Never overwrite an unrelated `d3d9.dll` wrapper/mod.
2. After the build and graphics check, copy `renderer_patch/build/d3d9.dll` beside `Audition.exe` in the permitted test copy, not into `DATA/`. Back up and replace an existing DLL only if it is an identified older copy of this same shim; do not overwrite an unrelated wrapper, vendor DLL or Windows system DLL. Manually install only verified, tagged test archives into that copy's `DATA/`, backing up each file before replacement. Do not install active batch outputs or legacy unmarked staged output; receipts must identify the renderer metadata contract. A fresh checkout includes neither built nor installed files.
3. Launch normally. Inspect `renderer-patch.log` beside the copied DLL. `ACTIVE_V64` indicates hook installation, not visual correctness. `INACTIVE` means this executable did not match; stop the test. `FATAL_PARTIAL_INSTALL` means rollback failed; stop the process immediately.
4. Check Start/Exit glows, the yellow spectator icon, lobby and room panels, menus, inventories, text alignment, mouse hover/click boundaries and transparent pixels. Then play a song and check the gameplay HUD, score/combo graphics and results screen. Note any remaining broken screen and texture/log evidence before considering another hook.
5. For a full return to native assets, close the game, restore **all installed pilot archives** from `original-data/DATA` to `../DATA`, then remove **only this pilot's DLL**. The affected archive names are recorded in `../renderer_patch/test-backup/install.json`. Restoring files from `test-backup` instead returns the pre-tag enhanced assets, which can reintroduce oversized UI without the shim. No EXE restoration is needed; the pilot never edits it.

If the client crashes, the guard refuses it, or its protection rejects the shim, roll back the pilot and diagnose the log before expanding to more archives. Do not resume the 330-archive job on the assumption this prototype is confirmed.
