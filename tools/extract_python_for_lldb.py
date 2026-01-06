#!/usr/bin/env python3
"""
Extract Python 3.10 modules for LLDB distribution.

This script extracts Python site-packages required for LLDB Python scripting:
1. Python 3.10 standard library (python310.zip from embeddable package)
2. LLDB Python module (from official LLVM installer)

The extracted Python environment enables full LLDB functionality including:
- Full "bt all" backtraces
- Advanced variable inspection
- Python scripting and API access
- LLDB formatters and plugins

Output structure:
    python/
    ├── python310.zip                    # Standard library (2.52 MB compressed)
    └── Lib/
        └── site-packages/
            └── lldb/                     # LLDB Python module (101 MB → 30 MB compressed)
                ├── __init__.py
                ├── _lldb.cp310-win_amd64.pyd
                ├── formatters/
                ├── plugins/
                └── utils/

Size Impact:
- Python standard library: 2.52 MB compressed
- LLDB Python module: ~30 MB compressed (101 MB uncompressed)
- Total additional: ~32.5 MB compressed
- Final LLDB archive: ~61.5 MB compressed (from 29 MB)
"""

import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

# Python 3.10.11 embeddable package URL
PYTHON_EMBEDDABLE_URL = "https://www.python.org/ftp/python/3.10.11/python-3.10.11-embed-amd64.zip"

# LLVM versions for each platform (must match create_lldb_archives.py)
LLVM_VERSIONS = {
    "win": "21.1.5",
    "linux": "21.1.5",
    "darwin": "21.1.6",
}

# LLVM download URLs (must match create_lldb_archives.py)
LLVM_DOWNLOAD_URLS = {
    ("win", "x86_64"): "https://github.com/llvm/llvm-project/releases/download/llvmorg-{version}/LLVM-{version}-win64.exe",
    ("win", "arm64"): "https://github.com/llvm/llvm-project/releases/download/llvmorg-{version}/LLVM-{version}-woa64.exe",
    ("linux", "x86_64"): "https://github.com/llvm/llvm-project/releases/download/llvmorg-{version}/LLVM-{version}-Linux-X64.tar.xz",
    ("linux", "arm64"): "https://github.com/llvm/llvm-project/releases/download/llvmorg-{version}/clang+llvm-{version}-aarch64-linux-gnu.tar.xz",
    ("darwin", "x86_64"): "https://github.com/llvm/llvm-project/releases/download/llvmorg-{version}/LLVM-{version}-macOS-X64.tar.xz",
    ("darwin", "arm64"): "https://github.com/llvm/llvm-project/releases/download/llvmorg-{version}/LLVM-{version}-macOS-ARM64.tar.xz",
}


def download_file(url: str, destination: Path) -> Path:
    """Download a file with progress reporting."""
    if destination.exists():
        print(f"✓ File already exists: {destination}")
        return destination

    print(f"Downloading: {url}")
    print(f"Destination: {destination}")
    print()

    destination.parent.mkdir(parents=True, exist_ok=True)

    def show_progress(block_num: int, block_size: int, total_size: int) -> None:
        if total_size > 0:
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
        urllib.request.urlretrieve(url, destination, reporthook=show_progress)
        print()  # New line after progress
        print(f"✓ Downloaded: {destination.stat().st_size / (1024*1024):.2f} MB")
        return destination
    except Exception as e:
        if destination.exists():
            destination.unlink()
        raise RuntimeError(f"Failed to download {url}: {e}") from e


def extract_python_embeddable(zip_path: Path, output_dir: Path) -> Path:
    """
    Extract Python embeddable package and get python310.zip.

    Args:
        zip_path: Path to python-3.10.11-embed-amd64.zip
        output_dir: Output directory for extraction

    Returns:
        Path to extracted python310.zip
    """
    print("\n" + "=" * 70)
    print("EXTRACTING PYTHON EMBEDDABLE PACKAGE")
    print("=" * 70)
    print(f"Source: {zip_path}")
    print(f"Output: {output_dir}")
    print()

    import zipfile

    extract_temp = output_dir / "python_embeddable_temp"
    extract_temp.mkdir(parents=True, exist_ok=True)

    print("Extracting embeddable package...")
    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        zip_ref.extractall(extract_temp)

    # Find python310.zip
    python310_zip = extract_temp / "python310.zip"
    if not python310_zip.exists():
        raise RuntimeError(f"python310.zip not found in {extract_temp}")

    # Copy python310.zip to output directory
    dest_python_zip = output_dir / "python310.zip"
    shutil.copy2(python310_zip, dest_python_zip)

    size_mb = dest_python_zip.stat().st_size / (1024 * 1024)
    print(f"✓ Extracted python310.zip ({size_mb:.2f} MB)")

    # Clean up temp directory
    shutil.rmtree(extract_temp)

    return dest_python_zip


