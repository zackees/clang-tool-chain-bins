#!/usr/bin/env python3
"""Validate one real clang-extra archive on its native runner."""

from __future__ import annotations

import argparse
import json
import os
import platform as host_platform
import struct
import subprocess
import tarfile
import tempfile
from pathlib import Path

import pyzstd

REQUIRED_TOOLS = ("clang-format", "clang-query", "clang-tidy", "git-clang-format", "run-clang-tidy")
COMPILED_TOOLS = ("clang-format", "clang-query", "clang-tidy", "clangd")
PE_MACHINE_X86_64 = 0x8664
PE_MACHINE_ARM64 = 0xAA64
PE_MACHINE_BY_ARCH = {"x86_64": PE_MACHINE_X86_64, "arm64": PE_MACHINE_ARM64}


def _binary_name(name: str, target: str) -> str:
    return name + (".exe" if target == "win" else "")


def _extract(archive: Path, destination: Path) -> None:
    if archive.stat().st_size < 1024:
        raise AssertionError(f"{archive} is an LFS pointer or otherwise not a real archive")
    with archive.open("rb") as raw, pyzstd.ZstdFile(raw) as compressed, tarfile.open(fileobj=compressed, mode="r|") as tar:
        tar.extractall(destination)


def read_pe_machine(executable: Path) -> int:
    with executable.open("rb") as stream:
        header = stream.read(64)
        if len(header) < 64 or header[:2] != b"MZ":
            raise AssertionError(f"{executable} is not a PE executable")
        pe_offset = struct.unpack_from("<I", header, 0x3C)[0]
        stream.seek(pe_offset)
        pe_header = stream.read(6)
    if len(pe_header) != 6 or pe_header[:4] != b"PE\0\0":
        raise AssertionError(f"{executable} has an invalid PE header")
    return struct.unpack_from("<H", pe_header, 4)[0]


def validate_pe_machine(executable: Path, expected_arch: str) -> None:
    expected = PE_MACHINE_BY_ARCH[expected_arch]
    actual = read_pe_machine(executable)
    if actual != expected:
        raise AssertionError(
            f"{executable} has PE machine 0x{actual:04x}; expected 0x{expected:04x} for {expected_arch}"
        )


def validate(
    archive: Path,
    target: str,
    expected_major: str,
    run_check: bool = True,
    expected_arch: str | None = None,
) -> None:
    with tempfile.TemporaryDirectory(prefix="clang-extra-validate-") as temporary:
        root = Path(temporary)
        _extract(archive, root)
        bin_dir = root / "bin"
        required = [*_REQUIRED_NAMES(target)]
        missing = [name for name in required if not (bin_dir / name).is_file()]
        if missing:
            raise AssertionError(f"archive is missing tools: {', '.join(missing)}")
        compiled = [bin_dir / _binary_name(name, target) for name in COMPILED_TOOLS]
        clangd = compiled[-1]
        resource_headers = root / "lib" / "clang" / expected_major / "include"
        if not resource_headers.is_dir():
            raise AssertionError(f"archive is missing Clang resource headers: {resource_headers}")
        if target == "win":
            if expected_arch is None:
                raise AssertionError("Windows validation requires --expected-arch")
            for executable in compiled:
                validate_pe_machine(executable, expected_arch)
        if target != "win" and not clangd.stat().st_mode & 0o111:
            raise AssertionError(f"{clangd} is not executable")
        environment = os.environ.copy()
        if target == "win":
            # Executables are addressed by absolute path. Restrict PATH so a
            # developer-installed LLVM cannot mask a missing archive DLL.
            environment["PATH"] = str(bin_dir)
        elif target == "linux":
            environment["LD_LIBRARY_PATH"] = str(root / "lib") + os.pathsep + environment.get("LD_LIBRARY_PATH", "")
        elif target == "darwin":
            environment["DYLD_LIBRARY_PATH"] = str(root / "lib") + os.pathsep + environment.get("DYLD_LIBRARY_PATH", "")
        for executable in compiled:
            version_result = subprocess.run([str(executable), "--version"], capture_output=True, text=True, env=environment)
            if version_result.returncode != 0:
                dependency_output = ""
                if target == "darwin":
                    dependency_output = subprocess.run(
                        ["otool", "-L", str(executable)], capture_output=True, text=True
                    ).stdout
                raise AssertionError(
                    f"{executable.name} --version failed with exit code {version_result.returncode}:\n"
                    f"{version_result.stdout}\n{version_result.stderr}\n{dependency_output}"
                )
            version = version_result.stdout + version_result.stderr
            if f"version {expected_major}." not in version and f"LLVM {expected_major}" not in version:
                raise AssertionError(f"unexpected {executable.name} version: {version.strip()}")

        if target == "linux":
            dependencies = subprocess.run(["ldd", str(clangd)], check=True, capture_output=True, text=True).stdout
            if "not found" in dependencies:
                raise AssertionError(f"unresolved Linux dependency:\n{dependencies}")
        elif target == "darwin":
            dependencies = subprocess.run(["otool", "-L", str(clangd)], check=True, capture_output=True, text=True).stdout
            if "not found" in dependencies:
                raise AssertionError(f"unresolved macOS dependency:\n{dependencies}")

        if run_check:
            fixture = root / "fixture.cpp"
            fixture.write_text("#include <stddef.h>\nint main() { return 0; }\n", encoding="utf-8")
            (root / "compile_commands.json").write_text(
                json.dumps([{"directory": str(root), "file": fixture.name, "arguments": ["clang++", "-std=c++20", "-target", "wasm32-wasi", "-nostdinc++", "-fsyntax-only", "-c", fixture.name]}]),
                encoding="utf-8",
            )
            check = subprocess.run(
                [str(clangd), f"--check={fixture.name}", f"--compile-commands-dir={root}"],
                cwd=root,
                capture_output=True,
                text=True,
                env=environment,
            )
            if check.returncode != 0:
                raise AssertionError(
                    f"clangd --check failed with exit code {check.returncode}:\n{check.stdout}\n{check.stderr}"
                )


def _REQUIRED_NAMES(target: str) -> tuple[str, ...]:
    compiled = tuple(_binary_name(name, target) for name in ("clang-format", "clang-query", "clang-tidy", "clangd"))
    return (*compiled, "git-clang-format", "run-clang-tidy")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--platform", choices=("win", "linux", "darwin"), required=True)
    parser.add_argument("--expected-major", required=True)
    parser.add_argument("--expected-arch", choices=("x86_64", "arm64"))
    parser.add_argument("--skip-check", action="store_true")
    args = parser.parse_args(argv)
    validate(args.archive, args.platform, args.expected_major, not args.skip_check, args.expected_arch)
    print(f"validated {args.archive} on {host_platform.platform()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
