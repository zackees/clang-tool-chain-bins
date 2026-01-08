#!/usr/bin/env python3
"""
Extract MinGW sysroot from LLVM-MinGW release.

This script downloads LLVM-MinGW and extracts only the sysroot directory
(x86_64-w64-mingw32/) which contains headers and libraries for GNU ABI support.
"""

import argparse
import hashlib
import json
import shutil
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

# LLVM-MinGW version and download URLs
LLVM_MINGW_VERSION = "20251104"  # Release date format (latest as of Nov 2024)
LLVM_VERSION = "21.1.5"

LLVM_MINGW_URLS = {
    "x86_64": f"https://github.com/mstorsjo/llvm-mingw/releases/download/"
    f"{LLVM_MINGW_VERSION}/llvm-mingw-{LLVM_MINGW_VERSION}-ucrt-x86_64.zip",
    "arm64": f"https://github.com/mstorsjo/llvm-mingw/releases/download/"
    f"{LLVM_MINGW_VERSION}/llvm-mingw-{LLVM_MINGW_VERSION}-ucrt-aarch64.zip",
}

# MinGW-w64 GCC runtime DLLs source (for libgcc_s_seh-1.dll and libstdc++-6.dll)
# These are needed for GNU ABI compatibility but not included in LLVM-MinGW
MINGW_GCC_VERSION = "14.2.0"
MINGW_GCC_URLS = {
    "x86_64": f"https://github.com/niXman/mingw-builds-binaries/releases/download/{MINGW_GCC_VERSION}-rt_v12-rev0/x86_64-{MINGW_GCC_VERSION}-release-posix-seh-msvcrt-rt_v12-rev0.7z",
    # ARM64 MinGW-w64 GCC binaries (if needed in future)
    "arm64": None,  # Not available yet
}

# Expected SHA256 checksums (to be updated after first download)
CHECKSUMS = {
    "x86_64": "TBD",  # Update after first download
    "arm64": "TBD",  # Update after first download
}


def download_llvm_mingw(arch: str, output_dir: Path) -> Path:
    """Download LLVM-MinGW release."""
    url = LLVM_MINGW_URLS.get(arch)
    if not url:
        raise ValueError(f"Unsupported architecture: {arch}")

    output_dir.mkdir(parents=True, exist_ok=True)
    filename = Path(url).name
    output_path = output_dir / filename

    if output_path.exists():
        print(f"Already downloaded: {output_path}")
        return output_path

    print(f"Downloading: {url}")
    print(f"To: {output_path}")

    try:
        urllib.request.urlretrieve(url, output_path)
        print(f"Downloaded: {output_path.stat().st_size / (1024*1024):.2f} MB")
    except Exception as e:
        print(f"Error downloading: {e}")
        if output_path.exists():
            output_path.unlink()
        raise

    return output_path


def download_mingw_gcc_dlls(arch: str, output_dir: Path) -> Path:
    """Download MinGW-w64 GCC runtime DLLs (libgcc and libstdc++)."""
    url = MINGW_GCC_URLS.get(arch)
    if not url:
        print(f"Warning: No MinGW-w64 GCC DLLs available for {arch}, skipping...")
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    filename = Path(url).name
    output_path = output_dir / filename

    if output_path.exists():
        print(f"Already downloaded: {output_path}")
        return output_path

    print(f"Downloading MinGW-w64 GCC runtime: {url}")
    print(f"To: {output_path}")

    try:
        urllib.request.urlretrieve(url, output_path)
        print(f"Downloaded: {output_path.stat().st_size / (1024*1024):.2f} MB")
    except Exception as e:
        print(f"Error downloading: {e}")
        if output_path.exists():
            output_path.unlink()
        raise

    return output_path


