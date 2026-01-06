#!/usr/bin/env python3
"""
Create LLDB archives for all platforms.

This script:
1. Extracts LLDB binaries from official LLVM releases or existing extracted directories
2. Filters to essential LLDB components (lldb, lldb-server, lldb-argdumper)
3. Creates tar archives with proper permissions
4. Compresses with zstd level 22
5. Generates SHA256 checksums
6. Outputs archives to downloads-bins/assets/lldb/{platform}/{arch}/

LLDB binaries to package:
- lldb              # Main debugger (essential)
- lldb-server       # Remote debugging server (essential for remote debugging)
- lldb-argdumper    # Argument processing helper (essential for CLI)

Unlike the Clang toolchain, LLDB has minimal binaries, so no deduplication is needed.
"""

import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

# LLDB binaries to extract and package
LLDB_BINARIES = {
    "lldb",  # Main debugger
    "lldb-server",  # Remote debugging server
    "lldb-argdumper",  # Argument processing helper
}

# Additional LLDB support files (DLLs, shared libraries)
LLDB_SUPPORT_FILES = {
    "liblldb.dll",  # Windows: LLDB shared library
    "liblldb.so",   # Linux: LLDB shared library
    "liblldb.dylib",  # macOS: LLDB shared library
}

# LLVM versions for each platform (from CLAUDE.md)
LLVM_VERSIONS = {
    "win": "21.1.5",
    "linux": "21.1.5",
    "darwin": "21.1.6",  # macOS uses newer version
}

# Official LLVM download URLs
LLVM_DOWNLOAD_URLS = {
    ("win", "x86_64"): "https://github.com/llvm/llvm-project/releases/download/llvmorg-{version}/LLVM-{version}-win64.exe",
    ("win", "arm64"): "https://github.com/llvm/llvm-project/releases/download/llvmorg-{version}/LLVM-{version}-woa64.exe",
    ("linux", "x86_64"): "https://github.com/llvm/llvm-project/releases/download/llvmorg-{version}/LLVM-{version}-Linux-X64.tar.xz",
    ("linux", "arm64"): "https://github.com/llvm/llvm-project/releases/download/llvmorg-{version}/clang+llvm-{version}-aarch64-linux-gnu.tar.xz",
    ("darwin", "x86_64"): "https://github.com/llvm/llvm-project/releases/download/llvmorg-{version}/LLVM-{version}-macOS-X64.tar.xz",
    ("darwin", "arm64"): "https://github.com/llvm/llvm-project/releases/download/llvmorg-{version}/LLVM-{version}-macOS-ARM64.tar.xz",
}


def download_llvm_if_needed(platform: str, arch: str, version: str, work_dir: Path) -> Path:
    """
    Download LLVM release if not already present.

    Args:
        platform: Platform name (win, linux, darwin)
        arch: Architecture (x86_64, arm64)
        version: LLVM version (e.g., "21.1.5")
        work_dir: Working directory for downloads

    Returns:
        Path to downloaded archive
    """
    key = (platform, arch)
    if key not in LLVM_DOWNLOAD_URLS:
        raise ValueError(f"Unsupported platform/arch combination: {platform}/{arch}")

    url = LLVM_DOWNLOAD_URLS[key].format(version=version)
    filename = Path(url).name
    download_path = work_dir / filename

    if download_path.exists():
        print(f"✓ LLVM archive already exists: {download_path}")
        return download_path

    print(f"Downloading LLVM {version} for {platform}/{arch}...")
    print(f"URL: {url}")
    print(f"Destination: {download_path}")
    print()

    work_dir.mkdir(parents=True, exist_ok=True)

    def show_progress(block_num: int, block_size: int, total_size: int) -> None:
        if total_size > 0:
            downloaded = block_num * block_size
            percent = min(100, (downloaded / total_size) * 100)
            mb_downloaded = downloaded / (1024 * 1024)
            mb_total = total_size / (1024 * 1024)
            print(f"\rProgress: {percent:5.1f}% ({mb_downloaded:6.1f} MB / {mb_total:6.1f} MB)", end="", flush=True)

    try:
        urllib.request.urlretrieve(url, download_path, reporthook=show_progress)
        print()  # New line after progress
        print(f"✓ Downloaded: {download_path.stat().st_size / (1024*1024):.2f} MB")
        return download_path
    except Exception as e:
        if download_path.exists():
            download_path.unlink()
        raise RuntimeError(f"Failed to download LLVM: {e}") from e


