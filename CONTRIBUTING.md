# Contributing

Thanks for helping improve the tools. Start with the [user guide](./README.md) and
[AGENTS.md](./AGENTS.md) for repository conventions and safety requirements.

## Development setup

Run from the repository root. Use this project's environment, not a sibling
project's `.venv`. Python **3.11 or 3.12** is recommended for the full workflow.

```powershell
uv venv --python 3.12
uv pip install --python .venv/Scripts/python.exe -r requirements-upscale.txt numpy==1.26.4
```

NumPy is needed for the mocked ONNX pixel/padding tests; no runtime/model is needed.
Optional AI backends use `requirements-dml.txt` (Windows ONNX/DirectML) or
`requirements-dat.txt` (CPU DAT). You do not need models, converters or game assets
for the synthetic tests below. See [upscaling setup](docs/upscaling.md) if needed.

## Where things live

| Directory | Purpose |
| --- | --- |
| `acv_tools/` | Maintained Python unpacker, packer, upscaler and texture manifests |
| `tests/` | Synthetic regression tests, CLI checks and opt-in integration tests |
| `renderer_patch/` | Experimental x86 Direct3D shim and native tests |
| `research/` | Offline investigation scripts and Ghidra scripts |
| `docs/` | Format guides, diagnostics and historical research |

Game assets, models, external tools and research evidence are not shipped with
this repository. Setup/output paths in examples are user-created. Production tools
must not depend on `research/` or private evidence.

## Safe tests

These commands use synthetic data and mocks, not a real converter or GPU workload:

```powershell
.\.venv\Scripts\python.exe -B -m unittest tests.test_acv_unpack tests.test_acv_pack tests.test_stage_renderer_pilot tests.test_cli -v
.\.venv\Scripts\python.exe -B -m unittest tests.test_dat_upscale tests.test_texture_manifest tests.test_staging_metadata tests.test_preserved_psd tests.test_preserved_p8 tests.test_preserved_volume tests.test_exceptional_upscale tests.test_bc7_upscale tests.test_renderer_diagnostics -v
```

For relevant upscaler changes, also run the synthetic/mocked methods in
`tests.test_acv_upscale`. **Do not blindly run full discovery**: when local tools
are installed, `test_ai_legacy_pixel_formats` and
`test_resize_repack_verify_and_resume` launch real external tools, including ESRGAN.
Only opt into integration tests when no batch is active:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

Native builds, graphics probes and live-game tests have separate instructions in
the [renderer guide](renderer_patch/README.md). The build runs seven native CPU
executables and creates an inactive default DLL plus a distinct
[two-DLL scoped bundle](renderer_patch/CACHE-TEST.md); only the latter is the
current locally tested game configuration. Neither DLL is needed for offline
Python work. Do not run builds or GPU probes merely to verify documentation.
See [minimal logging](renderer_patch/README.md#minimal-logging-default) for tracing
controls and their next-launch behavior. Passing Python or GPU-probe checks
does not prove live-game compatibility. Report exactly which checks ran and which
did not.

## Making a change

- Keep the change focused and add a regression test for changed logic.
- Use package-relative imports and invoke tools with `python -m acv_tools.<module>`
  or `python -m research.<module>` from the repository root.
- Keep worker functions importable and process startup behind the main guard for
  Windows multiprocessing.
- Preserve path checks, overwrite refusal, error reporting and the 64 MiB payload
  and decoded-index limits. Legacy ciphers/constants implement the archive format;
  they are not candidates for a modern-cipher replacement.
- Repacked replacements must decode to the intended bytes; untouched stored
  payloads must remain byte-identical. Never silently skip failures.
- Pipeline fingerprints include implementation files. Code changes can invalidate
  resume/cache compatibility; never rewrite receipts to hide a mismatch.

Before starting, stopping or changing a batch, inspect its job record when
present, progress files, lock state and **live process command lines**. Custom
runners may create job records; the normal CLI does not always do so. A stored PID
or a README status is not proof that a job is running. Never run a second job on
the same cache, edit active pipeline code, or clean/move/install active outputs.
Keep original archives read-only and preserve receipts, caches, logs and recovery
evidence. Nothing should automatically install into the game directory.

## Research

Ordinary extraction/repacking needs no Ghidra, emulator, dump or commercial tool.
For research-only work, read [diagnostics](docs/diagnostics.md) and the
[historical notes](docs/acv-research-history.md). Some scripts need `unicorn` and
`pefile`; install them separately into the project environment. Native replay
also needs the exact trusted recovered image and dependencies expected by that
script; these artifacts are not supplied by the repository.

Ghidra scripts live in `research/ghidra_scripts/`; obtain Ghidra and a compatible
JDK separately. Preserve recovered evidence and paired Ghidra projects; ask before
destructive cleanup.

## Before opening a pull request

1. Explain the problem, the change and the checks you ran.
2. Update user-facing commands or docs when interfaces change.
3. Run `git diff --check` and inspect staged filenames with `git diff --cached --name-only`.
4. Do not commit game archives, extracted textures, dumps, models, downloaded
   binaries, native build outputs, credentials or private screenshots. Do not
   force-add ignored assets. Git is not their backup.

Contributions must be yours to license under this repository's [MIT License](LICENSE),
or have clearly identified compatible permissions and retained notices. Do not
submit proprietary code/assets or assume recovered game material can be relicensed.
Third-party models/tools keep their own licenses. MIT does not authorize game
modification or asset redistribution; see the [policy and rights summary](docs/modding-policy.md).