def extract_llvm_archive(archive_path: Path, extract_dir: Path, platform: str) -> Path:
    """
    Extract LLVM archive and find root directory.

    Args:
        archive_path: Path to LLVM archive
        extract_dir: Directory to extract to
        platform: Platform name (win, linux, darwin)

    Returns:
        Path to LLVM root directory
    """
    print("\n" + "=" * 70)
    print("EXTRACTING LLVM ARCHIVE")
    print("=" * 70)
    print(f"Source: {archive_path}")
    print(f"Output: {extract_dir}")
    print()

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
            import tarfile

            with lzma.open(archive_path) as xz_file, tarfile.open(fileobj=xz_file) as tar:
                tar.extractall(path=extract_dir)

    else:
        raise ValueError(f"Unsupported archive format: {archive_path.suffix}")

    print(f"✓ Extracted to: {extract_dir}")

    # Find LLVM root directory
    # First check if extract_dir itself is the root (Windows .exe case)
    if (extract_dir / "lib" / "site-packages" / "lldb").exists() or (extract_dir / "bin").exists():
        llvm_root = extract_dir
        print(f"✓ Found LLVM root: {llvm_root}")
        return llvm_root

    # Otherwise look for subdirectories (Linux/macOS .tar.xz case)
    llvm_root = None
    for item in extract_dir.iterdir():
        if item.is_dir():
            # Check if it has lib/site-packages/lldb/ (or bin/ for fallback)
            if (item / "lib" / "site-packages" / "lldb").exists() or (item / "bin").exists():
                llvm_root = item
                break

    if not llvm_root:
        raise RuntimeError(f"Could not find LLVM root directory in {extract_dir}")

    print(f"✓ Found LLVM root: {llvm_root}")
    return llvm_root


def extract_lldb_python_module(llvm_root: Path, output_dir: Path) -> Path:
    """
    Extract LLDB Python module from LLVM installation.

    Args:
        llvm_root: Root directory of extracted LLVM
        output_dir: Output directory (should end with site-packages/)

    Returns:
        Path to extracted lldb/ module directory
    """
    print("\n" + "=" * 70)
    print("EXTRACTING LLDB PYTHON MODULE")
    print("=" * 70)
    print(f"Source: {llvm_root}")
    print(f"Output: {output_dir}")
    print()

    # Find LLDB Python module
    lldb_source = llvm_root / "lib" / "site-packages" / "lldb"
    if not lldb_source.exists():
        raise RuntimeError(f"LLDB Python module not found at {lldb_source}")

    # Create site-packages directory
    site_packages = output_dir / "Lib" / "site-packages"
    site_packages.mkdir(parents=True, exist_ok=True)

    # Copy LLDB module
    lldb_dest = site_packages / "lldb"
    if lldb_dest.exists():
        shutil.rmtree(lldb_dest)

    print(f"Copying LLDB module from: {lldb_source}")
    print(f"                      to: {lldb_dest}")
    shutil.copytree(lldb_source, lldb_dest)

    # Count files and calculate size
    file_count = sum(1 for _ in lldb_dest.rglob("*") if _.is_file())
    total_size = sum(f.stat().st_size for f in lldb_dest.rglob("*") if f.is_file())
    size_mb = total_size / (1024 * 1024)

    print(f"✓ Copied LLDB Python module: {file_count} files, {size_mb:.1f} MB")

    # List key files
    print("\nKey LLDB Python files:")
    key_files = [
        "__init__.py",
        "_lldb.cp310-win_amd64.pyd",
        "lldb-argdumper.exe",
    ]
    for key_file in key_files:
        file_path = lldb_dest / key_file
        if file_path.exists():
            file_size = file_path.stat().st_size / (1024 * 1024)
            print(f"  ✓ {key_file:30s} ({file_size:6.1f} MB)")
        else:
            print(f"  - {key_file:30s} (not found - may be platform-specific)")

    return lldb_dest