def extract_llvm_archive(archive_path: Path, extract_dir: Path, platform: str) -> Path:
    """
    Extract LLVM archive.

    Args:
        archive_path: Path to LLVM archive
        extract_dir: Directory to extract to
        platform: Platform name (win, linux, darwin)

    Returns:
        Path to extracted LLVM root directory
    """
    print(f"\nExtracting LLVM archive: {archive_path}")
    extract_dir.mkdir(parents=True, exist_ok=True)

    if archive_path.suffix == ".exe":
        # Windows installer - need 7z
        print("Windows .exe installer detected, using 7z...")
        try:
            subprocess.run(
                ["7z", "x", str(archive_path), f"-o{extract_dir}", "-y"],
                check=True,
                capture_output=True,
                text=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            raise RuntimeError(
                "7z is required to extract Windows .exe installer.\n"
                "Install 7z: https://www.7-zip.org/"
            ) from e

    elif archive_path.suffix == ".xz" or archive_path.name.endswith(".tar.xz"):
        print("Extracting tar.xz archive...")
        # Try external tar command first (faster)
        tar_available = shutil.which("tar") is not None

        if tar_available:
            print("Using system tar command...")
            subprocess.run(
                ["tar", "-xJf", str(archive_path), "-C", str(extract_dir)],
                check=True,
                capture_output=True,
                text=True,
            )
        else:
            # Fallback to Python extraction
            print("Using Python extraction (slower)...")
            import lzma

            with lzma.open(archive_path) as xz_file, tarfile.open(fileobj=xz_file) as tar:
                tar.extractall(path=extract_dir)

    else:
        raise ValueError(f"Unsupported archive format: {archive_path.suffix}")

    print(f"✓ Extracted to: {extract_dir}")

    # Find the LLVM root directory
    llvm_root = None
    for item in extract_dir.iterdir():
        if item.is_dir():
            # Check if it has a bin/ directory
            if (item / "bin").exists():
                llvm_root = item
                break

    if not llvm_root:
        raise RuntimeError(f"Could not find LLVM root with bin/ directory in {extract_dir}")

    print(f"✓ Found LLVM root: {llvm_root}")
    return llvm_root


def extract_lldb_binaries(llvm_root: Path, output_dir: Path, platform: str) -> int:
    """
    Extract LLDB binaries from LLVM installation.

    Args:
        llvm_root: Root directory of extracted LLVM
        output_dir: Output directory for LLDB binaries
        platform: Platform name (win, linux, darwin)

    Returns:
        Number of binaries extracted
    """
    print(f"\nExtracting LLDB binaries from: {llvm_root}")

    bin_dir = llvm_root / "bin"
    if not bin_dir.exists():
        raise RuntimeError(f"bin/ directory not found in {llvm_root}")

    output_bin = output_dir / "bin"
    output_bin.mkdir(parents=True, exist_ok=True)

    # Determine binary extension
    ext = ".exe" if platform == "win" else ""

    extracted_count = 0
    print(f"\nLooking for LLDB binaries in: {bin_dir}")
    print("Expected binaries:")

    for binary_name in sorted(LLDB_BINARIES):
        binary_file = bin_dir / f"{binary_name}{ext}"

        if binary_file.exists():
            dest = output_bin / binary_file.name
            shutil.copy2(binary_file, dest)
            size_mb = dest.stat().st_size / (1024 * 1024)
            print(f"  ✓ {binary_file.name:20s} ({size_mb:6.1f} MB)")
            extracted_count += 1
        else:
            print(f"  ✗ {binary_name}{ext:4s} (not found - may be optional)")

    # Also copy support files (DLLs, shared libraries)
    print("\nLooking for LLDB support files:")
    for support_file in sorted(LLDB_SUPPORT_FILES):
        support_path = bin_dir / support_file

        if support_path.exists():
            dest = output_bin / support_path.name
            shutil.copy2(support_path, dest)
            size_mb = dest.stat().st_size / (1024 * 1024)
            print(f"  ✓ {support_path.name:20s} ({size_mb:6.1f} MB)")
            extracted_count += 1
        else:
            # Support files are optional (platform-specific)
            print(f"  - {support_file:20s} (not found - platform-specific)")

    if extracted_count == 0:
        raise RuntimeError(f"No LLDB binaries found in {bin_dir}")

    print(f"\n✓ Extracted {extracted_count} LLDB files (binaries + support)")
    return extracted_count


def create_tar_archive(source_dir: Path, output_tar: Path) -> Path:
    """
    Create tar archive with correct permissions for LLDB.

    Args:
        source_dir: Directory containing bin/ (e.g., lldb_extracted/)
        output_tar: Output tar file path

    Returns:
        Path to created tar file
    """
    print("\n" + "=" * 70)
    print("CREATING TAR ARCHIVE")
    print("=" * 70)
    print(f"Source: {source_dir}")
    print(f"Output: {output_tar}")
    print()

    def tar_filter(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo:
        """Filter to set correct permissions for LLDB files."""
        if tarinfo.isfile():
            # All binaries in bin/ should be executable
            if "/bin/" in tarinfo.name or tarinfo.name.startswith("bin/"):
                tarinfo.mode = 0o755  # rwxr-xr-x
                print(f"  Setting executable: {tarinfo.name}")
            else:
                # Other files default to readable
                tarinfo.mode = 0o644  # rw-r--r--
        return tarinfo

    print("Creating tar archive...")
    print("Setting permissions...")

    with tarfile.open(output_tar, "w") as tar:
        # Add bin/ directory
        bin_dir = source_dir / "bin"
        if bin_dir.exists():
            tar.add(bin_dir, arcname="bin", filter=tar_filter)

        # Add any other top-level files (LICENSE, README, etc.)
        for item in source_dir.iterdir():
            if item.is_file():
                tar.add(item, arcname=item.name, filter=tar_filter)

    size = output_tar.stat().st_size
    print(f"✓ Created: {output_tar} ({size / (1024*1024):.2f} MB)")

    return output_tar


def verify_tar_permissions(tar_file: Path) -> int:
    """Verify that files in the tar archive have correct permissions."""
    print("\n" + "=" * 70)
    print("VERIFYING TAR PERMISSIONS")
    print("=" * 70)
    print(f"Checking permissions in: {tar_file}")
    print()

    issues_found = []
    executables_checked = 0

    with tarfile.open(tar_file, "r") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue

            # Check files in bin/ directory
            if "/bin/" in member.name or member.name.startswith("bin/"):
                executables_checked += 1
                # Check if executable bit is set
                if not (member.mode & 0o100):
                    issues_found.append((member.name, oct(member.mode), "executable missing +x"))
                    print(f"  ✗ Missing executable permission: {member.name} (mode: {oct(member.mode)})")
                else:
                    print(f"  ✓ {member.name} (mode: {oct(member.mode)})")

    print()
    print(f"Total executables checked: {executables_checked}")

    if issues_found:
        print(f"\n⚠️  WARNING: Found {len(issues_found)} files with incorrect permissions!")
        print("\nFiles with issues:")
        for name, mode, issue in issues_found:
            print(f"  - {name} (mode: {mode}) - {issue}")
        raise RuntimeError(f"Tar archive has {len(issues_found)} files with incorrect permissions")
    else:
        print("✅ All files have correct permissions")

    return executables_checked


def compress_with_zstd(tar_file: Path, output_zst: Path, level: int = 22) -> Path:
    """Compress tar with zstd."""
    import zstandard as zstd

    print("\n" + "=" * 70)
    print(f"COMPRESSING WITH ZSTD LEVEL {level}")
    print("=" * 70)
    print(f"Input:  {tar_file} ({tar_file.stat().st_size / (1024*1024):.2f} MB)")
    print(f"Output: {output_zst}")
    print()

    print("Compressing (this may take a while)...")

    import time

    start = time.time()

    # Create compressor with multi-threading
    cctx = zstd.ZstdCompressor(level=level, threads=-1)

    # Stream compress
    with open(tar_file, "rb") as ifh, open(output_zst, "wb") as ofh:
        # Read in chunks to allow interruption
        chunk_size = 1024 * 1024  # 1MB chunks
        reader = cctx.stream_reader(ifh, size=tar_file.stat().st_size)

        while True:
            chunk = reader.read(chunk_size)
            if not chunk:
                break
            ofh.write(chunk)

    elapsed = time.time() - start

    original_size = tar_file.stat().st_size
    compressed_size = output_zst.stat().st_size
    ratio = original_size / compressed_size if compressed_size > 0 else 0

    print(f"✓ Compressed in {elapsed:.1f}s")
    print(f"  Original:   {original_size / (1024*1024):.2f} MB")
    print(f"  Compressed: {compressed_size / (1024*1024):.2f} MB")
    print(f"  Ratio:      {ratio:.2f}:1")
    print(f"  Reduction:  {(1 - compressed_size/original_size) * 100:.1f}%")

    return output_zst


def generate_checksum(file_path: Path) -> str:
    """Generate SHA256 checksum for a file."""
    sha256_hash = hashlib.sha256()

    with open(file_path, "rb") as f:
        # Read in chunks to handle large files
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)

    return sha256_hash.hexdigest()


def process_platform_arch(
    lldb_root: Path, platform: str, arch: str, version: str, source_dir: Path | None = None
) -> dict[str, str | int] | None:
    """
    Process a single platform/arch combination.

    Args:
        lldb_root: Root downloads-bins/assets/lldb directory
        platform: Platform name (win, linux, darwin)
        arch: Architecture (x86_64, arm64)
        version: LLDB version (e.g., "21.1.5")
        source_dir: Optional existing LLVM extraction directory

    Returns:
        Dict with archive info, or None if skipped
    """
    output_dir = lldb_root / platform / arch

    print("\n" + "=" * 70)
    print(f"PROCESSING: {platform}/{arch} (LLVM {version})")
    print("=" * 70)

    # Working directory for this platform/arch
    work_dir = lldb_root.parent.parent / "work" / "lldb" / platform / arch
    work_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Get LLVM source
    if source_dir and source_dir.exists():
        print(f"\n✓ Using provided LLVM source: {source_dir}")
        llvm_root = source_dir
    else:
        # Download LLVM if needed
        archive_path = download_llvm_if_needed(platform, arch, version, work_dir)

        # Extract LLVM
        extract_dir = work_dir / "extracted"
        llvm_root = extract_llvm_archive(archive_path, extract_dir, platform)

    # Step 2: Extract LLDB binaries
    lldb_extracted = work_dir / "lldb_extracted"
    if lldb_extracted.exists():
        shutil.rmtree(lldb_extracted)
    lldb_extracted.mkdir(parents=True, exist_ok=True)

    binary_count = extract_lldb_binaries(llvm_root, lldb_extracted, platform)

    if binary_count == 0:
        print(f"⚠️  No LLDB binaries found for {platform}/{arch}")
        return None

    # Step 3: Create archive name
    archive_base = f"lldb-{version}-{platform}-{arch}"
    tar_file = work_dir / f"{archive_base}.tar"
    zst_file = output_dir / f"{archive_base}.tar.zst"

    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)

    # Step 4: Create TAR
    create_tar_archive(lldb_extracted, tar_file)

    # Step 5: Verify permissions
    verify_tar_permissions(tar_file)

    # Step 6: Compress with zstd
    compress_with_zstd(tar_file, zst_file)

    # Step 7: Generate checksum
    print("\nGenerating SHA256 checksum...")
    sha256 = generate_checksum(zst_file)
    print(f"SHA256: {sha256}")

    # Write checksum file
    checksum_file = zst_file.with_suffix(".tar.zst.sha256")
    with open(checksum_file, "w") as f:
        f.write(f"{sha256}  {zst_file.name}\n")

    # Clean up uncompressed tar
    print(f"\nRemoving uncompressed tar: {tar_file}")
    tar_file.unlink()

    print("\n✅ SUCCESS!")
    print(f"Archive: {zst_file}")
    print(f"Size: {zst_file.stat().st_size / (1024*1024):.2f} MB")
    print(f"SHA256: {sha256}")

    return {
        "filename": zst_file.name,
        "path": str(zst_file.relative_to(lldb_root)),
        "sha256": sha256,
        "size": zst_file.stat().st_size,
    }


