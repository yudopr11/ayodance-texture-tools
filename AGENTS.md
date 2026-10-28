# Agent instructions

## Project and layout

This project unpacks, upscales and repacks AyoDance ACV texture archives. Supports
plain archives and tagged formats 1, 2, 3, 5 and 6. The renderer shim is an
experimental, client-specific companion—not a general renderer or protection bypass.

- `acv_tools/`: maintained Python tools; use package-relative imports.
- `tests/`: unittest regression tests and CLI smoke tests.
- `renderer_patch/`: x86 Direct3D shim, C++ tests, graphics probe and build script.
- `research/`: offline investigation scripts and `ghidra_scripts/`; production
  tools must not depend on this package.
- `docs/`: current format/diagnostics guides and explicitly historical research.

Read `README.md` and the relevant guide before changing a workflow:
`docs/acv-unpacking.md`, `docs/diagnostics.md`, or `renderer_patch/README.md`.
Historical research commands and paths are provenance, not current instructions.

## Working conventions

- Run commands from the repository root. Use the local `.venv` and Python 3.11+
  for the complete workflow. Do not borrow a sibling project's environment.
- Invoke Python tools with `python -m acv_tools.<module>` or
  `python -m research.<module>`, not obsolete root-level script paths.
- Keep changes small and consistent with surrounding code. Add a focused test for
  changed logic; update documented commands when interfaces or paths change.
- Preserve Windows multiprocessing compatibility: worker functions must remain
  importable and process startup must stay behind the main guard.
- Keep trust-boundary validation, path checks, size limits and overwrite refusal.
  The archive safety limit is 64 MiB per stored/decoded payload and decoded index.
- Legacy Blowfish/Rijndael code implements the native archive format. Do not
  replace it with a modern cipher or alter format constants as a security cleanup.

## Archive, cache and running-job safety

- Treat `original-data/DATA/` as read-only input. Installed `../DATA` may already
  contain enhanced/tagged archives; it is not an untouched upscale source.
- Publish separate staged outputs. Never overwrite originals or automatically
  install archives, DLLs or EXEs into the game directory.
- Before starting/stopping a batch, inspect `staging-esrgan-2x/job.json`, progress
  files and the actual process command line. A stored PID or README status can be
  stale. Never kill a process based solely on a previously recorded PID.
- Do not start a second job against the same staging/cache directory. Do not move,
  clean, rebuild or install active job outputs. Ordinary source/docs work must not
  restart or interrupt a running batch without the user's request.
- Prefer closing the game during GPU processing. If the user explicitly chooses
  to run both, explain GPU contention/device-error risk; do not close the game
  without permission.
- Preserve `.texture-cache/`, verified receipts, recovery directories and logs.
  Resume acceptance depends on source/output hashes, tool/model fingerprints and
  the renderer contract—not archive filenames or file counts alone.
- Current preset: Real-ESRGAN x4plus RGB enhancement followed by 2× final dimensions,
  capped at 2048 on the longest side; resize original alpha separately. Preserve
  DDS pixel formats and mipmap policy. Do not AI-upscale an already-enhanced source.
- Enlarged DDS files need native-layout metadata and the compatible shim to avoid
  oversized UI. Keep cached images untagged; add metadata when staging replacements.
- Repacked replacements must decode to the intended bytes; untouched stored
  payloads must remain byte-identical. Report failures, never silently skip them.
  Offline verification does not establish full live-game compatibility.

## Checks

Safe default regression command (PowerShell, no real converter/GPU workload):

```powershell
.\.venv\Scripts\python.exe -B -m unittest tests.test_acv_unpack tests.test_acv_pack tests.test_stage_renderer_pilot tests.test_cli -v
```

Also run relevant synthetic/mocked methods in `tests.test_acv_upscale` when changing
upscaling logic. **Full test discovery can launch installed external tools and
ESRGAN**: `test_ai_legacy_pixel_formats` and
`test_resize_repack_verify_and_resume` are integration tests, not safe defaults
while a GPU batch is active. Do not start integration tests, graphics probes,
research recovery or native builds merely to verify a documentation change.

Research self-checks may require `unicorn`/`pefile` or the local
`diagnostics/python-re-libs` folder on `PYTHONPATH`; see the diagnostics guide.
Renderer builds and GPU checks have separate instructions and are not substitutes
for live client validation. Report exactly which checks ran and which did not.

## Local assets and Git

Keep originals, staged archives/caches, models, downloaded tools, screenshots,
recovered evidence, `.venv` and native build outputs out of Git. Respect
`.gitignore`; inspect staged filenames before committing and never force-add
proprietary assets or credentials. The RX 9070 XT is the owner's example hardware,
not a requirement for other users.

Git is not a backup for ignored assets. Do not delete diagnostic evidence as
scratch output: preserve `checkpoint-v64/`, `bootstrap-static/`, Ghidra project
pairs and the matching research dependency snapshot. Versioned decompilation
reports can cover different functions rather than supersede each other. Organize
reports only after checking references; preserve historical audit contents and
verify moved evidence with hashes. Ask before destructive cleanup.
