#!/usr/bin/env python3
"""
Extract clang archives (tar.zst) for LLDB archive creation.

This helper script:
1. Decompresses zstd-compressed archives
2. Extracts tar contents to work directory
3. Locates LLDB binaries (lldb, lldb-server, lldb-argdumper)
4. Reports extraction status and binary locations

Usage:
    python3 tools/extract_clang_archive.py \
        --archive assets/clang/linux/x86_64/llvm-21.1.5-linux-x86_64.tar.zst \
        --output work/llvm_linux_x64

Requirements:
    - zstandard module (installed via: uv pip install zstandard)
    - tarfile module (built-in)
"""

import argparse
import sys
import tarfile
from pathlib import Path

try:
    import zstandard as zstd
except ImportError:
    print("ERROR: zstandard module not found", file=sys.stderr)
    print("Install with: uv pip install zstandard", file=sys.stderr)
    sys.exit(1)


def extract_clang_archive(archive_path: Path, output_dir: Path) -> dict:
    """
    Extract clang archive and locate LLDB binaries.

    Args:
        archive_path: Path to tar.zst archive
        output_dir: Directory to extract to

    Returns:
        dict with status, extracted_dir, and lldb_binaries paths
    """
    if not archive_path.exists():
        return {
            "status": "error",
            "message": f"Archive not found: {archive_path}",
        }

    print(f"Extracting {archive_path.name} ({archive_path.stat().st_size / (1024 * 1024):.1f} MB)...")

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Decompress zstd + extract tar
    try:
        with open(archive_path, 'rb') as compressed:
            dctx = zstd.ZstdDecompressor()
            with dctx.stream_reader(compressed) as reader, tarfile.open(fileobj=reader, mode='r|') as tar:
                # Extract all files
                tar.extractall(path=output_dir)

        print(f"✓ Extracted to: {output_dir}")
    except Exception as e:
        return {
            "status": "error",
            "message": f"Extraction failed: {e}",
        }

    # Find extracted directory (usually has nested structure)
    extracted_subdirs = [d for d in output_dir.iterdir() if d.is_dir()]

    if not extracted_subdirs:
        return {
            "status": "error",
            "message": f"No subdirectories found in {output_dir}",
        }

    # Usually the archive extracts to a single subdirectory
    extracted_dir = extracted_subdirs[0] if len(extracted_subdirs) == 1 else output_dir

    print(f"✓ Extracted directory: {extracted_dir}")

    # Locate LLDB binaries
    lldb_binaries = {}
    bin_dir = extracted_dir / "bin"

    if bin_dir.exists():
        for binary_name in ["lldb", "lldb-server", "lldb-argdumper"]:
            binary_path = bin_dir / binary_name
            if binary_path.exists():
                lldb_binaries[binary_name] = binary_path
                size_mb = binary_path.stat().st_size / (1024 * 1024)
                print(f"  ✓ Found: {binary_name} ({size_mb:.1f} MB)")
            else:
                print(f"  ✗ Missing: {binary_name}")
    else:
        print(f"  ✗ bin/ directory not found in {extracted_dir}")

    return {
        "status": "success",
        "extracted_dir": extracted_dir,
        "lldb_binaries": lldb_binaries,
        "lldb_count": len(lldb_binaries),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Extract clang archive for LLDB archive creation"
    )
    parser.add_argument(
        "--archive",
        type=Path,
        required=True,
        help="Path to tar.zst archive (e.g., assets/clang/linux/x86_64/llvm-21.1.5-linux-x86_64.tar.zst)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output directory for extraction (e.g., work/llvm_linux_x64)",
    )

    args = parser.parse_args()

    # Extract archive
    result = extract_clang_archive(args.archive, args.output)

    if result["status"] == "error":
        print(f"\n❌ ERROR: {result['message']}", file=sys.stderr)
        sys.exit(1)

    # Success
    print("\n✅ Extraction complete!")
    print(f"   Extracted directory: {result['extracted_dir']}")
    print(f"   LLDB binaries found: {result['lldb_count']}/3")

    if result['lldb_count'] < 3:
        print("\n⚠️  WARNING: Not all LLDB binaries found (expected 3: lldb, lldb-server, lldb-argdumper)")
        sys.exit(1)


if __name__ == "__main__":
    main()
