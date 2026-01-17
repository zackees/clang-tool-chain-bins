#!/usr/bin/env python3
"""
Fetch and archive Cosmopolitan (cosmocc) toolchain.

This script downloads the cosmocc toolchain from cosmo.zip and repackages
it as a .tar.zst archive for consistency with other clang-tool-chain archives.

Cosmocc is universal - the same binaries run on all platforms (Windows, Linux,
macOS, FreeBSD, NetBSD, OpenBSD) without modification.

Usage:
    python fetch_and_archive_cosmocc.py [--version VERSION] [--output-dir DIR]

Example:
    python fetch_and_archive_cosmocc.py --version 4.0.2
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

# Default version
DEFAULT_VERSION = "4.0.2"

# Download URL template
COSMOCC_URL_TEMPLATE = "https://cosmo.zip/pub/cosmocc/cosmocc-{version}.zip"

# Output directory
DEFAULT_OUTPUT_DIR = Path(__file__).parent.parent / "assets" / "cosmocc"


def download_file(url: str, dest: Path) -> None:
    """Download a file with progress indication."""
    print(f"Downloading: {url}")
    print(f"Destination: {dest}")

    # Use urllib to download
    with urllib.request.urlopen(url) as response:
        total_size = int(response.headers.get("Content-Length", 0))
        downloaded = 0
        chunk_size = 8192 * 16  # 128KB chunks

        with open(dest, "wb") as f:
            while True:
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)

                if total_size > 0:
                    percent = (downloaded / total_size) * 100
                    mb_downloaded = downloaded / (1024 * 1024)
                    mb_total = total_size / (1024 * 1024)
                    print(f"\r  Progress: {mb_downloaded:.1f}/{mb_total:.1f} MB ({percent:.1f}%)", end="", flush=True)

    print()  # Newline after progress


def calculate_sha256(file_path: Path) -> str:
    """Calculate SHA256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192 * 16), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def extract_zip(zip_path: Path, dest_dir: Path) -> None:
    """Extract a zip file."""
    import zipfile

    print(f"Extracting: {zip_path}")
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(dest_dir)
    print(f"  Extracted to: {dest_dir}")


def get_directory_size(path: Path) -> int:
    """Calculate total size of a directory in bytes."""
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                total += entry.stat().st_size
    except (PermissionError, FileNotFoundError):
        pass
    return total


def strip_unnecessary_files(source_dir: Path) -> dict:
    """
    Remove unnecessary files from cosmocc to reduce archive size.

    Removes:
    - */lib/dbg (debug libraries, ~391 MB)
    - */lib/optlinux (Linux-specific optimizations that defeat APE portability, ~150 MB)
    - */lib/tiny (tiny variants, nice-to-have but not essential, ~41 MB)

    Returns:
        dict: Statistics about removed files and bytes saved
    """
    patterns_to_remove = [
        "*/lib/dbg",
        "*/lib/optlinux",
        "*/lib/tiny",
    ]

    total_bytes_removed = 0
    total_files_removed = 0
    removed_dirs = []

    print("Scanning for unnecessary files to remove...")

    for pattern in patterns_to_remove:
        # Find all matching directories
        matching_paths = list(source_dir.glob(pattern))

        for path in matching_paths:
            if path.exists() and path.is_dir():
                # Calculate size before removal
                dir_size = get_directory_size(path)

                # Count files
                file_count = sum(1 for _ in path.rglob("*") if _.is_file())

                # Remove directory
                shutil.rmtree(path)

                total_bytes_removed += dir_size
                total_files_removed += file_count
                removed_dirs.append(str(path.relative_to(source_dir)))

                mb_removed = dir_size / (1024 * 1024)
                print(f"  Removed: {path.relative_to(source_dir)} ({mb_removed:.1f} MB, {file_count} files)")

    stats = {
        "bytes_removed": total_bytes_removed,
        "files_removed": total_files_removed,
        "directories_removed": removed_dirs,
    }

    return stats


def create_tar_zst(source_dir: Path, output_file: Path, compression_level: int = 22) -> None:
    """Create a .tar.zst archive from a directory."""
    print(f"Creating archive: {output_file}")
    print(f"  Source: {source_dir}")
    print(f"  Compression level: {compression_level}")

    # Use tar and zstd
    # First create tar, then compress with zstd
    tar_file = output_file.with_suffix("")  # Remove .zst suffix for intermediate tar

    # Create tar archive
    # On Windows, use Python's tarfile module
    import tarfile

    print("  Creating tar archive...")
    with tarfile.open(tar_file, "w") as tar:
        # Add the contents of source_dir (not the directory itself)
        for item in source_dir.iterdir():
            tar.add(item, arcname=item.name)

    print("  Compressing with zstd...")
    # Compress with zstd
    try:
        import zstandard as zstd

        with open(tar_file, "rb") as f_in:
            with open(output_file, "wb") as f_out:
                cctx = zstd.ZstdCompressor(level=compression_level)
                cctx.copy_stream(f_in, f_out)
    except ImportError:
        # Fallback to command-line zstd
        subprocess.run(
            ["zstd", f"-{compression_level}", "-f", str(tar_file), "-o", str(output_file)],
            check=True,
        )

    # Remove intermediate tar file
    tar_file.unlink()

    # Report size
    size_mb = output_file.stat().st_size / (1024 * 1024)
    print(f"  Archive size: {size_mb:.1f} MB")


