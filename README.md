# AyoDance ACV texture tools

Extract, edit, upscale and repack AyoDance textures. Supports plain ACV archives
and tagged formats **1, 2, 3, 5 and 6**.

These are command-line tools, not a ready-to-install texture pack. Game files,
models and third-party executables are **not included**.

## Before you start

- Use your own **untouched backup** of the game's archives. Keep it separate from
  the installed game; already-enhanced files are not suitable upscale inputs.
- Start with one archive. Outputs are staged separately; nothing installs itself.
- Enlarged textures can break UI layout without the compatible
  [experimental renderer shim](renderer_patch/README.md). It supports a specific
  client build, not every AyoDance version. Offline verification is not proof of
  live-game compatibility. **AyoDance rule 6 prohibits game modifications and lists
  a permanent block.** These tools are not approved for the live service; see the
  [official rules and license limits](docs/modding-policy.md).

## 1. Set up

Examples use **Windows PowerShell**, Python **3.11 or 3.12**, and
[uv](https://docs.astral.sh/uv/getting-started/installation/).
Download or clone this repository, then run from its root:

```powershell
uv venv --python 3.12
uv pip install --python .venv/Scripts/python.exe -r requirements-acv.txt
```

The commands below use user-created `original-data/DATA/`, `texture-work/` and
`staging/` directories. Supply your own untouched archive backups and substitute
archive numbers from your installation; none of these assets are included.
No game process or reverse-engineering tools are needed to extract or repack.

## 2. Extract and edit textures

```powershell
.\.venv\Scripts\python.exe -B -m acv_tools.acv_unpack original-data/DATA/262.acv --textures-only --output texture-work
```

Edit the DDS files under `texture-work/262/`, keeping their relative paths.
Existing extraction folders are refused. See the [format guide](docs/acv-unpacking.md)
for supported formats and limits.

## 3. Repack into a separate output

```powershell
New-Item -ItemType Directory -Force staging | Out-Null
.\.venv\Scripts\python.exe -B -m acv_tools.acv_pack original-data/DATA/262.acv texture-work/262 staging/262.acv
# Check decoding without installing or extracting the result:
.\.venv\Scripts\python.exe -B -m acv_tools.acv_unpack staging/262.acv --textures-only
```

Only provided files are replaced; other stored payloads are preserved byte-for-byte.
Existing output archives are refused. Keep your backups and inspect staged results
before considering installation.

## Optional: AI upscaling

The UltraSharpV2 preset enhances all DDS textures, then produces **2× dimensions**,
capped at **2048 pixels on the longest side**. Original alpha, DDS pixel formats
and mipmap policy are preserved, with explicit handling for unusual formats:
P8 DDS is enhanced and requantized to a shared RGBA palette; a mislabeled PSD's
saved composite becomes BGRA DDS; depth-one DXT volumes retain their volume
container. Single 2D BC7_UNORM/straight-alpha DX10 DDS is decoded to BGRA8 for
D3D9 compatibility, retaining the mip policy. These need the updated renderer below. True multi-slice 3D volumes
remain native and are listed in `preserved_images`.
AI can distort small text, and tiled inference
can introduce seams. Doubling both dimensions uses roughly 4× the texture memory.

For Windows GPU processing:

1. Get `texconv.exe` from [Microsoft DirectXTex](https://github.com/microsoft/DirectXTex/releases)
   and place it in `tools/`.
2. Get the author's **`4x-UltraSharpV2_fp32_op17.onnx`** from
   [Kim2091/UltraSharpV2](https://huggingface.co/Kim2091/UltraSharpV2) and place it
   in `tools/ultrasharp/`. Do not substitute the Lite model.
3. Close the game, install the optional backend, and try one archive:

```powershell
uv pip install --python .venv/Scripts/python.exe -r requirements-dml.txt
.\.venv\Scripts\python.exe -B -m acv_tools.acv_upscale original-data/DATA/039.acv staging --model tools/ultrasharp/4x-UltraSharpV2_fp32_op17.onnx --workers 1
```

This uses **ONNX/DirectML GPU adapter 0**. UltraSharpV2 has a **CC-BY-NC-SA-4.0**
license; review its terms before using or sharing outputs. Use only trusted model
files. See the [upscaling guide](docs/upscaling.md) for checksums, CPU support,
legacy ESRGAN, resume behavior and reusing textures after game updates.

Keep the generated receipts, image cache and texture manifests: resume and future
updates depend on them. Completed receipts record verified output; failures
retain in-flight progress. Never run two jobs against the same staging/cache,
change the pipeline, clean outputs or install files while a batch is running.
The CLI writes `batch-report.json`; verify its results, not just the file count.

## Using enlarged textures in game (experimental)

**Technical requirement, not permission to mod:** enlarged, tagged textures need
this repo's compatible `renderer_patch` shim to preserve native UI size and hit
areas. Replacing ACV files alone is not enough. Obtain written publisher permission
before deployment; the official modification ban above still applies.

**Two builds, different scopes:** the default comprehensive build remains
[deliberately inactive](renderer_patch/README.md#current-candidate-is-deliberately-inactive).
A separate [two-DLL cache test bundle](renderer_patch/CACHE-TEST.md) enables the
renderer and full-width cache-ID correction with guarded publication, without
changing enhanced textures or enabling the prefix/I/O experiment. **Both matching
DLLs—`d3d9.dll` and `acv_patch_boundary.dll`—are required beside `Audition.exe`**
for this bundle, not just `d3d9.dll`. Follow its dedicated build, installation,
log checks and rollback instructions—not the inactive single-DLL build. Neither
DLL is needed for offline unpacking/upscaling/repacking. No prebuilt DLLs are shipped.

On **2026-11-04**, the owner reported an enhanced set of 330 ACVs, 48 loose DDS
and 43 JPG working with the scoped bundle. See the [recorded scope and limits](renderer_patch/README.md#current-local-validation--2026-11-04).
This is one client's successful test, not exhaustive compatibility, a proven cause
for every earlier CTD, or publisher permission.

For a permitted test, close the game and any upscale batch first. Back up the
working archives and DLLs outside the game folder. Install only verified outputs
and the matching bundle beside `Audition.exe`; never overwrite an unidentified
wrapper, Windows system DLL, `d3dx9_43.dll` or the client EXE. Keep receipts,
caches and manifests out of the game. `ACTIVE_V64` confirms installation, not
full compatibility; follow the bundle's stop/rollback rules on failure.

`renderer-patch.log` defaults to startup and failure messages only. For
[minimal logging](renderer_patch/README.md#minimal-logging-default), clear
`ACV_RENDER_TRACE` and move `renderer-trace.enabled` out of the game folder before
the next launch. Verbose tracing is optional; disabling it does not disable either
DLL or its patches. There is no supported fully-off logging switch.

## More information

- [Contributing and developer setup](CONTRIBUTING.md)
- [Archive format, validation and troubleshooting](docs/acv-unpacking.md)
- [Upscaling and incremental updates](docs/upscaling.md)
- [Experimental renderer: build, testing and rollback](renderer_patch/README.md)

Local game assets, tools and generated outputs are Git-ignored, not backed up by
Git. Keep separate backups and do not upload proprietary assets or credentials.

## License

Author-owned code and documentation use the [MIT License](LICENSE). Game assets,
trademarks, external tools and models are not covered by that grant and retain
their own rights and terms. MIT does not authorize game modification or asset
redistribution. See [modding policy and third-party rights](docs/modding-policy.md).