def create_python_environment(platform: str, arch: str, work_dir: Path, llvm_source_dir: Path | None = None) -> Path:
    """
    Create complete Python environment for LLDB.

    Args:
        platform: Platform name (win, linux, darwin)
        arch: Architecture (x86_64, arm64)
        work_dir: Working directory for downloads and extraction
        llvm_source_dir: Optional existing LLVM extraction directory

    Returns:
        Path to python/ directory ready for LLDB archive
    """
    print("\n" + "=" * 70)
    print(f"CREATING PYTHON ENVIRONMENT: {platform}/{arch}")
    print("=" * 70)
    print()

    work_dir.mkdir(parents=True, exist_ok=True)

    # Output python/ directory
    python_dir = work_dir / "python"
    if python_dir.exists():
        print(f"Removing existing python directory: {python_dir}")
        shutil.rmtree(python_dir)
    python_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Download and extract Python embeddable package
    if platform == "win":
        print("\n[1/3] Downloading Python 3.10.11 embeddable package...")
        python_zip = download_file(PYTHON_EMBEDDABLE_URL, work_dir / "python-3.10.11-embed-amd64.zip")
        extract_python_embeddable(python_zip, python_dir)
    else:
        print(f"\n[1/3] Skipping Python embeddable package (not needed for {platform})")

    # Step 2: Get LLVM source
    print("\n[2/3] Getting LLVM source...")
    if llvm_source_dir and llvm_source_dir.exists():
        print(f"✓ Using provided LLVM source: {llvm_source_dir}")
        llvm_root = llvm_source_dir
    else:
        # Download LLVM
        version = LLVM_VERSIONS.get(platform)
        if not version:
            raise ValueError(f"No LLVM version defined for platform {platform}")

        key = (platform, arch)
        if key not in LLVM_DOWNLOAD_URLS:
            raise ValueError(f"Unsupported platform/arch: {platform}/{arch}")

        url = LLVM_DOWNLOAD_URLS[key].format(version=version)
        filename = Path(url).name
        llvm_archive = download_file(url, work_dir / filename)

        # Extract LLVM
        extract_dir = work_dir / "llvm_extracted"
        llvm_root = extract_llvm_archive(llvm_archive, extract_dir, platform)

    # Step 3: Extract LLDB Python module
    print("\n[3/3] Extracting LLDB Python module...")
    extract_lldb_python_module(llvm_root, python_dir)

    print("\n" + "=" * 70)
    print("PYTHON ENVIRONMENT CREATED")
    print("=" * 70)
    print(f"Location: {python_dir}")
    print()

    # Show directory structure
    print("Directory structure:")
    for item in sorted(python_dir.rglob("*")):
        if item.is_file():
            rel_path = item.relative_to(python_dir)
            size = item.stat().st_size / (1024 * 1024)
            if size > 1.0:  # Only show files > 1 MB
                print(f"  {rel_path} ({size:.1f} MB)")

    # Calculate total size
    total_size = sum(f.stat().st_size for f in python_dir.rglob("*") if f.is_file())
    print(f"\nTotal size: {total_size / (1024*1024):.1f} MB uncompressed")
    print(f"Expected compressed (zstd-22): ~{total_size / (1024*1024) * 0.3:.1f} MB")

    return python_dir


def main() -> None:
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract Python modules for LLDB distribution",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
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
        default=Path("work/python_extraction"),
        help="Working directory for downloads and extraction (default: work/python_extraction)",
    )
    parser.add_argument(
        "--llvm-source-dir",
        type=Path,
        help="Use existing LLVM extraction directory (skip download/extract)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory for python/ (default: work-dir/python)",
    )

    args = parser.parse_args()

    work_dir = args.work_dir.resolve()

    print("=" * 70)
    print("EXTRACT PYTHON FOR LLDB")
    print("=" * 70)
    print(f"Platform:  {args.platform}")
    print(f"Arch:      {args.arch}")
    print(f"Work dir:  {work_dir}")
    if args.llvm_source_dir:
        print(f"LLVM src:  {args.llvm_source_dir}")
    print()

    try:
        python_dir = create_python_environment(args.platform, args.arch, work_dir, args.llvm_source_dir)

        # Optionally copy to different output directory
        if args.output_dir:
            output_dir = args.output_dir.resolve()
            print(f"\nCopying to output directory: {output_dir}")
            if output_dir.exists():
                shutil.rmtree(output_dir)
            shutil.copytree(python_dir, output_dir)
            print(f"✓ Copied to: {output_dir}")

        print("\n✅ SUCCESS!")
        print("\nNext steps:")
        print("1. Use this python/ directory in LLDB archive creation")
        print("2. Update create_lldb_archives.py to include python/ in archives")
        print("3. Test LLDB with Python modules")

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
