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
import subprocess
import tarfile
import tempfile
import urllib.request
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
SCRIPT_SOURCES = {
    "git-clang-format": "clang/tools/clang-format/git-clang-format",
    "run-clang-tidy": "clang-tools-extra/clang-tidy/tool/run-clang-tidy.py",
}
LLVM_DOWNLOAD_URLS = {
    ("win", "x86_64"): "https://github.com/llvm/llvm-project/releases/download/llvmorg-{version}/LLVM-{version}-win64.exe",
    ("linux", "x86_64"): "https://github.com/llvm/llvm-project/releases/download/llvmorg-{version}/LLVM-{version}-Linux-X64.tar.xz",
    ("linux", "arm64"): "https://github.com/llvm/llvm-project/releases/download/llvmorg-{version}/LLVM-{version}-Linux-ARM64.tar.xz",
    ("darwin", "x86_64"): "https://github.com/llvm/llvm-project/releases/download/llvmorg-{version}/LLVM-{version}-macOS-X64.tar.xz",
    ("darwin", "arm64"): "https://github.com/llvm/llvm-project/releases/download/llvmorg-{version}/LLVM-{version}-macOS-ARM64.tar.xz",
}


def expected_binary_name(platform: str) -> str:
    return "clangd.exe" if platform == "win" else "clangd"


def download_llvm(platform: str, arch: str, version: str, work_dir: Path) -> Path:
    """Download the exact upstream distribution used for this target."""
    try:
        url = LLVM_DOWNLOAD_URLS[(platform, arch)].format(version=version)
    except KeyError as error:
        raise ValueError(f"no upstream LLVM distribution for {platform}/{arch}; use the Forge source fallback") from error
    work_dir.mkdir(parents=True, exist_ok=True)
    archive = work_dir / Path(url).name
    if not archive.exists():
        urllib.request.urlretrieve(url, archive)
    return archive


def extract_llvm(archive: Path, extract_dir: Path, platform: str) -> Path:
    """Extract an official release using 7-Zip on Windows or tar.xz elsewhere."""
    extract_dir.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".exe":
        try:
            subprocess.run(["7z", "x", str(archive), f"-o{extract_dir}", "-y"], check=True, capture_output=True, text=True)
        except FileNotFoundError as error:
            raise RuntimeError("7z is required to extract the Windows LLVM installer") from error
    elif archive.name.endswith(".tar.xz"):
        subprocess.run(["tar", "-xJf", str(archive), "-C", str(extract_dir)], check=True)
    else:
        raise ValueError(f"unsupported LLVM archive: {archive}")
    return _find_root(extract_dir)


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


def _copy_release_script(destination: Path, name: str, version: str, executable: bool) -> str:
    relative = SCRIPT_SOURCES[name]
    url = f"https://raw.githubusercontent.com/llvm/llvm-project/llvmorg-{version}/{relative}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as response:
        destination.write_bytes(response.read())
    if executable:
        destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return url


