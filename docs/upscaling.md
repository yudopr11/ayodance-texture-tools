# Upscaling and incremental updates

Run these PowerShell commands from the repository root, using this project's `.venv`.
For initial setup, see the [user quick start](../README.md). Models, converters and
original game assets must be obtained separately.

## Current workflow and local validation

The production preset is UltraSharpV2 ONNX/DirectML; CPU DAT is the reference and
Real-ESRGAN remains a separate legacy option. Examples below use a fresh output
folder named `staging/`; the completed local batch instead lives in `staging/DATA/`,
with receipts and the consolidated cache under that directory. Use the actual
output root for new jobs and manifests, not a mix of layouts.

The owner reported the full installed set working on 2026-11-04; see
[renderer validation scope](../renderer_patch/README.md#current-local-validation--2026-11-04).
The current enhanced 262 and receipt are under
`staging/updates/repaired-262-20261104/`, not historical `staging/DATA/262.acv`.
The old root manifest describes its original outputs; never relabel it or replace
its source/output bindings to imply it describes the repaired revision.

Offline upscaling needs no renderer DLL. To use these enlarged textures in the
supported client, the [scoped bundle](../renderer_patch/CACHE-TEST.md) requires
**both matching `d3d9.dll` and `acv_patch_boundary.dll` beside `Audition.exe`**.
The comprehensive default DLL remains inactive. Successful local testing is not
exhaustive compatibility or publisher permission.

## Output sizing

The current preset uses **UltraSharpV2 (DAT2) for every DDS texture**,
including UI, HUD, text and fonts. The model's 4× RGB result is downsampled with
Lanczos to **2× source dimensions**, capped at **2048 pixels on the longest side**.
Alpha comes from the original, resized separately with Lanczos. Textures at or
above the cap remain unchanged. This does not turn every texture into 2048×2048.
Doubling both dimensions uses roughly four times the texture memory.

### UltraSharpV2 comparison (CPU reference)

Install the optional backend into this project's `.venv` (Python **3.11 or 3.12**).
Obtain `4x-UltraSharpV2.safetensors` from the author's
[Hugging Face repository](https://huggingface.co/Kim2091/UltraSharpV2), linked by
[OpenModelDB](https://openmodeldb.info/models/4x-UltraSharpV2), and place it under
`tools/ultrasharp/`. Its license is **CC-BY-NC-SA-4.0**, not PBRify's CC0; check the
license before distributing derivatives or using it commercially. The published
safetensors SHA-256 is `2b5db674aa8f3864a696dccd1d07d7cc0500fbf220f35044cd30eee9a7f971a7`.
Models and converters are not included in Git. Use only trusted state dictionaries.

```powershell
uv pip install --python .venv/Scripts/python.exe -r requirements-dat.txt
# CPU reference: bound thread overhead, then process one small mixed archive.
$env:OMP_NUM_THREADS = '8'
$env:MKL_NUM_THREADS = '8'
.\.venv\Scripts\python.exe -B -m acv_tools.acv_upscale original-data/DATA/039.acv staging --model tools/ultrasharp/4x-UltraSharpV2.safetensors --workers 1
```

- `.safetensors` and `.pth` use **CPU FP32**. The experimental PyTorch/DirectML
  DAT path produced visible artifacts and is not used. For the separately tested
  ONNX/DirectML GPU path, see below. A full CPU batch is much slower; inspect a pilot
  before deciding whether to run it.
- One persistent worker loads the model once. Inference uses 128-pixel input tiles
  with 32-pixel context, cropped before stitching. DAT has global attention; tiled
  inference is approximate and can show seams or color shifts.
- `staging/preview/` holds local comparison images, when generated. The comparison
  shows original, Lanczos and actual repacked UltraSharp DDS pixels at equal display
  size. AI may sharpen lettering incorrectly; very small text cannot be recovered
  reliably, and original-alpha preservation can retain a soft outline.
- Fingerprints include the model, converter, implementation, package versions,
  precision and tiling policy. Cached DDS images remain untagged. A changed pipeline
  refuses mismatched receipts; archive/move the old `staging/` safely before reusing
  that one output location for a different model. Never clean an active batch.
- `--model` and `--esrgan` are mutually exclusive. Without either, the legacy cubic
  converter path remains available; it does not use UltraSharp.

### UltraSharpV2 GPU (ONNX/DirectML)

On Windows, a trusted `.onnx` supplied to `--model` selects **DirectML GPU adapter
0**, not PyTorch. Obtain the author's `4x-UltraSharpV2_fp32_op17.onnx` from the same
Hugging Face repository. SHA-256:
`6c0201e3403745f39a9aa5273c50ed084cf6c4af5a71a4654c30252cf19bf0a5`.
Do not substitute the Lite model. The model's license and final sizing above still apply.

```powershell
uv pip install --python .venv/Scripts/python.exe -r requirements-dml.txt
# Only in an empty staging/ or one belonging to this exact GPU pipeline:
.\.venv\Scripts\python.exe -B -m acv_tools.acv_upscale original-data/DATA/039.acv staging --model tools/ultrasharp/4x-UltraSharpV2_fp32_op17.onnx --workers 1
```

The local four-texture RX 9070 XT preview matched CPU RGB within 1/255 per channel;
alpha was identical and profiling confirmed GPU execution. This is a small pilot,
not full texture or live-game validation. Some shape operations run on CPU; a missing
DirectML provider or whole-session CPU fallback is an error. A startup profile also
rejects sessions with no GPU computation, even if DirectML is registered. One persistent
session uses FP32 and the same tiled inference, alpha, DDS and renderer-metadata policies.
The GPU backend/package/model get a separate fingerprint/cache; CPU receipts cannot
resume as GPU output. Stop and inspect any old batch before preserving its outputs
aside and restarting. Keep the game closed during GPU processing.

### Texture inventory and game updates

After a batch completes, backfill a per-texture log **without AI or texconv**:

```powershell
.\.venv\Scripts\python.exe -B -m acv_tools.texture_manifest build original-data/DATA staging staging/texture-manifest
```

The new manifest directory contains an `index.json` and one `NNN.acv.json` per
verified staged archive. Logs record the ACV location, entry number, internal texture
name/path, decoded source/output/cache SHA-256 hashes, dimensions, DDS pixel format,
mips, model/backend and original pipeline fingerprint. Backfilling checks both
archive hashes against receipts, renderer tags and unchanged non-texture payloads.
It refuses active batch locks and existing manifest directories; preserve old logs.
Exceptional formats now follow an explicit enhancement path:

- **PSD mislabeled `.dds`:** enhance the saved merged composite and emit a tagged
  BGRA8 DDS under the same archive path. Layers are not retained in this replacement;
  keep the untouched original PSD. Alpha is resized separately, not synthesized.
- **P8 DDS:** enhance decoded RGB, resize original alpha, then quantize all generated
  mip levels together to one 256-entry RGBA palette. One entry is reserved for fully
  transparent pixels. Palette color/partial-alpha approximation is lossy; inspect
  edges. The disk format stays P8; the shim temporarily expands it to BGRA8 on upload,
  which costs more GPU memory than native P8.
- **Depth-one DXT1/3/5 volumes:** enhance the sole slice, retaining DXT format, the
  volume header and mip policy on disk. The shim clears the volume-only header fields
  in a temporary 2D upload; it does not discard any additional slice.
- **BC7/DX10:** single 2D BC7_UNORM (DXGI 98), one array element and explicit straight
alpha are decoded to BGRA8 DDS for D3D9 compatibility. Original alpha is resized
separately; mipmapped inputs get the usual full output chain. This intentionally
changes the pixel format and uses more storage/GPU memory than BC7. Other DX10
formats, arrays, cubes and alpha modes still fail. Manifests log both formats.
BC7 inputs already at the size cap fail explicitly: same-size format replacement
needs a separate workflow, rather than silently keeping D3D9-unsupported BC7.
- **True 3D volumes (depth > 1):** validate every compressed slice/mip and preserve
  native bytes as `DDS-volume` in `preserved_images`. There is no validated 3D renderer
  or alpha-hit-test path. Other volume formats, cubemaps, unsupported DX10 and malformed images
  still fail explicitly.

New receipts use renderer contract **`v64-tagged-dimensions-v2`**. P8 and depth-one
volume DDS have version-2 tags and require the compatible two-DLL bundle; an old
version-1-only shim or the inactive default DLL is insufficient. Ordinary/converted PSD/BC7 textures use version-1 tags, which the
new shim also supports. Historical receipts/manifests that preserved these images
remain readable, but are not silently relabeled or considered enhanced. Existing
completed archives are not automatically rebuilt; retain old outputs and request a
separate staged rebuild when replacing historical exceptions.

For a game update, first take a **new untouched input snapshot**, keeping the old
originals and enhanced archives intact. Stage one changed archive into a **new**
subdirectory, never over the previous outputs:

```powershell
.\.venv\Scripts\python.exe -B -m acv_tools.texture_manifest update updated-originals/DATA/061.acv staging/updates/patch-001 --manifest staging/texture-manifest --model tools/ultrasharp/4x-UltraSharpV2_fp32_op17.onnx
```

Unchanged decoded DDS hashes recover verified enhanced bytes from the old archive
into a fresh cache, even after a rename or move between ACVs. Only cache misses
need AI; at-cap textures remain unchanged. The current game's archive is the repack
template, so its new non-texture contents are preserved. `update-report.json` counts
reused/new-or-changed/at-cap textures, and `texture-manifest/` logs the new output
for subsequent updates. No installation occurs. Keep the old originals, staged
archives, receipts, manifests and caches: hashes alone cannot reconstruct images.

Reuse requires the **exact current pipeline fingerprint** by default. A changed
model, converter, backend, dependency or processing policy must not silently reuse
old enhancements. For an audited, compatible code-only change, repeat
`--reuse-pipeline OLD_FINGERPRINT` to explicitly approve older fingerprints. This
is a manual compatibility decision, **not** automatic model/tool equivalence.
The model filename and backend must still match. Exact-current candidates take
precedence; conflicting enhancements otherwise fail rather than depend on manifest
order. Names alone never prove content identity. See the
[update safety notes](acv-unpacking.md#texture-manifests-and-incremental-updates).

### Legacy Real-ESRGAN path

The old all-AI workflow remains available, using **Real-ESRGAN x4plus** for 4× RGB
followed by 2×/cap2048 final sizing. Do not reuse its cache as UltraSharp output.

**Hardware example:** the RX 9070 XT is the GPU used on **my own machine**, not a
requirement for yours. The NCNN Vulkan executable uses GPU index 0 in this pipeline;
your GPU, available memory and performance may differ. A compatible Vulkan GPU and
an appropriate Real-ESRGAN NCNN executable are needed for this AI path.

Obtain the converter from [Microsoft DirectXTex](https://github.com/microsoft/DirectXTex/releases)
and the AI executable/models from [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN/releases).
The existing local setup uses `tools/texconv.exe` and the official
`realesrgan-ncnn-vulkan-20220424-windows` executable/models under `tools/esrgan/`.
These binaries and models are not distributed in this repository.

**Only run this when you deliberately want to start or resume the GPU batch:**

```powershell
uv pip install --python .venv/Scripts/python.exe -r requirements-upscale.txt
# Only in an empty staging/ or one already belonging to this exact pipeline:
.\.venv\Scripts\python.exe -B -m acv_tools.acv_upscale original-data/DATA staging --esrgan tools/esrgan/realesrgan-ncnn-vulkan.exe --workers 1
```

- Use untouched backups as input, **not already-enhanced installed game archives**.
- Enlarged DDS files retain their original pixel format and include native layout
  metadata. Existing mipmapped textures get a full new mip chain; non-mipmapped
  textures stay non-mipmapped. Cubes, unsupported DX10 and unsupported volume formats fail explicitly;
true 3D volumes are validated and retained unchanged as described above.
- Native layout metadata requires the [experimental v64 two-DLL bundle](../renderer_patch/CACHE-TEST.md).
  It keeps UI layout at native size while uploading enlarged pixels. An unpatched
  client may show oversized/misaligned UI even if the archive itself verifies.
- Each final `NNN.acv` has a `.metadata/NNN.acv.json` receipt with source/output
  SHA-256 hashes and a pipeline fingerprint (converter/model/backend-specific inputs).
  Legacy adjacent receipts remain readable; a verified matching rerun moves them
  into `.metadata/`. Two receipts for one archive are rejected, not guessed between.
  Keep receipts: resume and future texture reuse need them.
- Identical inputs reuse a checksummed, untagged `.texture-cache/`. A matching rerun
  reuses verified archives; interrupted/unreceipted outputs are moved aside before
  rebuilding. Tool/model changes require a new staging directory.
- Every packed replacement is decoded and compared exactly; untouched stored
  payloads are compared byte-for-byte. Verification is not proof of live-game safety.
- `*.acv.progress.json` records in-flight texture progress. It is deleted only after
  the archive verifies and its receipt is published, or on a verified matching resume.
  Failed jobs retain progress. Batch reports, logs and recovery evidence are kept.
  The previously launched background job also has `batch.log` and `job.json`;
  those are not created by every CLI launch.
  A full run writes `batch-report.json`. Check **330 verified**, not file count alone.
- Never run two jobs against the same staging/cache directory. Keep the game closed
  and do not install or move output files while a batch is running.