def main() -> None:
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Create LLDB archives for all platforms")
    parser.add_argument(
        "--lldb-root",
        type=Path,
        default=Path("downloads-bins/assets/lldb"),
        help="Root LLDB directory (default: downloads-bins/assets/lldb)",
    )
    parser.add_argument(
        "--platform",
        help="Process only this platform (win, linux, darwin). If not specified, process all.",
    )
    parser.add_argument(
        "--arch",
        help="Process only this architecture (x86_64, arm64). If not specified, process all.",
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        help="Use existing LLVM extraction directory (skip download/extract)",
    )
    parser.add_argument("--zstd-level", type=int, default=22, help="Zstd compression level (default: 22)")

    args = parser.parse_args()

    lldb_root = args.lldb_root.resolve()

    if not lldb_root.exists():
        print(f"Error: LLDB root directory not found: {lldb_root}")
        sys.exit(1)

    # Define platforms and architectures to process
    platforms = [args.platform] if args.platform else ["win", "linux", "darwin"]
    architectures = [args.arch] if args.arch else ["x86_64", "arm64"]

    # Process each platform/arch combination
    results = {}
    for platform in platforms:
        # Get version for this platform
        version = LLVM_VERSIONS.get(platform)
        if not version:
            print(f"Warning: No LLVM version defined for platform {platform}, skipping")
            continue

        results[platform] = {}
        for arch in architectures:
            try:
                result = process_platform_arch(lldb_root, platform, arch, version, args.source_dir)
                if result:
                    results[platform][arch] = result
            except Exception as e:
                print(f"\n❌ Error processing {platform}/{arch}: {e}")
                import traceback

                traceback.print_exc()
                continue

    # Print summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    total_archives = sum(len(arches) for arches in results.values())
    print(f"\n✓ Created {total_archives} archives")

    if total_archives == 0:
        print("\n⚠️  No archives were created")
        sys.exit(1)

    for platform, arches in results.items():
        for arch, info in arches.items():
            print(f"\n{platform}/{arch}:")
            print(f"  File: {info['filename']}")
            print(f"  Size: {info['size'] / (1024*1024):.2f} MB")
            print(f"  SHA256: {info['sha256']}")

    # Save results to JSON for manifest updates
    results_file = lldb_root / "archive_results.json"
    with open(results_file, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n✓ Archive info saved to: {results_file}")
    print("\nNext steps:")
    print("1. Update manifests with these SHA256 hashes")
    print("2. Upload archives to GitHub (if not already in downloads-bins repo)")
    print("3. Test download and extraction")
    print("\n✅ Done!")


if __name__ == "__main__":
    main()
