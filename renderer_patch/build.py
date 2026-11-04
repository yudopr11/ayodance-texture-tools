"""Build the x86 experimental shim and standalone tests; never install or launch the game."""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ZIG = ROOT / 'tools/zig-python/ziglang/zig.exe'
PATCH = ROOT / 'renderer_patch'
BUILD = PATCH / 'build'


def main():
    if not ZIG.is_file():
        raise SystemExit('Install build tool: uv pip install --target tools/zig-python --no-deps ziglang==0.14.1')
    BUILD.mkdir(exist_ok=True)
    common = [str(ZIG), 'cc', '-target', 'x86-windows-gnu', '-std=c++17',
              '-Wall', '-Wextra', '-Werror', '-O2', '-fno-exceptions', '-fno-rtti']
    # Win32 GetProcAddress explicitly returns a generic function pointer.
    windows = ['-Wno-cast-function-type-mismatch', '-luser32']
    # C-only MSVC COFF avoids the pinned GNU target's broken x86 finally emission.
    seh = [str(ZIG), 'cc', '-target', 'x86-windows-msvc', '-nostdlib', '-nostdinc',
           '-fms-extensions', '-fexceptions', '-Wall', '-Wextra', '-Werror', '-O2', '-c']
    for name in ('archive_phase_seh', 'archive_phase_seh_test', 'archive_phase_unwind_test'):
        subprocess.run(seh + [f'renderer_patch/{name}.c', '-o', str(BUILD / f'{name}.obj')],
                       cwd=ROOT, check=True)
    subprocess.run([str(ZIG), 'cc', '-target', 'x86-windows-gnu',
                    str(BUILD / 'archive_phase_seh.obj'), str(BUILD / 'archive_phase_seh_test.obj'),
                    '-o', str(BUILD / 'archive_phase_seh_test.exe')], cwd=ROOT, check=True)
    subprocess.run([str(BUILD / 'archive_phase_seh_test.exe')], cwd=ROOT, check=True)
    print('Archive phase SEH boundary: PASS (handled/continued exceptions, outward unwind, nested cleanup)')
    subprocess.run(common + ['renderer_patch/archive_phase_test.cpp',
                    str(BUILD / 'archive_phase_seh.obj'), str(BUILD / 'archive_phase_unwind_test.obj'),
                    '-o', str(BUILD / 'archive_phase_test.exe')], cwd=ROOT, check=True)
    subprocess.run([str(BUILD / 'archive_phase_test.exe')], cwd=ROOT, check=True)
    subprocess.run(common + ['renderer_patch/texture_metadata_test.cpp', '-o',
                             str(BUILD / 'texture_metadata_test.exe')], cwd=ROOT, check=True)
    subprocess.run([str(BUILD / 'texture_metadata_test.exe')], cwd=ROOT, check=True)
    subprocess.run(common + ['renderer_patch/archive_io_test.cpp', '-o',
                             str(BUILD / 'archive_io_test.exe')], cwd=ROOT, check=True)
    subprocess.run([str(BUILD / 'archive_io_test.exe')], cwd=ROOT, check=True)
    subprocess.run(common + windows + ['renderer_patch/renderer_trace_test.cpp', '-o',
                                       str(BUILD / 'renderer_trace_test.exe')], cwd=ROOT, check=True)
    subprocess.run([str(BUILD / 'renderer_trace_test.exe')], cwd=ROOT, check=True)
    subprocess.run(common + windows + ['renderer_patch/renderer_lifecycle_test.cpp', '-o',
                                       str(BUILD / 'renderer_lifecycle_test.exe')], cwd=ROOT, check=True)
    subprocess.run([str(BUILD / 'renderer_lifecycle_test.exe')], cwd=ROOT, check=True)
    # Fixed, disjoint emulation address; this fixture is never loaded into the game or host.
    subprocess.run(common + windows + ['-shared', 'renderer_patch/archive_io_emulator.cpp',
                   '-Wl,--export-all-symbols', '-Wl,--image-base,0x07000000', '-o',
                   str(BUILD / 'archive_io_emulator.dll')], cwd=ROOT, check=True)
    subprocess.run(common + windows + ['-shared', 'renderer_patch/d3d9_proxy.cpp',
                   'renderer_patch/d3d9.def', '-o', str(BUILD / 'd3d9.dll')], cwd=ROOT, check=True)
    # Isolated synthetic DLL host: never use this fixture as the game shim.
    boundary_test = BUILD / 'boundary-test'
    boundary_test.mkdir(exist_ok=True)
    subprocess.run(common + windows + ['-shared', 'renderer_patch/patch_boundary.cpp',
                   '-o', str(BUILD / 'acv_patch_boundary.dll')], cwd=ROOT, check=True)
    subprocess.run(common + windows + ['-shared', '-DACV_BOUNDARY_TEST_HOST',
                   'renderer_patch/patch_boundary_test.cpp', 'renderer_patch/patch_boundary_test.def',
                   '-o', str(boundary_test / 'd3d9.dll')], cwd=ROOT, check=True)
    subprocess.run(common + windows + ['renderer_patch/patch_boundary_test.cpp', '-municode',
                   '-o', str(BUILD / 'boundary_cpu_test.exe')], cwd=ROOT, check=True)
    subprocess.run([str(BUILD / 'boundary_cpu_test.exe'), str(boundary_test / 'd3d9.dll'),
                    str(BUILD / 'acv_patch_boundary.dll')], cwd=ROOT, check=True, timeout=30)
    candidate = BUILD / 'cache-test'
    candidate.mkdir(exist_ok=True)
    (candidate / 'manifest.json').unlink(missing_ok=True)
    subprocess.run(common + windows + ['-shared', '-DACV_SCOPED_CACHE_TEST',
                   'renderer_patch/d3d9_proxy.cpp',
                   'renderer_patch/d3d9_cache_test.def', '-o', str(candidate / 'd3d9.dll')],
                   cwd=ROOT, check=True)
    shutil.copyfile(BUILD / 'acv_patch_boundary.dll', candidate / 'acv_patch_boundary.dll')
    subprocess.run(common + windows + ['renderer_patch/gpu_probe.cpp', '-o',
                                       str(BUILD / 'gpu_probe.exe')], cwd=ROOT, check=True)
    shutil.copyfile(PATCH / 'CACHE-TEST.md', candidate / 'TESTING.md')
    files = ('d3d9.dll', 'acv_patch_boundary.dll', 'TESTING.md')
    manifest = {'status': 'experimental-cache-test-v1-not-live-validated',
                'scope': 'renderer corrections and full-width cache IDs; archive I/O unchanged',
                'files': {name: hashlib.sha256((candidate / name).read_bytes()).hexdigest() for name in files}}
    (candidate / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print('Built experimental x86 DLL and standalone probe; no game files were installed.')
    print('Default d3d9.dll remains inactive; scoped cache experiment: build/cache-test/ (two DLLs).')
    print('Close full-screen D3D applications, then run:')
    print(f'"{BUILD / "gpu_probe.exe"}" "{ROOT.parent / "d3dx9_43.dll"}" "{BUILD / "d3d9.dll"}"')


if __name__ == '__main__':
    main()
