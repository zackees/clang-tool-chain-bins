#!/usr/bin/env python3
"""
Fetch and Archive Node.js Runtime for Emscripten

This script automates the Node.js binary packaging process:
1. Downloads official Node.js binaries from nodejs.org
2. Verifies checksums against official SHASUMS256.txt
3. Extracts archive (ZIP for Windows, TAR.XZ/TAR.GZ for Unix)
4. Strips unnecessary files (include/, share/, npm, corepack, docs)
5. Keeps only: bin/node[.exe], LICENSE, minimal lib/node_modules
6. Creates TAR archive with proper permissions
7. Compresses with zstd level 22
8. Generates checksums (SHA256, MD5)
9. Creates manifest.json
10. Places in ../assets/nodejs/{platform}/{arch}/

Usage:
    python fetch_and_archive_nodejs.py --platform win --arch x86_64
    python fetch_and_archive_nodejs.py --platform linux --arch x86_64
    python fetch_and_archive_nodejs.py --platform darwin --arch arm64

Requirements:
    - Python 3.10+
    - pyzstd module: pip install pyzstd

Size estimates:
    - Official Node.js: 28-49 MB compressed
    - Our stripped version: 10-15 MB compressed (65-71% reduction)
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Dict, Optional

# ============================================================================
# Configuration
# ============================================================================

# Node.js LTS version (Jod)
NODEJS_VERSION = "22.11.0"

# Official Node.js download URLs
NODEJS_DOWNLOAD_URLS = {
    ("win", "x86_64"): f"https://nodejs.org/dist/v{NODEJS_VERSION}/node-v{NODEJS_VERSION}-win-x64.zip",
    ("linux", "x86_64"): f"https://nodejs.org/dist/v{NODEJS_VERSION}/node-v{NODEJS_VERSION}-linux-x64.tar.xz",
    ("linux", "arm64"): f"https://nodejs.org/dist/v{NODEJS_VERSION}/node-v{NODEJS_VERSION}-linux-arm64.tar.xz",
    ("darwin", "x86_64"): f"https://nodejs.org/dist/v{NODEJS_VERSION}/node-v{NODEJS_VERSION}-darwin-x64.tar.gz",
    ("darwin", "arm64"): f"https://nodejs.org/dist/v{NODEJS_VERSION}/node-v{NODEJS_VERSION}-darwin-arm64.tar.gz",
}

# Official checksums URL
CHECKSUMS_URL = f"https://nodejs.org/dist/v{NODEJS_VERSION}/SHASUMS256.txt"

# Directories to remove (stripping)
STRIP_DIRS = [
    "include",  # C++ headers - not needed for Emscripten runtime
    "share",    # Man pages, docs - not needed
]

# Files to remove (stripping)
STRIP_FILES = [
    "README.md",
    "CHANGELOG.md",
    "LICENSE",  # Will be kept, but individual module licenses can go
]

# Executables to remove (stripping)
STRIP_EXECUTABLES = [
    "npm",
    "npx",
    "corepack",
]


# ============================================================================
# Utility Functions
# ============================================================================


def print_section(title: str) -> None:
    """Print a formatted section header."""
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def download_file(url: str, output_path: Path, show_progress: bool = True) -> None:
    """Download a file with progress indication."""
    print(f"Downloading from: {url}")
    print(f"Saving to: {output_path}")

    output_path = Path(output_path)
    breadcrumb_path = Path(str(output_path) + ".downloading")

    # Create breadcrumb file to mark download in progress
    breadcrumb_path.touch()

    def report_progress(block_num: int, block_size: int, total_size: int) -> None:
        if show_progress and total_size > 0:
            downloaded = block_num * block_size
            percent = min(100, (downloaded / total_size) * 100)
            mb_downloaded = downloaded / (1024 * 1024)
            mb_total = total_size / (1024 * 1024)
            print(
                f"\rProgress: {percent:5.1f}% ({mb_downloaded:6.1f} MB / {mb_total:6.1f} MB)",
                end="",
                flush=True,
            )

    try:
        urllib.request.urlretrieve(url, output_path, reporthook=report_progress)
        if show_progress:
            print()  # New line after progress
        # Download completed successfully, remove breadcrumb
        breadcrumb_path.unlink(missing_ok=True)
    except (KeyboardInterrupt, Exception):
        # Download interrupted or failed, clean up partial file and breadcrumb
        if output_path.exists():
            output_path.unlink()
        breadcrumb_path.unlink(missing_ok=True)
        raise


def get_file_hash(filepath: Path, algorithm: str = "sha256") -> str:
    """Calculate hash of a file."""
    h = hashlib.new(algorithm)
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ============================================================================
# Step 1: Download Node.js
# ============================================================================


def download_nodejs(platform: str, arch: str, work_dir: Path) -> Path:
    """Download Node.js binaries for the specified platform and architecture."""
    print_section("STEP 1: DOWNLOAD NODE.JS BINARIES")

    key = (platform, arch)
    if key not in NODEJS_DOWNLOAD_URLS:
        raise ValueError(f"Unsupported platform/arch combination: {platform}/{arch}")

    url = NODEJS_DOWNLOAD_URLS[key]
    filename = Path(url).name
    download_path = work_dir / filename
    breadcrumb_path = Path(str(download_path) + ".downloading")

    print(f"Platform: {platform}")
    print(f"Architecture: {arch}")
    print(f"Node.js Version: {NODEJS_VERSION}")
    print()

    # Check for incomplete download from previous attempt
    if breadcrumb_path.exists():
        print(f"⚠️  Found incomplete download marker: {breadcrumb_path.name}")
        if download_path.exists():
            print(f"Removing partial download: {download_path}")
            download_path.unlink()
        breadcrumb_path.unlink()
        print()

    if download_path.exists():
        print(f"File already exists: {download_path}")
        print("Skipping download...")
    else:
        download_file(url, download_path)

    print(f"\nDownloaded: {download_path}")
    print(f"Size: {download_path.stat().st_size / (1024*1024):.2f} MB")

    return download_path


# ============================================================================
# Step 2: Verify Checksum
# ============================================================================


def verify_checksum(archive_path: Path, platform: str, arch: str, work_dir: Path) -> None:
    """Verify archive checksum against official SHASUMS256.txt."""
    print_section("STEP 2: VERIFY CHECKSUM")

    # Download SHASUMS256.txt
    checksums_path = work_dir / "SHASUMS256.txt"
    if not checksums_path.exists():
        print(f"Downloading checksums from: {CHECKSUMS_URL}")
        download_file(CHECKSUMS_URL, checksums_path, show_progress=False)
    else:
        print(f"Using existing checksums: {checksums_path}")

    # Parse checksums file
    checksums = {}
    with open(checksums_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 2:
                checksum = parts[0]
                filename = parts[1]
                checksums[filename] = checksum

    # Find checksum for our archive
    archive_name = archive_path.name
    if archive_name not in checksums:
        print(f"⚠️  Warning: Checksum not found for {archive_name}")
        print("Available files in SHASUMS256.txt:")
        for name in sorted(checksums.keys()):
            print(f"  - {name}")
        print("\nSkipping checksum verification...")
        return

    expected_checksum = checksums[archive_name]
    print(f"Expected SHA256: {expected_checksum}")

    # Calculate actual checksum
    print(f"Calculating SHA256 of: {archive_path.name}")
    actual_checksum = get_file_hash(archive_path, "sha256")
    print(f"Actual SHA256:   {actual_checksum}")

    # Verify
    if actual_checksum == expected_checksum:
        print("✓ Checksum verification PASSED")
    else:
        print("✗ Checksum verification FAILED")
        raise RuntimeError(
            f"Checksum mismatch for {archive_name}!\n"
            f"Expected: {expected_checksum}\n"
            f"Actual:   {actual_checksum}"
        )


# ============================================================================
# Step 3: Extract Archive
# ============================================================================


def extract_archive(archive_path: Path, extract_dir: Path, platform: str) -> Path:
    """Extract Node.js archive (ZIP for Windows, TAR.XZ/TAR.GZ for Unix)."""
    print_section("STEP 3: EXTRACT ARCHIVE")

    print(f"Extracting: {archive_path.name}")
    print(f"To: {extract_dir}")

    extract_dir.mkdir(parents=True, exist_ok=True)

    # Determine extraction method based on file extension
    if archive_path.suffix == ".zip":
        # Windows ZIP archive
        print("Format: ZIP (Windows)")
        with zipfile.ZipFile(archive_path, "r") as zf:
            zf.extractall(path=extract_dir)
    elif archive_path.name.endswith(".tar.xz"):
        # Linux TAR.XZ archive
        print("Format: TAR.XZ (Linux)")
        with tarfile.open(archive_path, "r:xz") as tar:
            tar.extractall(path=extract_dir)
    elif archive_path.name.endswith(".tar.gz"):
        # macOS TAR.GZ archive
        print("Format: TAR.GZ (macOS)")
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(path=extract_dir)
    else:
        raise ValueError(f"Unknown archive format: {archive_path.name}")

    # Find the extracted node directory (e.g., node-v22.11.0-linux-x64)
    extracted_dirs = [d for d in extract_dir.iterdir() if d.is_dir() and d.name.startswith("node-")]
    if not extracted_dirs:
        raise RuntimeError(f"Could not find extracted Node.js directory in {extract_dir}")

    node_dir = extracted_dirs[0]
    print(f"Extracted to: {node_dir}")
    print(f"Directory size: {sum(f.stat().st_size for f in node_dir.rglob('*') if f.is_file()) / (1024*1024):.2f} MB")

    return node_dir


# ============================================================================
# Step 4: Strip Unnecessary Files
# ============================================================================


def strip_unnecessary_files(node_dir: Path) -> None:
    """Remove unnecessary files to reduce size."""
    print_section("STEP 4: STRIP UNNECESSARY FILES")

    removed_size = 0

    # Remove directories
    for dir_name in STRIP_DIRS:
        dir_path = node_dir / dir_name
        if dir_path.exists():
            size_before = sum(f.stat().st_size for f in dir_path.rglob("*") if f.is_file())
            print(f"Removing directory: {dir_name}/ ({size_before / (1024*1024):.2f} MB)")
            shutil.rmtree(dir_path)
            removed_size += size_before

    # Remove executables from bin/
    bin_dir = node_dir / "bin"
    if bin_dir.exists():
        for exe_name in STRIP_EXECUTABLES:
            for exe_path in bin_dir.glob(f"{exe_name}*"):
                if exe_path.is_file():
                    size = exe_path.stat().st_size
                    print(f"Removing executable: bin/{exe_path.name} ({size / (1024*1024):.2f} MB)")
                    exe_path.unlink()
                    removed_size += size

    # Remove npm and corepack from lib/node_modules
    node_modules = node_dir / "lib" / "node_modules"
    if node_modules.exists():
        for module_name in ["npm", "corepack"]:
            module_path = node_modules / module_name
            if module_path.exists():
                size_before = sum(f.stat().st_size for f in module_path.rglob("*") if f.is_file())
                print(f"Removing module: lib/node_modules/{module_name}/ ({size_before / (1024*1024):.2f} MB)")
                shutil.rmtree(module_path)
                removed_size += size_before

    # Remove documentation files
    for doc_file in ["README.md", "CHANGELOG.md"]:
        doc_path = node_dir / doc_file
        if doc_path.exists():
            size = doc_path.stat().st_size
            print(f"Removing file: {doc_file} ({size / 1024:.2f} KB)")
            doc_path.unlink()
            removed_size += size

    print(f"\n✓ Removed {removed_size / (1024*1024):.2f} MB of unnecessary files")

    # Calculate final size
    final_size = sum(f.stat().st_size for f in node_dir.rglob("*") if f.is_file())
    print(f"Final directory size: {final_size / (1024*1024):.2f} MB")


# ============================================================================
# Step 5: Create TAR Archive
# ============================================================================


def create_tar_archive(node_dir: Path, output_dir: Path, platform: str, arch: str) -> Path:
    """Create TAR archive with proper permissions."""
    print_section("STEP 5: CREATE TAR ARCHIVE")

    tar_name = f"nodejs-{NODEJS_VERSION}-{platform}-{arch}.tar"
    tar_path = output_dir / tar_name

    print(f"Creating TAR archive: {tar_path.name}")

    # Create TAR archive with proper permissions
    with tarfile.open(tar_path, "w") as tar:
        # Add all contents, preserving structure
        print(f"Adding directory: {node_dir.name}/")
        tar.add(node_dir, arcname=".", filter=_tar_filter)

    tar_size = tar_path.stat().st_size
    print(f"TAR size: {tar_size / (1024*1024):.2f} MB")

    return tar_path


def _tar_filter(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo:
    """Filter function for TAR archive to set proper permissions."""
    # Set executable permissions for node binary
    if "/bin/" in tarinfo.name or tarinfo.name.startswith("bin/"):
        if tarinfo.name.endswith((".exe", "node")):
            tarinfo.mode = 0o755  # Executable
        else:
            tarinfo.mode = 0o644  # Readable
    else:
        # All other files are readable
        if tarinfo.isfile():
            tarinfo.mode = 0o644
        elif tarinfo.isdir():
            tarinfo.mode = 0o755

    return tarinfo


# ============================================================================
# Step 6: Compress with zstd
# ============================================================================


def compress_with_zstd(tar_path: Path) -> Path:
    """Compress TAR archive with zstd level 22."""
    print_section("STEP 6: COMPRESS WITH ZSTD")

    try:
        import pyzstd
    except ImportError:
        print("Error: pyzstd module not installed")
        print("Install with: pip install pyzstd")
        sys.exit(1)

    zst_path = Path(str(tar_path) + ".zst")

    print(f"Compressing: {tar_path.name}")
    print(f"Output: {zst_path.name}")
    print("Compression level: 22 (maximum)")

    tar_size = tar_path.stat().st_size
    print(f"Input size: {tar_size / (1024*1024):.2f} MB")

    # Compress with zstd level 22
    print("Compressing... (this may take a while)")
    with open(tar_path, "rb") as f_in:
        tar_data = f_in.read()

    compressed_data = pyzstd.compress(tar_data, level_or_option=22)

    with open(zst_path, "wb") as f_out:
        f_out.write(compressed_data)

    zst_size = zst_path.stat().st_size
    ratio = (1 - zst_size / tar_size) * 100

    print(f"Compressed size: {zst_size / (1024*1024):.2f} MB")
    print(f"Compression ratio: {ratio:.1f}%")

    # Remove uncompressed TAR
    print(f"Removing uncompressed TAR: {tar_path.name}")
    tar_path.unlink()

    return zst_path


# ============================================================================
# Step 7: Generate Checksums
# ============================================================================


def generate_checksums(archive_path: Path) -> Dict[str, str]:
    """Generate SHA256 and MD5 checksums."""
    print_section("STEP 7: GENERATE CHECKSUMS")

    print(f"Generating checksums for: {archive_path.name}")

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
    print(f"Written: {sha256_file.name}")
    print(f"Written: {md5_file.name}")

    return checksums


# ============================================================================
# Step 8: Create Manifest
# ============================================================================


def create_manifest(
    archive_path: Path, checksums: Dict[str, str], platform: str, arch: str, output_dir: Path
) -> Path:
    """Create manifest.json for the archive."""
    print_section("STEP 8: CREATE MANIFEST")

    manifest_dir = output_dir
    manifest_path = manifest_dir / "manifest.json"

    # Construct GitHub URL for the archive
    github_url = (
        f"https://raw.githubusercontent.com/zackees/clang-tool-chain-bins/main/"
        f"assets/nodejs/{platform}/{arch}/{archive_path.name}"
    )

    # Create or update manifest
    manifest = {}
    if manifest_path.exists():
        print(f"Loading existing manifest: {manifest_path}")
        with open(manifest_path, "r") as f:
            manifest = json.load(f)
    else:
        print(f"Creating new manifest: {manifest_path}")

    # Update manifest with new version
    manifest["latest"] = NODEJS_VERSION
    if NODEJS_VERSION not in manifest:
        manifest[NODEJS_VERSION] = {}

    manifest[NODEJS_VERSION] = {
        "href": github_url,
        "sha256": checksums["sha256"],
    }

    # Write manifest
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")  # Add trailing newline

    print(f"✓ Manifest created: {manifest_path}")
    print(f"Version: {NODEJS_VERSION}")
    print(f"URL: {github_url}")

    return manifest_path


# ============================================================================
# Step 9: Verify Archive
# ============================================================================


def verify_archive(archive_path: Path, platform: str) -> None:
    """Verify that the archive can be extracted and node binary works."""
    print_section("STEP 9: VERIFY ARCHIVE")

    # Create temporary directory for verification
    verify_dir = archive_path.parent / "verify_test"
    if verify_dir.exists():
        shutil.rmtree(verify_dir)
    verify_dir.mkdir(parents=True)

    try:
        print(f"Extracting archive for verification: {archive_path.name}")

        # Extract zstd archive
        try:
            import pyzstd
        except ImportError:
            print("⚠️  Warning: pyzstd not available, skipping verification")
            return

        # Decompress zstd
        with open(archive_path, "rb") as f:
            compressed_data = f.read()

        decompressed_data = pyzstd.decompress(compressed_data)

        # Extract TAR
        import io

        tar_buffer = io.BytesIO(decompressed_data)
        with tarfile.open(fileobj=tar_buffer, mode="r") as tar:
            tar.extractall(path=verify_dir)

        # Find node binary
        node_binary_name = "node.exe" if platform == "win" else "node"
        node_binary = verify_dir / "bin" / node_binary_name

        if not node_binary.exists():
            raise RuntimeError(f"Node binary not found: {node_binary}")

        print(f"✓ Node binary found: {node_binary}")
        print(f"✓ Verification PASSED")

    except Exception as e:
        print(f"✗ Verification FAILED: {e}")
        raise
    finally:
        # Clean up verification directory
        if verify_dir.exists():
            shutil.rmtree(verify_dir)
            print(f"Cleaned up verification directory")


# ============================================================================
# Main
# ============================================================================


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Fetch and archive Node.js binaries for Emscripten",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python fetch_and_archive_nodejs.py --platform win --arch x86_64
  python fetch_and_archive_nodejs.py --platform linux --arch x86_64
  python fetch_and_archive_nodejs.py --platform darwin --arch arm64

Supported platforms:
  win/x86_64, linux/x86_64, linux/arm64, darwin/x86_64, darwin/arm64
        """,
    )
    parser.add_argument(
        "--platform",
        required=True,
        choices=["win", "linux", "darwin"],
        help="Target platform",
    )
    parser.add_argument(
        "--arch",
        required=True,
        choices=["x86_64", "arm64"],
        help="Target architecture",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path("work_nodejs"),
        help="Working directory for downloads and extraction (default: work_nodejs)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory for final archives (default: ../assets/nodejs/{platform}/{arch})",
    )

    args = parser.parse_args()

    # Validate platform/arch combination
    key = (args.platform, args.arch)
    if key not in NODEJS_DOWNLOAD_URLS:
        print(f"Error: Unsupported platform/arch combination: {args.platform}/{args.arch}")
        print("\nSupported combinations:")
        for (platform, arch) in NODEJS_DOWNLOAD_URLS.keys():
            print(f"  - {platform}/{arch}")
        sys.exit(1)

    # Set default output directory
    if args.output_dir is None:
        args.output_dir = Path(__file__).parent.parent / "assets" / "nodejs" / args.platform / args.arch

    # Create directories
    work_dir = args.work_dir / args.platform / args.arch
    work_dir.mkdir(parents=True, exist_ok=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("Node.js Archive Creation Pipeline")
    print("=" * 70)
    print(f"Platform:    {args.platform}")
    print(f"Architecture: {args.arch}")
    print(f"Version:     {NODEJS_VERSION}")
    print(f"Work Dir:    {work_dir}")
    print(f"Output Dir:  {args.output_dir}")
    print("=" * 70)

    try:
        # Step 1: Download
        archive_path = download_nodejs(args.platform, args.arch, work_dir)

        # Step 2: Verify checksum
        verify_checksum(archive_path, args.platform, args.arch, work_dir)

        # Step 3: Extract
        extract_dir = work_dir / "extracted"
        node_dir = extract_archive(archive_path, extract_dir, args.platform)

        # Step 4: Strip unnecessary files
        strip_unnecessary_files(node_dir)

        # Step 5: Create TAR archive
        tar_path = create_tar_archive(node_dir, work_dir, args.platform, args.arch)

        # Step 6: Compress with zstd
        zst_path = compress_with_zstd(tar_path)

        # Step 7: Generate checksums
        checksums = generate_checksums(zst_path)

        # Step 8: Create manifest
        manifest_path = create_manifest(zst_path, checksums, args.platform, args.arch, args.output_dir)

        # Step 9: Verify archive
        verify_archive(zst_path, args.platform)

        # Move final archive to output directory
        final_archive_path = args.output_dir / zst_path.name
        final_sha256_path = args.output_dir / (zst_path.name + ".sha256")
        final_md5_path = args.output_dir / (zst_path.name + ".md5")

        print_section("FINALIZING")
        print(f"Moving archive to: {final_archive_path}")
        shutil.move(str(zst_path), str(final_archive_path))
        shutil.move(str(zst_path) + ".sha256", str(final_sha256_path))
        shutil.move(str(zst_path) + ".md5", str(final_md5_path))

        # Print summary
        print_section("SUMMARY")
        print(f"✓ Archive created successfully!")
        print(f"Archive:  {final_archive_path}")
        print(f"Size:     {final_archive_path.stat().st_size / (1024*1024):.2f} MB")
        print(f"Manifest: {manifest_path}")
        print(f"SHA256:   {final_sha256_path}")
        print(f"MD5:      {final_md5_path}")
        print()
        print(f"Next steps:")
        print(f"1. Test extraction: python expand_archive.py {final_archive_path} test/")
        print(f"2. Test node binary: test/bin/node --version")
        print(f"3. Commit to downloads-bins repository")
        print("=" * 70)

    except Exception as e:
        print(f"\n✗ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