def extract_gcc_dlls(archive_path: Path, extract_dir: Path, arch: str) -> list[Path]:
    """Extract libgcc_s_seh-1.dll and libstdc++-6.dll from MinGW-w64 GCC."""
    if not archive_path or not archive_path.exists():
        return []

    print(f"\nExtracting GCC runtime DLLs from: {archive_path}")

    # Extract to temp directory
    temp_extract = extract_dir / "mingw_gcc_temp"
    temp_extract.mkdir(parents=True, exist_ok=True)

    print("Extracting archive...")
    try:
        # Use 7z to extract .7z archive
        import subprocess
        result = subprocess.run(
            ["7z", "x", "-y", f"-o{temp_extract}", str(archive_path)],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode != 0:
            raise RuntimeError(f"7z extraction failed: {result.stderr}")
    except FileNotFoundError:
        print("Error: 7z command not found. Please install 7-Zip.")
        raise

    # Find the mingw64 directory
    mingw_root = None
    for item in temp_extract.iterdir():
        if item.is_dir() and "mingw" in item.name.lower():
            mingw_root = item
            break

    if not mingw_root:
        raise RuntimeError(f"Could not find MinGW root directory in {temp_extract}")

    print(f"Found MinGW-w64 root: {mingw_root}")

    # Find and copy DLLs to a safe location before cleanup
    dll_names = ["libgcc_s_seh-1.dll", "libstdc++-6.dll"]
    found_dlls = []
    dll_temp_dir = extract_dir / "gcc_dlls"
    dll_temp_dir.mkdir(parents=True, exist_ok=True)

    for dll_name in dll_names:
        # Look in bin directory first
        dll_path = mingw_root / "bin" / dll_name
        if dll_path.exists():
            # Copy to safe location before cleanup
            dll_safe_copy = dll_temp_dir / dll_name
            shutil.copy2(dll_path, dll_safe_copy)
            found_dlls.append(dll_safe_copy)
            print(f"Found {dll_name}: {dll_path.stat().st_size / 1024:.1f} KB")
        else:
            print(f"Warning: {dll_name} not found in {mingw_root / 'bin'}")

    # Clean up temp directory (with retry for Windows permission issues)
    print("Cleaning up temporary files...")
    max_retries = 3
    for attempt in range(max_retries):
        try:
            shutil.rmtree(temp_extract)
            break
        except (PermissionError, OSError) as e:
            if attempt < max_retries - 1:
                print(f"Warning: Cleanup failed (attempt {attempt+1}/{max_retries}): {e}")
                import time
                time.sleep(1)
            else:
                print(f"Warning: Could not clean up {temp_extract}: {e}")
                print("This is not critical - the DLLs were copied successfully.")

    return found_dlls


def extract_sysroot(archive_path: Path, extract_dir: Path, arch: str, gcc_dlls: list[Path] = None) -> Path:
    """Extract only the sysroot directory from LLVM-MinGW."""
    print(f"\nExtracting sysroot from: {archive_path}")

    # Determine target triple based on architecture
    if arch == "x86_64":
        sysroot_name = "x86_64-w64-mingw32"
    elif arch == "arm64":
        sysroot_name = "aarch64-w64-mingw32"
    else:
        raise ValueError(f"Unknown architecture: {arch}")

    # Extract entire archive first (LLVM-MinGW structure)
    temp_extract = extract_dir / "temp"
    temp_extract.mkdir(parents=True, exist_ok=True)

    print("Extracting archive...")
    try:
        # Check if it's a ZIP or tar.xz archive
        if archive_path.suffix == ".zip":
            with zipfile.ZipFile(archive_path, "r") as zf:
                zf.extractall(path=temp_extract)
        else:
            with tarfile.open(archive_path, "r:xz") as tar:
                tar.extractall(path=temp_extract)
    except Exception as e:
        print(f"Error extracting archive: {e}")
        raise

    # Find the llvm-mingw root directory
    llvm_mingw_root = None
    for item in temp_extract.iterdir():
        if item.is_dir() and item.name.startswith("llvm-mingw"):
            llvm_mingw_root = item
            break

    if not llvm_mingw_root:
        raise RuntimeError(f"Could not find llvm-mingw root directory in {temp_extract}")

    print(f"Found LLVM-MinGW root: {llvm_mingw_root}")

    # Copy sysroot directory
    sysroot_src = llvm_mingw_root / sysroot_name
    if not sysroot_src.exists():
        raise RuntimeError(f"Sysroot not found: {sysroot_src}")

    sysroot_dst = extract_dir / sysroot_name
    print(f"Copying sysroot: {sysroot_src} -> {sysroot_dst}")

    if sysroot_dst.exists():
        shutil.rmtree(sysroot_dst)

    shutil.copytree(sysroot_src, sysroot_dst, symlinks=True)

    # Copy GCC runtime DLLs to sysroot bin directory (for GNU ABI support)
    if gcc_dlls:
        sysroot_bin = sysroot_dst / "bin"
        sysroot_bin.mkdir(parents=True, exist_ok=True)

        print(f"\nCopying GCC runtime DLLs to sysroot:")
        for dll_path in gcc_dlls:
            dll_dst = sysroot_bin / dll_path.name
            print(f"  {dll_path.name} -> {dll_dst}")
            shutil.copy2(dll_path, dll_dst)
        print(f"✓ Copied {len(gcc_dlls)} GCC runtime DLLs")

    # Copy top-level include directory (contains C/C++ headers)
    include_src = llvm_mingw_root / "include"
    if include_src.exists():
        include_dst = extract_dir / "include"
        print(f"Copying headers: {include_src} -> {include_dst}")
        if include_dst.exists():
            shutil.rmtree(include_dst)
        shutil.copytree(include_src, include_dst, symlinks=True)

    # Also copy generic headers if they exist
    generic_headers = llvm_mingw_root / "generic-w64-mingw32"
    if generic_headers.exists():
        generic_dst = extract_dir / "generic-w64-mingw32"
        print(f"Copying generic headers: {generic_headers} -> {generic_dst}")
        if generic_dst.exists():
            shutil.rmtree(generic_dst)
        shutil.copytree(generic_headers, generic_dst, symlinks=True)

    # Copy clang resource headers (mm_malloc.h, intrinsics, etc.)
    # These are compiler builtin headers needed for compilation
    clang_resource_src = llvm_mingw_root / "lib" / "clang"
    if clang_resource_src.exists():
        # Find the version directory (e.g., "21")
        version_dirs = [d for d in clang_resource_src.iterdir() if d.is_dir()]
        if version_dirs:
            clang_version_dir = version_dirs[0]  # Should only be one
            resource_include_src = clang_version_dir / "include"
            if resource_include_src.exists():
                # Copy to lib/clang/<version>/include in sysroot
                resource_dst = extract_dir / "lib" / "clang" / clang_version_dir.name / "include"
                print(f"Copying clang resource headers: {resource_include_src} -> {resource_dst}")
                resource_dst.parent.mkdir(parents=True, exist_ok=True)
                if resource_dst.exists():
                    shutil.rmtree(resource_dst)
                shutil.copytree(resource_include_src, resource_dst, symlinks=True)
                print(f"Copied {len(list(resource_dst.glob('*.h')))} resource headers")

            # Copy compiler-rt runtime libraries (libclang_rt.builtins.a, etc.)
            # These are needed for linking to provide runtime support functions
            resource_lib_src = clang_version_dir / "lib"
            if resource_lib_src.exists():
                resource_lib_dst = extract_dir / "lib" / "clang" / clang_version_dir.name / "lib"
                print(f"Copying compiler-rt libraries: {resource_lib_src} -> {resource_lib_dst}")
                resource_lib_dst.parent.mkdir(parents=True, exist_ok=True)
                if resource_lib_dst.exists():
                    shutil.rmtree(resource_lib_dst)
                shutil.copytree(resource_lib_src, resource_lib_dst, symlinks=True)

                # Count library files
                lib_count = len(list(resource_lib_dst.glob("**/*.a")))
                print(f"Copied {lib_count} compiler-rt library files")

    # Clean up temp directory
    print("Cleaning up temporary files...")
    shutil.rmtree(temp_extract)

    print(f"\n✓ Sysroot extracted to: {sysroot_dst}")
    return sysroot_dst


def create_archive(sysroot_dir: Path, output_dir: Path, arch: str) -> Path:
    """Create compressed archive of sysroot."""
    try:
        import pyzstd
    except ImportError:
        print("Error: pyzstd module not installed")
        print("Install with: pip install pyzstd")
        sys.exit(1)

    archive_name = f"mingw-sysroot-{LLVM_VERSION}-win-{arch}.tar.zst"
    archive_path = output_dir / archive_name

    print(f"\nCreating archive: {archive_path}")

    # Create tar archive in memory, then compress
    import io

    tar_buffer = io.BytesIO()

    with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
        # Determine what to archive
        sysroot_name = "x86_64-w64-mingw32" if arch == "x86_64" else "aarch64-w64-mingw32"

        sysroot_path = sysroot_dir.parent / sysroot_name
        include_path = sysroot_dir.parent / "include"
        generic_path = sysroot_dir.parent / "generic-w64-mingw32"
        lib_clang_path = sysroot_dir.parent / "lib" / "clang"

        if sysroot_path.exists():
            print(f"Adding to archive: {sysroot_name}/")
            tar.add(sysroot_path, arcname=sysroot_name)

        if include_path.exists():
            print("Adding to archive: include/")
            tar.add(include_path, arcname="include")

        if generic_path.exists():
            print("Adding to archive: generic-w64-mingw32/")
            tar.add(generic_path, arcname="generic-w64-mingw32")

        if lib_clang_path.exists():
            print("Adding to archive: lib/clang/ (resource headers)")
            tar.add(lib_clang_path, arcname="lib/clang")

    tar_data = tar_buffer.getvalue()
    tar_size = len(tar_data)
    print(f"Tar size: {tar_size / (1024*1024):.2f} MB")

    # Compress with zstd level 22
    print("Compressing with zstd level 22...")
    compressed_data = pyzstd.compress(tar_data, level_or_option=22)

    with open(archive_path, "wb") as f:
        f.write(compressed_data)

    compressed_size = archive_path.stat().st_size
    ratio = (1 - compressed_size / tar_size) * 100

    print(f"Compressed size: {compressed_size / (1024*1024):.2f} MB")
    print(f"Compression ratio: {ratio:.1f}%")

    return archive_path


def generate_checksums(archive_path: Path) -> dict[str, str]:
    """Generate SHA256 and MD5 checksums."""
    print("\nGenerating checksums...")

    sha256_hash = hashlib.sha256()
    md5_hash = hashlib.md5()

    with open(archive_path, "rb") as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            sha256_hash.update(chunk)
            md5_hash.update(chunk)

    checksums = {
        "sha256": sha256_hash.hexdigest(),
        "md5": md5_hash.hexdigest(),
    }

    # Write checksum files
    sha256_file = Path(str(archive_path) + ".sha256")
    md5_file = Path(str(archive_path) + ".md5")

    with open(sha256_file, "w") as f:
        f.write(f"{checksums['sha256']}  {archive_path.name}\n")

    with open(md5_file, "w") as f:
        f.write(f"{checksums['md5']}  {archive_path.name}\n")

    print(f"SHA256: {checksums['sha256']}")
    print(f"MD5: {checksums['md5']}")

    return checksums


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Extract MinGW sysroot from LLVM-MinGW release")
    parser.add_argument("--arch", required=True, choices=["x86_64", "arm64"], help="Target architecture")
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path("work"),
        help="Working directory for downloads and extraction",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).parent.parent / "assets" / "mingw" / "win",
        help="Output directory for final archives (default: ../assets/mingw/win)",
    )

    args = parser.parse_args()

    work_dir = args.work_dir / args.arch
    output_dir = args.output_dir / args.arch
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("MINGW SYSROOT EXTRACTION")
    print("=" * 70)
    print(f"Architecture: {args.arch}")
    print(f"Work directory: {work_dir}")
    print(f"Output directory: {output_dir}")
    print()

    # Step 1: Download LLVM-MinGW
    archive_path = download_llvm_mingw(args.arch, work_dir)

    # Step 1b: Download MinGW-w64 GCC runtime DLLs
    gcc_archive_path = download_mingw_gcc_dlls(args.arch, work_dir)

    # Step 2: Extract GCC runtime DLLs
    gcc_dlls = []
    if gcc_archive_path:
        gcc_dlls = extract_gcc_dlls(gcc_archive_path, work_dir, args.arch)

    # Step 3: Extract sysroot and include GCC DLLs
    sysroot_dir = extract_sysroot(archive_path, work_dir / "extracted", args.arch, gcc_dlls)

    # Step 4: Create compressed archive
    final_archive = create_archive(sysroot_dir, output_dir, args.arch)

    # Step 5: Generate checksums
    checksums = generate_checksums(final_archive)

    # Step 6: Update manifest
    manifest_path = output_dir / "manifest.json"
    manifest_data = {
        "latest": LLVM_VERSION,
        "versions": {
            LLVM_VERSION: {
                "version": LLVM_VERSION,
                "href": f"./mingw-sysroot-{LLVM_VERSION}-win-{args.arch}.tar.zst",
                "sha256": checksums["sha256"],
            }
        },
    }

    with open(manifest_path, "w") as f:
        json.dump(manifest_data, f, indent=2)

    print(f"\n✓ Manifest written to: {manifest_path}")
    print("\n" + "=" * 70)
    print("COMPLETE")
    print("=" * 70)
    print(f"Archive: {final_archive}")
    print(f"Size: {final_archive.stat().st_size / (1024*1024):.2f} MB")
    print(f"SHA256: {checksums['sha256']}")


if __name__ == "__main__":
    main()
