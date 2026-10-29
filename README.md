# AyoDance ACV texture tools

Unpack, edit, upscale and repack AyoDance texture archives without modifying the
original input files. Supports plain ACVs and tagged formats **1, 2, 3, 5 and 6**.

**Local batch status:** no full batch running. The current UltraSharpV2 comparison
pilot is under `staging/`; no batch starts automatically.

## Project layout

```text
acv_tools/         Production unpacker, packer, upscaler and pilot stager
research/          Offline loader probes and Ghidra scripts
renderer_patch/    Experimental v64 renderer source, build and rollback guide
tests/             Regression tests and package CLI smoke tests
docs/              Format guide and historical research
```

The following are local, Git-ignored assets—not included with the source:

- `original-data/DATA/`: untouched backup archives; the upscale source.
- `staging/`: current enhanced archives, receipts, `.texture-cache/` and `preview/`.
- Historical staging folders, if retained locally, are not current outputs.
- `tools/`: downloaded converters, ESRGAN models and the local compiler.
- `diagnostics/`: offline evidence and examples; reports are grouped under
  `reports/`, `decompilation/` and `audit/`. See the [diagnostics guide](docs/diagnostics.md).
- `ghidra_12.2_PUBLIC/`, `images/`, `.venv/`: local analysis tools, screenshots and environment.

Git does **not** back up these ignored assets. Keep a separate backup before cleanup
or installing anything into the game. The game directory is outside this project.

## Setup and basic use

Examples use PowerShell from the project root. Replace the example path with your
own checkout location. Python **3.11+** is recommended for all commands; the
upscaler requires it for `hashlib.file_digest`.

```powershell
cd C:\path\to\acv-unpacker-upscaler
uv venv
uv pip install --python .venv/Scripts/python.exe -r requirements-acv.txt

# Extract into a NEW folder. Use your own untouched archive backup.
.\.venv\Scripts\python.exe -B -m acv_tools.acv_unpack original-data/DATA/262.acv --textures-only --output texture-work

# After editing DDS files, build a separate archive, never over the source.
mkdir staging
.\.venv\Scripts\python.exe -B -m acv_tools.acv_pack original-data/DATA/262.acv texture-work/262 staging/262.acv
.\.venv\Scripts\python.exe -B -m acv_tools.acv_unpack staging/262.acv --textures-only

# Non-GPU container, metadata and CLI tests.
.\.venv\Scripts\python.exe -B -m unittest tests.test_acv_unpack tests.test_acv_pack tests.test_stage_renderer_pilot tests.test_cli -v
```

Use `python -m acv_tools.<module>` from the project root, not the old root-level
script paths. See [the format and unpacking guide](docs/acv-unpacking.md).

## AI upscaling for a 1440p / “2K” display

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

- **CPU FP32 only** for `--model` at present: the experimental PyTorch/DirectML DAT
  path produced visible, nondeterministic artifacts on the local AMD setup and is
  not offered as a supported backend. CPU pilot success does not validate GPU use.
  A full CPU batch is much slower than the old Vulkan workflow; inspect the pilot
  before deciding whether to run it.
- One persistent worker loads the model once. Inference uses 128-pixel input tiles
  with 32-pixel context, cropped before stitching. DAT has global attention; tiled
  inference is approximate and can show seams or color shifts.
- `staging/preview/` holds local comparison PNGs, when generated. The comparison
  shows original, Lanczos and actual repacked UltraSharp DDS pixels at equal display
  size. AI may sharpen lettering incorrectly; very small text cannot be recovered
  reliably, and original-alpha preservation can retain a soft outline.
- Fingerprints include the model, converter, implementation, package versions,
  precision and tiling policy. Cached DDS images remain untagged. A changed pipeline
  refuses mismatched receipts; archive/move the old `staging/` safely before reusing
  that one output location for a different model. Never clean an active batch.
- `--model` and `--esrgan` are mutually exclusive. Without either, the legacy cubic
  converter path remains available; it does not use UltraSharp.

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
- Native layout metadata requires the [experimental v64 renderer shim](renderer_patch/README.md).
  It keeps UI layout at native size while uploading enlarged pixels. An unpatched
  client may show oversized/misaligned UI even if the archive itself verifies.
- Each final `NNN.acv` has a `.acv.json` receipt with source/output SHA-256 hashes and
  a pipeline fingerprint (converter/model/backend-specific inputs).
- Identical inputs reuse a checksummed, untagged `.texture-cache/`. A matching rerun
  reuses verified archives; interrupted/unreceipted outputs are moved aside before
  rebuilding. Tool/model changes require a new staging directory.
- Every packed replacement is decoded and compared exactly; untouched stored
  payloads are compared byte-for-byte. Verification is not proof of live-game safety.
- `*.acv.progress.json` records texture progress. The previously launched background
  job also has `batch.log` and `job.json`; those are not created by every CLI launch.
  A full run writes `batch-report.json`. Check **330 verified**, not file count alone.
- Never run two jobs against the same staging/cache directory. Keep the game closed
  and do not install or move output files while a batch is running.

## Tests, research and renderer

Full test discovery also includes real converter/GPU integration tests when their
local executables exist; unlike the basic test command above, it can run ESRGAN:

```powershell
# Safe DAT model/cache/tiling tests (mocked, no model inference):
.\.venv\Scripts\python.exe -B -m unittest tests.test_dat_upscale -v
# Integration tests: deliberately opt in only when no batch is active.
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

The renderer has separate C++ and graphics tests; see its
[build, testing and rollback guide](renderer_patch/README.md). It is an experimental,
client-specific memory patch, not a universal renderer or protection bypass.

Offline probes live in `research/`; normal extraction/repacking does not need
Ghidra, Unicorn or a recovered executable. For local research dependencies, either
install `unicorn` and `pefile` into your environment or expose the existing ignored
folder before running research modules:

```powershell
$env:PYTHONPATH = "$PWD/diagnostics/python-re-libs"
.\.venv\Scripts\python.exe -B -m research.checkpoint_loader --self-test
```

Ghidra scripts are in `research/ghidra_scripts/`; Ghidra and a compatible JDK must be
obtained separately. [Historical notes](docs/acv-research-history.md) retain old paths
as provenance, not current commands.

On my local test installation, renderer-tagged `001`–`021` and `262` were installed
for testing, with backups outside this repository in `../renderer_patch/test-backup/`.
Those installed game archives are **not untouched originals**. Tested UI screens
were confirmed working, but that does not establish exhaustive UI/HUD or gameplay
coverage. Follow the renderer guide before installation or rollback.
