#!/usr/bin/env python3
"""Build the small, runtime-complete ``clang-extra`` distribution.

The input is an extracted official LLVM distribution (or a directory produced
by the Forge fallback).  Keeping the selection here, instead of copying an
already-installed clang, makes the archive reproducible and prevents a host
LLVM installation from leaking into the artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat
import tarfile
import tempfile
from pathlib import Path

import pyzstd

SUPPORTED_TARGETS = {
    ("win", "x86_64"),
    ("linux", "x86_64"),
    ("linux", "arm64"),
    ("darwin", "x86_64"),
    ("darwin", "arm64"),
}
EXTRA_TOOLS = ("clang-format", "clang-query", "clang-tidy", "git-clang-format", "run-clang-tidy", "clangd")
LLVM_DOWNLOAD_URLS = {
    ("win", "x86_64"): "LLVM-{version}-win64.exe",
    ("linux", "x86_64"): "LLVM-{version}-Linux-X64.tar.xz",
    ("linux", "arm64"): "clang+llvm-{version}-aarch64-linux-gnu.tar.xz",
    ("darwin", "x86_64"): "LLVM-{version}-macOS-X64.tar.xz",
    ("darwin", "arm64"): "LLVM-{version}-macOS-ARM64.tar.xz",
}


def expected_binary_name(platform: str) -> str:
    return "clangd.exe" if platform == "win" else "clangd"


def _find_root(directory: Path) -> Path:
    if (directory / "bin").is_dir():
        return directory
    candidates = sorted(p for p in directory.iterdir() if p.is_dir() and (p / "bin").is_dir())
    if len(candidates) == 1:
        return candidates[0]
    raise ValueError(f"could not identify an LLVM root below {directory}")


def _copy_file(source: Path, destination: Path, executable: bool = False) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    if executable:
        destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def stage_clang_extra(
    source_dir: Path,
    staging_dir: Path,
    platform: str,
    arch: str,
    version: str,
    llvm_commit: str | None = None,
) -> dict:
    """Stage tools and all runtime data needed by clangd.

    Missing tools and resource headers are errors: a partial archive is worse
    than a failed release because it otherwise produces an installable but
    unusable index entry.
    """
    if (platform, arch) not in SUPPORTED_TARGETS:
        raise ValueError(f"unsupported clang-extra target: {platform}/{arch}")
    root = _find_root(source_dir)
    bin_dir = root / "bin"
    destination_bin = staging_dir / "bin"
    missing = [name for name in EXTRA_TOOLS if not (bin_dir / (name + (".exe" if platform == "win" else ""))).is_file()]
    if missing:
        raise FileNotFoundError(f"LLVM distribution is missing clang-extra tools: {', '.join(missing)}")

    for name in EXTRA_TOOLS:
        filename = name + (".exe" if platform == "win" else "")
        _copy_file(bin_dir / filename, destination_bin / filename, platform != "win")

    # clangd loads the Clang resource directory and, on Windows/Unix LLVM
    # distributions, sibling runtime libraries from these locations.
    resources = root / "lib" / "clang"
    if not resources.is_dir():
        raise FileNotFoundError(f"LLVM resource directory is missing: {resources}")
    shutil.copytree(resources, staging_dir / "lib" / "clang")
    for pattern in (("*.dll",) if platform == "win" else ("*.so", "*.so.*", "*.dylib")):
        for library in sorted((root / "lib").glob(pattern)):
            _copy_file(library, staging_dir / "lib" / library.name, platform != "win")
    # Windows LLVM releases keep DLLs beside the executables.
    if platform == "win":
        for library in sorted(bin_dir.glob("*.dll")):
            _copy_file(library, destination_bin / library.name)

    return {
        "method": "extracted",
        "llvm_version": version,
        "llvm_project_tag": f"llvmorg-{version}",
        "llvm_project_commit": llvm_commit,
        "llvm_source": "official llvm-project distribution",
        "target": {"platform": platform, "arch": arch},
        "tools": list(EXTRA_TOOLS),
        "build_options": {"mode": "prebuilt-extraction"},
    }


def create_archive(staging_dir: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as raw:
        tar_path = Path(raw.name)
    try:
        def tar_filter(member: tarfile.TarInfo) -> tarfile.TarInfo:
            if member.isfile() and (member.name.startswith("bin/") or "/bin/" in member.name):
                member.mode = 0o755
            elif member.isfile() and (member.name.endswith((".so", ".dylib")) or ".so." in member.name):
                member.mode = 0o755
            return member

        with tarfile.open(tar_path, "w") as archive:
            for path in sorted(staging_dir.rglob("*")):
                archive.add(path, arcname=path.relative_to(staging_dir).as_posix(), recursive=False, filter=tar_filter)
        with tar_path.open("rb") as source, output_path.open("wb") as raw_destination, pyzstd.ZstdFile(
            raw_destination, "w", level_or_option=22
        ) as destination:
            shutil.copyfileobj(source, destination)
    finally:
        tar_path.unlink(missing_ok=True)


def build_archive(
    source_dir: Path,
    output_dir: Path,
    platform: str,
    arch: str,
    version: str,
    llvm_commit: str | None = None,
) -> Path:
    filename = f"clang-extra-{version}-{platform}-{arch}.tar.zst"
    with tempfile.TemporaryDirectory(prefix="clang-extra-") as temporary:
        staging = Path(temporary) / "staging"
        provenance = stage_clang_extra(source_dir, staging, platform, arch, version, llvm_commit)
        output = output_dir / platform / arch / filename
        create_archive(staging, output)
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        (output.parent / f"{filename}.sha256").write_text(f"{digest}  {filename}\n", encoding="utf-8")
        (output.parent / f"{filename}.provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
        update_manifests(output_dir, platform, arch, version, filename, digest)
    return output


def update_manifests(output_dir: Path, platform: str, arch: str, version: str, filename: str, digest: str) -> None:
    """Update the component and target manifests without losing old versions."""
    target_dir = output_dir / platform / arch
    target_manifest = target_dir / "manifest.json"
    data = json.loads(target_manifest.read_text(encoding="utf-8")) if target_manifest.exists() else {}
    versions = data.setdefault("versions", {})
    for key in list(data):
        if key not in {"latest", "versions"} and isinstance(data[key], dict) and "href" in data[key]:
            versions.setdefault(key, data.pop(key))
    versions[version] = {
        "href": f"https://media.githubusercontent.com/media/zackees/clang-tool-chain-bins/main/assets/clang-extra/{platform}/{arch}/{filename}",
        "sha256": digest,
    }
    data["latest"] = max(versions, key=lambda item: tuple(int(part) for part in item.split(".") if part.isdigit()))
    target_manifest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    root_manifest = output_dir / "manifest.json"
    if root_manifest.exists():
        root = json.loads(root_manifest.read_text(encoding="utf-8"))
        platform_entry = next((item for item in root.get("platforms", []) if item.get("platform") == platform), None)
        if platform_entry is None:
            platform_entry = {"platform": platform, "architectures": []}
            root.setdefault("platforms", []).append(platform_entry)
        if not any(item.get("arch") == arch for item in platform_entry["architectures"]):
            platform_entry["architectures"].append({"arch": arch, "manifest_path": f"{platform}/{arch}/manifest.json"})
        root_manifest.write_text(json.dumps(root, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True, help="Extracted official LLVM or Forge staging directory")
    parser.add_argument("--output-dir", type=Path, default=Path("assets/clang-extra"))
    parser.add_argument("--platform", choices=sorted({target[0] for target in SUPPORTED_TARGETS}), required=True)
    parser.add_argument("--arch", choices=sorted({target[1] for target in SUPPORTED_TARGETS}), required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--llvm-commit", help="Resolved llvm-project commit for provenance auditing")
    args = parser.parse_args(argv)
    build_archive(args.source_dir, args.output_dir, args.platform, args.arch, args.version, args.llvm_commit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
