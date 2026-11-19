"""Build the optional native C crypto library for acv_tools.acv_crypto."""

import platform
import shutil
import struct
import subprocess
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
ROOT = PACKAGE.parent
SOURCE = PACKAGE / "_acv_crypto_native.c"
ZIG = ROOT / "tools/zig-python/ziglang/zig.exe"


def native_library_path():
    if sys.platform == "win32":
        return PACKAGE / "_acv_crypto_native.dll"
    if sys.platform == "darwin":
        return PACKAGE / "_acv_crypto_native.dylib"
    return PACKAGE / "_acv_crypto_native.so"


def zig_target():
    bits = struct.calcsize("P") * 8
    machine = platform.machine().lower()
    if machine in ("amd64", "x86_64", "x64"):
        arch = "x86_64" if bits == 64 else "x86"
    elif machine in ("arm64", "aarch64"):
        arch = "aarch64" if bits == 64 else "arm"
    elif machine in ("x86", "i386", "i686"):
        arch = "x86"
    else:
        raise ValueError(f"Unsupported architecture: {machine} ({bits}-bit)")
    if sys.platform == "win32":
        return f"{arch}-windows-gnu"
    if sys.platform == "darwin":
        return f"{arch}-macos"
    return f"{arch}-linux-gnu"


def build():
    output = native_library_path()
    pending = output.with_suffix(output.suffix + ".pending")
    if ZIG.is_file():
        command = [
            str(ZIG), "cc", "-target", zig_target(),
            "-std=c99", "-O3", "-Wall", "-Wextra", "-Werror",
            "-shared", str(SOURCE), "-o", str(pending),
        ]
    else:
        compiler = shutil.which("clang") or shutil.which("gcc") or shutil.which("cc")
        if not compiler:
            raise SystemExit(
                "No C compiler found. Install Zig locally with:\n"
                "  uv pip install --target tools/zig-python --no-deps ziglang==0.14.1"
            )
        command = [
            compiler, "-std=c99", "-O3", "-Wall", "-Wextra", "-Werror",
            "-shared", *([] if sys.platform == "win32" else ["-fPIC"]),
            str(SOURCE), "-o", str(pending),
        ]
    try:
        subprocess.run(command, cwd=ROOT, check=True)
        pending.replace(output)
    finally:
        pending.unlink(missing_ok=True)
        pending.with_suffix(".lib").unlink(missing_ok=True)
        output.with_suffix(".lib").unlink(missing_ok=True)
    return output


def main():
    output = build()
    print(f"Built native ACV crypto library: {output}")


if __name__ == "__main__":
    main()
