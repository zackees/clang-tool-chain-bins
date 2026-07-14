#!/usr/bin/env python3
"""Validate one real clang-extra archive on its native runner."""

from __future__ import annotations

import argparse
import json
import os
import platform as host_platform
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

import pyzstd

REQUIRED_TOOLS = ("clang-format", "clang-query", "clang-tidy", "git-clang-format", "run-clang-tidy")


def _binary_name(name: str, target: str) -> str:
    return name + (".exe" if target == "win" else "")


def _extract(archive: Path, destination: Path) -> None:
    if archive.stat().st_size < 1024:
        raise AssertionError(f"{archive} is an LFS pointer or otherwise not a real archive")
    with archive.open("rb") as raw, pyzstd.ZstdFile(raw) as compressed, tarfile.open(fileobj=compressed, mode="r|") as tar:
        tar.extractall(destination)


def validate(archive: Path, target: str, expected_major: str, run_check: bool = True) -> None:
    with tempfile.TemporaryDirectory(prefix="clang-extra-validate-") as temporary:
        root = Path(temporary)
        _extract(archive, root)
        bin_dir = root / "bin"
        required = [*_REQUIRED_NAMES(target)]
        missing = [name for name in required if not (bin_dir / name).is_file()]
        if missing:
            raise AssertionError(f"archive is missing tools: {', '.join(missing)}")
        clangd = bin_dir / _binary_name("clangd", target)
        if target != "win" and not clangd.stat().st_mode & 0o111:
            raise AssertionError(f"{clangd} is not executable")
        environment = os.environ.copy()
        if target == "linux":
            environment["LD_LIBRARY_PATH"] = str(root / "lib") + os.pathsep + environment.get("LD_LIBRARY_PATH", "")
        elif target == "darwin":
            environment["DYLD_LIBRARY_PATH"] = str(root / "lib") + os.pathsep + environment.get("DYLD_LIBRARY_PATH", "")
        version = subprocess.run(
            [str(clangd), "--version"], check=True, capture_output=True, text=True, env=environment
        ).stdout
        if f"version {expected_major}." not in version and f"LLVM {expected_major}" not in version:
            raise AssertionError(f"unexpected clangd version: {version.strip()}")

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
            fixture.write_text("#include <cstddef>\nint main() { return 0; }\n", encoding="utf-8")
            (root / "compile_commands.json").write_text(
                json.dumps([{"directory": str(root), "file": str(fixture), "arguments": ["clang++", "-std=c++20", "-target", "wasm32-wasi", "-c", str(fixture)]}]),
                encoding="utf-8",
            )
            subprocess.run(
                [str(clangd), f"--check={fixture.name}", f"--compile-commands-dir={root}"],
                check=True,
                cwd=root,
                capture_output=True,
                text=True,
                env=environment,
            )


def _REQUIRED_NAMES(target: str) -> tuple[str, ...]:
    return tuple(_binary_name(name, target) for name in (*REQUIRED_TOOLS, "clangd"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--platform", choices=("win", "linux", "darwin"), required=True)
    parser.add_argument("--expected-major", required=True)
    parser.add_argument("--skip-check", action="store_true")
    args = parser.parse_args(argv)
    validate(args.archive, args.platform, args.expected_major, not args.skip_check)
    print(f"validated {args.archive} on {host_platform.platform()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
