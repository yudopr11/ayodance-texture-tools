# Local diagnostics and retained evidence

`diagnostics/` is local, Git-ignored storage. It is **not included with a source
checkout** and Git is not its backup. Normal extraction/repacking does not need
these files. Preserve a separate copy if you need offline recovery or provenance.

## Layout

```text
diagnostics/
  reports/                 ACV scans, validation reports and texture comparisons
  decompilation/           Native archive and renderer Ghidra text reports
  audit/                   Cleanup, migration and organization records
  bootstrap-static/        Recovered bootstrap bytes and identity report
  checkpoint-v64/          Matching CPU checkpoint, recovered image and trace
  ghidra-projects/         Retained Ghidra analysis databases
  python-re-libs/          Original offline research dependency snapshot
  texture-demo/262/        Twelve extracted example DDS files
  texture-demo-staging/    Example rebuilt 262.acv
```

The organization pass moved **20 loose files** into the first three directories.
All **238 pre-existing files** were SHA-256 checked before/after; no files were
deleted or changed. `audit/organization.json` records the moved paths and hashes.
The manifest itself is a new file, not part of that 238-file comparison.

## Reports

- `reports/native-index-scan-v2.json`: all 330 original archive indexes.
- `reports/native-payload-samples-v3.json`: sampled decoded payload validation.
- `reports/real-pack-roundtrip-v1.json`: real archive replacement/preservation tests.
- `reports/texture-path-preflight.json`: texture extraction path checks.
- `reports/acv-sampling.json`, `acv-full-verification.json`,
  `acv-boundary-research.json`, `acv-mask-research.json`: earlier format research.
  These describe their historical probe capabilities, not the current unpacker's limits.
- `reports/effect-original-vs-ai.json` and its matching PNG: texture comparison.
- `decompilation/ghidra-native-acv-v1.txt` through `v3.txt`: different archive-code
  investigation coverage, not interchangeable duplicate logs.
- `decompilation/ghidra-render-v1.txt` through `v3.txt`: different renderer-code coverage.

`audit/` preserves the original cleanup/migration records unchanged. Embedded paths
record where files existed at the time; they were not rewritten during organization.
Historical documentation may also use old locations. The absent
`ghidra-bootstrap-all-functions.txt` was removed in an earlier cleanup and is not a
current report.

## What not to delete casually

- Keep **all** of `checkpoint-v64/`: `image.bin` does not replace `checkpoint.zip`.
  Checkpoint restoration validates the client, bootstrap, Unicorn binary/version
  and host pointer width. Reinstalling a different emulator build can make it incompatible.
- Keep `bootstrap-static/` at its existing path; current research code hashes it.
- Keep each Ghidra `.gpr` with its associated `.rep` directory. A zero-byte `.gpr`
  is not evidence that the project is empty or disposable.
- Keep `python-re-libs/` if checkpoint reproducibility matters. Production tools do
  not import it. Its presence alone does not install those packages into `.venv`.
- Reports and traces with different version suffixes can cover different routines;
  do not assume the latest version supersedes the others.

The retained tree was about **410 MiB** before adding the organization manifest;
98.6% was checkpoint/bootstrap/database/dependency data. No surviving scratch logs
were proven redundant, so this cleanup organized evidence rather than deleting it.

## Running research commands

Run from the project root; do not run an old root-level script path:

```powershell
$env:PYTHONPATH = "$PWD/diagnostics/python-re-libs"
.\.venv\Scripts\python.exe -B -m research.checkpoint_loader --self-test
.\.venv\Scripts\python.exe -B -m research.unpack_client_bootstrap --self-test
.\.venv\Scripts\python.exe -B -m research.probe_acv --self-test
```

Actual recovery/resume requires the exact trusted client/evidence used to create
that checkpoint. Do not load downloaded/untrusted checkpoints. Ghidra scripts are
under `research/ghidra_scripts/`; their caller-supplied output paths should now point
to `diagnostics/decompilation/` for new text reports. Ghidra and a JDK are separate
local installations.

The early `research.probe_acv` sampler is **not** the complete archive decoder. Its
default report destination is `diagnostics/reports/acv-sampling.json`, and it refuses
to overwrite an existing report. For normal work, use the
[maintained unpacker and packer](acv-unpacking.md).
