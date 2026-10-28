"""Build the x86 experimental shim and standalone tests; never install or launch the game."""
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
    subprocess.run(common + ['renderer_patch/texture_metadata_test.cpp', '-o',
                             str(BUILD / 'texture_metadata_test.exe')], cwd=ROOT, check=True)
    subprocess.run([str(BUILD / 'texture_metadata_test.exe')], cwd=ROOT, check=True)
    subprocess.run(common + windows + ['-shared', 'renderer_patch/d3d9_proxy.cpp',
                   'renderer_patch/d3d9.def', '-o', str(BUILD / 'd3d9.dll')], cwd=ROOT, check=True)
    subprocess.run(common + windows + ['renderer_patch/gpu_probe.cpp', '-o',
                                       str(BUILD / 'gpu_probe.exe')], cwd=ROOT, check=True)
    print('Built experimental x86 DLL and standalone probe; no game files were installed.')
    print('Close full-screen D3D applications, then run:')
    print(f'"{BUILD / "gpu_probe.exe"}" "{ROOT.parent / "d3dx9_43.dll"}" "{BUILD / "d3d9.dll"}"')


if __name__ == '__main__':
    main()
