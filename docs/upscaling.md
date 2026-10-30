# Upscaling and incremental updates

Run these PowerShell commands from the repository root, using this project's `.venv`.
For initial setup, see the [user quick start](../README.md). Models, converters and
original game assets must be obtained separately.

## Output sizing

The current comparison preset uses **UltraSharpV2 (DAT2) for every DDS texture**,
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
Valid Photoshop PSD images mislabeled `.dds` remain byte-identical and are listed
separately in `preserved_images` in receipts and manifests, not DDS counts. They
are not AI-upscaled or converted during staging or incremental updates. Unknown
or malformed image payloads still fail explicitly.

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
  textures stay non-mipmapped. Unsupported cube/volume/DX10 formats fail explicitly.
- Native layout metadata requires the [experimental v64 renderer shim](../renderer_patch/README.md).
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