def stage_clang_extra(
    source_dir: Path,
    staging_dir: Path,
    platform: str,
    arch: str,
    version: str,
    llvm_commit: str | None = None,
    provenance_method: str = "extracted",
    build_options: dict | None = None,
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
    compiled_tools = ("clang-format", "clang-query", "clang-tidy", "clangd")
    missing = [
        name
        for name in compiled_tools
        if not (bin_dir / (name + (".exe" if platform == "win" else ""))).is_file()
    ]
    if missing:
        raise FileNotFoundError(f"LLVM distribution is missing clang-extra tools: {', '.join(missing)}")

    fetched_scripts: dict[str, str] = {}
    for name in compiled_tools:
        filename = name + (".exe" if platform == "win" else "")
        _copy_file(bin_dir / filename, destination_bin / filename, platform != "win")
    for name in ("git-clang-format", "run-clang-tidy"):
        # These are Python helper scripts, not PE executables. The historical
        # clang-extra contract exposes them without a platform suffix.
        filename = name
        source = bin_dir / filename
        if source.is_file():
            _copy_file(source, destination_bin / filename, platform != "win")
        else:
            fetched_scripts[name] = _copy_release_script(destination_bin / filename, name, version, platform != "win")

    # clangd loads the Clang resource directory and, on Windows/Unix LLVM
    # distributions, sibling runtime libraries from these locations.
    resources = root / "lib" / "clang"
    if not resources.is_dir():
        raise FileNotFoundError(f"LLVM resource directory is missing: {resources}")
    shutil.copytree(resources, staging_dir / "lib" / "clang")
    for pattern in (("*.dll",) if platform == "win" else ("*.so", "*.so.*", "*.dylib")):
        for library in sorted((root / "lib").glob(pattern)):
            # LLVM macOS distributions may carry their build-host libc++;
            # bundling it makes clangd load an ABI-incompatible dylib on a
            # newer runner. The system C++ runtime is the required runtime
            # for the official macOS clangd binaries; LLVM/Clang dylibs still
            # remain packaged.
            if platform == "darwin" and library.name.startswith(("libc++", "libc++abi")):
                continue
            _copy_file(library, staging_dir / "lib" / library.name, platform != "win")
    # Windows LLVM releases keep DLLs beside the executables.
    if platform == "win":
        for library in sorted(bin_dir.glob("*.dll")):
            _copy_file(library, destination_bin / library.name)

    return {
        "method": provenance_method,
        "llvm_version": version,
        "llvm_project_tag": f"llvmorg-{version}",
        "llvm_project_commit": llvm_commit,
        "llvm_source": "official llvm-project distribution",
        "target": {"platform": platform, "arch": arch},
        "tools": list(EXTRA_TOOLS),
        "build_options": build_options or {"mode": "prebuilt-extraction"},
        "fetched_scripts": fetched_scripts,
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
    provenance_method: str = "extracted",
    build_options: dict | None = None,
) -> Path:
    filename = f"clang-extra-{version}-{platform}-{arch}.tar.zst"
    with tempfile.TemporaryDirectory(prefix="clang-extra-") as temporary:
        staging = Path(temporary) / "staging"
        provenance = stage_clang_extra(
            source_dir, staging, platform, arch, version, llvm_commit, provenance_method, build_options
        )
        output = output_dir / platform / arch / filename
        create_archive(staging, output)
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        (output.parent / f"{filename}.sha256").write_text(f"{digest}  {filename}\n", encoding="utf-8")
        (output.parent / f"{filename}.provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
        update_manifests(output_dir, platform, arch, version, filename, digest)
    return output


def build_downloaded_archive(
    output_dir: Path,
    platform: str,
    arch: str,
    version: str,
    work_dir: Path,
    llvm_commit: str | None = None,
    provenance_method: str = "extracted",
    build_options: dict | None = None,
) -> Path:
    """Download, extract, and package one native upstream distribution."""
    archive = download_llvm(platform, arch, version, work_dir)
    with tempfile.TemporaryDirectory(prefix="llvm-extract-") as extracted:
        source = extract_llvm(archive, Path(extracted), platform)
        return build_archive(source, output_dir, platform, arch, version, llvm_commit, provenance_method, build_options)


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
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument("--source-dir", type=Path, help="Extracted official LLVM or Forge staging directory")
    source_group.add_argument("--download", action="store_true", help="Download and extract the matching official LLVM distribution")
    parser.add_argument("--output-dir", type=Path, default=Path("assets/clang-extra"))
    parser.add_argument("--platform", choices=sorted({target[0] for target in SUPPORTED_TARGETS}), required=True)
    parser.add_argument("--arch", choices=sorted({target[1] for target in SUPPORTED_TARGETS}), required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--llvm-commit", help="Resolved llvm-project commit for provenance auditing")
    parser.add_argument("--provenance-method", choices=("extracted", "forge"), default="extracted")
    parser.add_argument("--build-options-json", help="JSON object recorded in provenance")
    parser.add_argument("--work-dir", type=Path, default=Path("tools/work/clang-extra"))
    args = parser.parse_args(argv)
    build_options = json.loads(args.build_options_json) if args.build_options_json else None
    if args.source_dir is not None:
        build_archive(
            args.source_dir,
            args.output_dir,
            args.platform,
            args.arch,
            args.version,
            args.llvm_commit,
            args.provenance_method,
            build_options,
        )
    else:
        build_downloaded_archive(
            args.output_dir,
            args.platform,
            args.arch,
            args.version,
            args.work_dir,
            args.llvm_commit,
            args.provenance_method,
            build_options,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
