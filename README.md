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

Put your original archive backups in `original-data/DATA/`. The commands below
use example archive numbers; substitute files from your own installation.
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
and mipmap policy are preserved. Valid palette-indexed P8 DDS and mislabeled PSD
images stay byte-identical at native resolution and are listed in `preserved_images`.
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

Keep `staging/.metadata/` receipts, `.texture-cache/` and any `texture-manifest/`:
resume and future updates depend on them. Successful runs remove their progress
files; failures retain them. Never run two jobs against the same staging/cache,
change the pipeline, clean outputs or install files while a batch is running.
The CLI writes `batch-report.json`; verify its results, not just the file count.

## Using enlarged textures in game (experimental)

**Technical requirement, not permission to mod:** enlarged, tagged textures need
this repo's compatible `renderer_patch` shim to preserve native UI size and hit
areas. Replacing ACV files alone is not enough. Obtain written publisher permission
before deployment; the official modification ban above still applies.

For a permitted test on the supported client build:

1. Follow the [renderer build and graphics checks](renderer_patch/README.md).
   The build produces **`renderer_patch/build/d3d9.dll`**; no prebuilt DLL is
   included. Do not build or install while an upscale batch is active.
2. Close the game and back up the working archives and any existing DLL outside
   the game folder. Copy the built **`d3d9.dll` beside `Audition.exe`**, not into
   `DATA/`. If an older copy of **this same shim** is there, replace it only after
   backing it up. Do not blindly overwrite an unrelated wrapper or vendor DLL;
   stop if you cannot identify the existing file. Never replace a Windows system
   DLL, `d3dx9_43.dll`, or the client EXE.
3. Manually copy only the verified, tagged test archives from `staging/` into the
   test copy's `DATA/`, backing up each corresponding archive before replacing it.
   Do not copy caches, receipts or texture manifests into the game.
4. Check `renderer-patch.log` beside the DLL. `ACTIVE_V64` confirms hook
   installation, not full compatibility; `INACTIVE` means stop and roll back.
   Follow the renderer guide's failure handling, visual checks and rollback steps.

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
