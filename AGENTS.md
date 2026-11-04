# Agent instructions

## Start here

Read `README.md`, `CONTRIBUTING.md` and the relevant guide before changing a workflow:
`docs/acv-unpacking.md`, `docs/upscaling.md`, `docs/diagnostics.md` or
`renderer_patch/README.md`. Historical commands are provenance, not current instructions.

- `acv_tools/`: maintained Python tools for plain ACV and tags 1, 2, 3, 5, 6.
- `tests/`: regression/CLI tests. `research/`: offline investigation; never a production dependency.
- `renderer_patch/`: experimental, client-specific x86 Direct3D shim—not a general renderer or protection bypass.

## Code and checks

- Work from the repo root with its own `.venv`, Python 3.11+ and package-relative imports.
  Invoke `python -m acv_tools.<module>` or `python -m research.<module>`.
- Keep changes small; test changed logic and update affected commands/docs.
- Keep Windows workers importable and process startup behind the main guard.
- Preserve path/trust-boundary checks, overwrite refusal and the **64 MiB** limit
  per stored/decoded payload and decoded index. Native Blowfish/Rijndael and format
  constants must not be replaced as a security cleanup.

Safe default (no converter/GPU workload):

```powershell
.\.venv\Scripts\python.exe -B -m unittest tests.test_acv_unpack tests.test_acv_pack tests.test_stage_renderer_pilot tests.test_cli -v
```

Also run relevant synthetic/mocked upscaler, DAT, manifest and metadata tests for
changed logic; see `CONTRIBUTING.md`. **Full discovery can launch texconv/ESRGAN**,
including `test_ai_legacy_pixel_formats` and `test_resize_repack_verify_and_resume`.
Do not run integration tests, native builds, graphics probes or research recovery
for documentation-only changes. Report checks run/skipped; offline checks do not
establish live-game compatibility.

## Data and running jobs

- Native source backups are read-only. Installed game data may already be enhanced;
  never enhance an enhanced source. Publish separate staged outputs, never overwrite
  originals or automatically install archives, DLLs or EXEs into the game.
- Before starting/stopping a batch, inspect its job record if present, progress,
  lock state and **live command lines**. Never trust a recorded PID alone.
- One job per staging/cache. Do not edit active pipeline code or move, clean, rebuild
  or install active outputs. Ordinary source/docs work must not restart/interrupt a
  batch without the user's request.
- Prefer closing the game for GPU work. Running both needs explicit user approval
  and a contention/device-error warning; never close the game without permission.
- Preserve receipts, texture manifests, `.texture-cache/`, logs and recovery folders.
  Resume/reuse requires hashes, tool/model/code fingerprints and the renderer contract,
  not names/counts. Never relabel old receipts to bypass mismatches.
- Preset: UltraSharpV2 DAT2 on all DDS RGB, then **2×, cap 2048**; resize original
  alpha separately and preserve pixel formats/mip policy. State dictionaries use CPU;
  trusted FP32 ONNX uses DirectML GPU 0 (CPU-reference spot checks, not exhaustive
  image validation). Do not use the
  artifact-producing PyTorch/DirectML path. Hardware examples are not requirements.
- Keep cached DDS untagged; tag staged enlargements with native-layout metadata.
  The current compatible scoped bundle needs both `d3d9.dll` and
  `acv_patch_boundary.dll` beside `Audition.exe`; the default DLL stays inactive.
  Verify decoded replacement bytes and byte-identical untouched stored payloads;
  report every failure.

## Git and evidence

Keep game assets, outputs/caches, models, tools, screenshots, recovered evidence,
`.venv`, native builds and credentials out of Git. Respect `.gitignore`, inspect
staged filenames and never force-add proprietary assets. Git is not their backup.

Ask before destructive cleanup. Preserve matching recovery checkpoints, bootstrap
artifacts, paired Ghidra projects and research dependency snapshots. Check references
before organizing reports, retain historical audit contents and hash-verify moves;
new decompilation reports do not necessarily supersede older ones.