def update_manifest(output_dir: Path, version: str, archive_name: str, sha256: str) -> None:
    """Update the manifest-universal.json file."""
    manifest_path = output_dir / "manifest-universal.json"

    # Load existing manifest or create new
    if manifest_path.exists():
        with open(manifest_path) as f:
            manifest = json.load(f)
    else:
        manifest = {"latest": version, "versions": {}}

    # Update with new version
    manifest["latest"] = version
    manifest["versions"][version] = {
        "href": f"https://media.githubusercontent.com/media/zackees/clang-tool-chain-bins/main/assets/cosmocc/{archive_name}",
        "sha256": sha256,
    }

    # Write manifest
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")

    print(f"Updated manifest: {manifest_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch and archive Cosmopolitan (cosmocc) toolchain")
    parser.add_argument("--version", default=DEFAULT_VERSION, help=f"Cosmocc version (default: {DEFAULT_VERSION})")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Output directory")
    parser.add_argument("--compression-level", type=int, default=22, help="Zstd compression level (default: 22)")
    parser.add_argument("--keep-temp", action="store_true", help="Keep temporary files")

    args = parser.parse_args()

    version = args.version
    output_dir = args.output_dir
    compression_level = args.compression_level

    print("=" * 60)
    print("Cosmopolitan (cosmocc) Archive Builder")
    print("=" * 60)
    print(f"Version: {version}")
    print(f"Output directory: {output_dir}")
    print()

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Create temporary directory
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Download URL
        url = COSMOCC_URL_TEMPLATE.format(version=version)
        zip_path = temp_path / f"cosmocc-{version}.zip"

        # Download
        print("Step 1: Downloading cosmocc...")
        download_file(url, zip_path)

        # Extract
        print("\nStep 2: Extracting...")
        extract_dir = temp_path / "extracted"
        extract_dir.mkdir()
        extract_zip(zip_path, extract_dir)

        # Find the actual content directory (may be nested)
        content_dirs = list(extract_dir.iterdir())
        if len(content_dirs) == 1 and content_dirs[0].is_dir():
            # Single directory extracted - use its contents
            source_dir = content_dirs[0]
        else:
            source_dir = extract_dir

        # Strip unnecessary files
        print("\nStep 2.5: Removing unnecessary files to reduce archive size...")
        strip_stats = strip_unnecessary_files(source_dir)
        mb_removed = strip_stats["bytes_removed"] / (1024 * 1024)
        print(f"  Total removed: {mb_removed:.1f} MB ({strip_stats['files_removed']} files)")
        print(f"  Directories removed: {len(strip_stats['directories_removed'])}")

        # Create archive
        print("\nStep 3: Creating .tar.zst archive...")
        archive_name = f"cosmocc-universal-{version}.tar.zst"
        archive_path = output_dir / archive_name
        create_tar_zst(source_dir, archive_path, compression_level)

        # Calculate SHA256
        print("\nStep 4: Calculating SHA256...")
        sha256 = calculate_sha256(archive_path)
        print(f"  SHA256: {sha256}")

        # Write SHA256 file
        sha256_path = archive_path.with_suffix(".tar.zst.sha256")
        with open(sha256_path, "w") as f:
            f.write(f"{sha256}  {archive_name}\n")
        print(f"  Written: {sha256_path}")

        # Update manifest
        print("\nStep 5: Updating manifest...")
        update_manifest(output_dir, version, archive_name, sha256)

        if args.keep_temp:
            print(f"\nTemporary files kept at: {temp_path}")
            # Move temp to a persistent location
            keep_dir = output_dir / "temp"
            shutil.copytree(temp_path, keep_dir, dirs_exist_ok=True)

    print("\n" + "=" * 60)
    print("Done!")
    print("=" * 60)
    print(f"\nArchive: {archive_path}")
    print(f"Size: {archive_path.stat().st_size / (1024 * 1024):.1f} MB")
    print(f"SHA256: {sha256}")
    print("\nNext steps:")
    print("  1. Commit and push the archive to Git LFS")
    print("  2. Verify the manifest URL is accessible")

    return 0


if __name__ == "__main__":
    sys.exit(main())
