#!/usr/bin/env python3
"""
Prepare Python 3.10 environment for Linux LLDB.

This script prepares a minimal Python 3.10 environment for LLDB on Linux:
1. Python 3.10.19 standard library (minimized from 43 MB → 11 MB)
2. LLDB Python module from Debian Jammy python3-lldb-21 packages
3. Proper symlinks for _lldb.so → liblldb.so.21

Output structure:
    python/
    └── Lib/
        ├── site-packages/
        │   └── lldb/                     # LLDB Python module (~890 KB)
        │       ├── __init__.py           (770 KB)
        │       ├── _lldb.cpython-310-{arch}-linux-gnu.so (symlink)
        │       ├── embedded_interpreter.py
        │       ├── formatters/
        │       ├── plugins/
        │       └── utils/
        ├── encodings/                    # From Python 3.10.19
        ├── collections/
        ├── os.py
        └── ...                           # Core Python modules (minimized)

Size Impact:
- Python standard library (minimized): ~11 MB uncompressed → ~2-3 MB compressed
- LLDB Python module: ~890 KB uncompressed → ~200-300 KB compressed
- Total: ~12 MB uncompressed → ~2-3 MB compressed
- Final LLDB archive: ~8 MB → ~10-11 MB per platform

Excluded from stdlib (32 MB saved):
- test/ (24 MB) - Python test suite
- idlelib/ (1.9 MB) - IDLE editor
- ensurepip/ (3.2 MB) - pip installer
- distutils/ (1.1 MB) - Package management
- lib2to3/ (870 KB) - Python 2 to 3 converter
- tkinter/ (686 KB) - GUI toolkit
- turtledemo/ (110 KB) - Turtle demos
"""

import argparse
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

# Python 3.10.19 source tarball URL
PYTHON_SOURCE_URL = "https://www.python.org/ftp/python/3.10.19/Python-3.10.19.tar.xz"
PYTHON_VERSION = "3.10.19"

# Debian Jammy python3-lldb-21 package URLs (Python 3.10 compatible)
LLDB_PYTHON_PACKAGES = {
    "x86_64": {
        "url": "https://apt.llvm.org/jammy/pool/main/l/llvm-toolchain-21/python3-lldb-21_21.1.5~++20251023083201+45afac62e373-1~exp1~20251023083316.53_amd64.deb",
        "arch_triple": "x86_64-linux-gnu",
    },
    "arm64": {
        "url": "https://apt.llvm.org/jammy/pool/main/l/llvm-toolchain-21/python3-lldb-21_21.1.5~++20251023083201+45afac62e373-1~exp1~20251023083316.53_arm64.deb",
        "arch_triple": "aarch64-linux-gnu",
    },
}

# Modules to exclude from Python stdlib (32 MB total)
EXCLUDED_MODULES = [
    "test",  # 24 MB - Test suite
    "idlelib",  # 1.9 MB - IDLE editor
    "ensurepip",  # 3.2 MB - pip installer
    "distutils",  # 1.1 MB - Package management
    "lib2to3",  # 870 KB - Python 2→3 converter
    "tkinter",  # 686 KB - GUI toolkit
    "turtledemo",  # 110 KB - Turtle demos
]


def run_command(cmd: list[str], description: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run a shell command with error handling."""
    print(f"  Running: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, check=check, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"    stderr: {result.stderr}")
        return result
    except subprocess.CalledProcessError as e:
        print(f"  ❌ Command failed: {e}")
        print(f"    stdout: {e.stdout}")
        print(f"    stderr: {e.stderr}")
        if check:
            raise
        return e


def download_file(url: str, destination: Path) -> Path:
    """Download a file if not already cached."""
    if destination.exists():
        print(f"  ✓ Already cached: {destination}")
        return destination

    print(f"  Downloading: {url}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    import urllib.request

    def show_progress(block_num: int, block_size: int, total_size: int) -> None:
        if total_size > 0:
            downloaded = block_num * block_size
            percent = min(100, (downloaded / total_size) * 100)
            mb_downloaded = downloaded / (1024 * 1024)
            mb_total = total_size / (1024 * 1024)
            print(f"\r    Progress: {percent:5.1f}% ({mb_downloaded:6.1f} MB / {mb_total:6.1f} MB)", end="", flush=True)

    try:
        urllib.request.urlretrieve(url, destination, reporthook=show_progress)
        print()  # New line after progress
        size_mb = destination.stat().st_size / (1024 * 1024)
        print(f"  ✓ Downloaded: {size_mb:.2f} MB")
        return destination
    except Exception as e:
        if destination.exists():
            destination.unlink()
        raise RuntimeError(f"Failed to download {url}: {e}") from e


def extract_python_stdlib(python_tarball: Path, output_lib_dir: Path) -> None:
    """
    Extract Python 3.10.19 standard library with exclusions.

    Args:
        python_tarball: Path to Python-3.10.19.tar.xz
        output_lib_dir: Output directory for Lib/ (e.g., python/Lib/)
    """
    print("\n[2/4] Extracting Python standard library...")
    print(f"  Source: {python_tarball}")
    print(f"  Output: {output_lib_dir}")

    # Extract tarball to temp directory
    temp_extract = output_lib_dir.parent / "python_temp"
    if temp_extract.exists():
        shutil.rmtree(temp_extract)
    temp_extract.mkdir(parents=True, exist_ok=True)

    print("  Extracting Python source tarball...")
    with tarfile.open(python_tarball, "r:xz") as tar:
        tar.extractall(temp_extract)

    # Find Lib/ directory in extracted source
    python_source_dir = temp_extract / f"Python-{PYTHON_VERSION}"
    source_lib = python_source_dir / "Lib"
    if not source_lib.exists():
        raise RuntimeError(f"Lib/ directory not found in {python_source_dir}")

    # Copy Lib/ with exclusions
    print("  Copying Lib/ directory with exclusions...")
    if output_lib_dir.exists():
        shutil.rmtree(output_lib_dir)
    output_lib_dir.mkdir(parents=True, exist_ok=True)

    # Count excluded size
    excluded_size = 0
    for item in source_lib.iterdir():
        if item.name in EXCLUDED_MODULES:
            if item.is_dir():
                size = sum(f.stat().st_size for f in item.rglob("*") if f.is_file())
            else:
                size = item.stat().st_size
            excluded_size += size
            print(f"    ✗ Excluding: {item.name:20s} ({size / (1024*1024):6.1f} MB)")

    # Copy non-excluded items
    copied_count = 0
    copied_size = 0
    for item in source_lib.iterdir():
        if item.name not in EXCLUDED_MODULES:
            dest = output_lib_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dest)
                size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file())
            else:
                shutil.copy2(item, dest)
                size = dest.stat().st_size
            copied_size += size
            copied_count += 1

    # Clean up temp directory
    shutil.rmtree(temp_extract)

    print(f"  ✓ Copied {copied_count} items ({copied_size / (1024*1024):.1f} MB)")
    print(f"  ✓ Excluded {len(EXCLUDED_MODULES)} modules ({excluded_size / (1024*1024):.1f} MB saved)")
    print(f"  ✓ Size reduction: {excluded_size / (excluded_size + copied_size) * 100:.1f}%")


def extract_deb_package(deb_path: Path, output_dir: Path) -> None:
    """
    Extract .deb package (ar + tar + zstd).

    Args:
        deb_path: Path to .deb package
        output_dir: Output directory for extraction
    """
    print(f"  Extracting .deb package: {deb_path.name}")

    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Extract .deb (ar archive)
    temp_deb = output_dir / "deb_temp"
    temp_deb.mkdir(parents=True, exist_ok=True)

    # Use ar to extract .deb
    run_command(["ar", "x", str(deb_path)], "Extract .deb with ar", check=True)

    # Find data.tar.* (usually data.tar.zst or data.tar.xz)
    data_tar = None
    for pattern in ["data.tar.zst", "data.tar.xz", "data.tar.gz"]:
        candidate = Path(pattern)
        if candidate.exists():
            data_tar = candidate
            break

    if not data_tar:
        raise RuntimeError("data.tar.* not found in .deb package")

    print(f"    Found: {data_tar}")

    # Extract data.tar with tar
    if data_tar.name.endswith(".zst"):
        # Need zstd decompression - use Python zstandard library
        print("    Decompressing with zstandard...")
        try:
            import zstandard as zstd
        except ImportError:
            raise RuntimeError("zstandard library required. Install with: pip install zstandard")

        # Decompress data.tar.zst to data.tar
        data_tar_decompressed = Path("data.tar")
        with open(data_tar, "rb") as compressed:
            dctx = zstd.ZstdDecompressor()
            with open(data_tar_decompressed, "wb") as decompressed:
                dctx.copy_stream(compressed, decompressed)

        # Extract data.tar
        run_command(["tar", "-xf", str(data_tar_decompressed), "-C", str(output_dir)], "Extract data.tar")

        # Clean up decompressed tar
        data_tar_decompressed.unlink()
    else:
        run_command(["tar", "-xf", str(data_tar), "-C", str(output_dir)], "Extract data.tar")

    # Clean up ar extraction files
    for f in Path(".").glob("*.tar.*"):
        f.unlink()
    for f in ["debian-binary", "control.tar.zst", "control.tar.xz", "control.tar.gz"]:
        p = Path(f)
        if p.exists():
            p.unlink()

    print(f"  ✓ Extracted to: {output_dir}")


def extract_lldb_python_module(deb_path: Path, arch: str, site_packages_dir: Path, lib_dir: Path) -> None:
    """
    Extract LLDB Python module from Debian package and create symlinks.

    Args:
        deb_path: Path to python3-lldb-21_*.deb
        arch: Architecture (x86_64 or arm64)
        site_packages_dir: Output site-packages/ directory
        lib_dir: lib/ directory containing liblldb.so.21 (for symlink calculation)
    """
    print("\n[3/4] Extracting LLDB Python module...")
    print(f"  Source: {deb_path.name}")
    print(f"  Architecture: {arch}")

    # Extract .deb package
    temp_extract = site_packages_dir.parent / "lldb_deb_temp"
    extract_deb_package(deb_path, temp_extract)

    # Find LLDB Python module in extracted package
    # Debian packages put it at: usr/lib/llvm-21/lib/python3.10/site-packages/lldb/
    # with symlinks at: usr/lib/python3/dist-packages/lldb -> ../../llvm-21/lib/python3/dist-packages/lldb
    lldb_source = None
    for candidate in [
        temp_extract / "usr" / "lib" / "llvm-21" / "lib" / "python3.10" / "site-packages" / "lldb",
        temp_extract / "usr" / "lib" / "llvm-21" / "lib" / "python3" / "dist-packages" / "lldb",
        temp_extract / "usr" / "lib" / "python3.10" / "dist-packages" / "lldb",
        temp_extract / "usr" / "lib" / "python3" / "dist-packages" / "lldb",
    ]:
        try:
            # On Windows, symlinks may cause permission errors
            # Try to resolve the path first
            if candidate.exists() and candidate.is_dir():
                lldb_source = candidate
                break
        except (PermissionError, OSError, NotADirectoryError) as e:
            # Windows may fail to check symlink existence
            print(f"  ⚠️  Skipping {candidate.name}: {e}")
            continue

    if not lldb_source:
        raise RuntimeError(f"LLDB Python module not found in {temp_extract}")

    print(f"  Found LLDB module at: {lldb_source}")

    # Copy LLDB module to site-packages/lldb/
    lldb_dest = site_packages_dir / "lldb"
    if lldb_dest.exists():
        shutil.rmtree(lldb_dest)

    print(f"  Copying to: {lldb_dest}")

    # On Windows, copytree may fail with symlinks - use copy_function that handles symlinks
    def copy_with_symlinks(src: str, dst: str, *, follow_symlinks: bool = False) -> str:
        """Copy function that preserves symlinks in tar archives."""
        src_path = Path(src)
        dst_path = Path(dst)

        if src_path.is_symlink():
            # Preserve symlink
            linkto = src_path.readlink()
            dst_path.symlink_to(linkto)
            return dst
        else:
            # Regular file copy
            return shutil.copy2(src, dst, follow_symlinks=follow_symlinks)

    try:
        shutil.copytree(lldb_source, lldb_dest, symlinks=True, copy_function=copy_with_symlinks)
    except (PermissionError, OSError) as e:
        # On Windows, symlink operations may fail - try without preserving symlinks
        print(f"  ⚠️  Symlink copy failed ({e}), trying without symlinks...")
        shutil.copytree(lldb_source, lldb_dest, symlinks=False)

    # Calculate size
    total_size = sum(f.stat().st_size for f in lldb_dest.rglob("*") if f.is_file())
    file_count = sum(1 for _ in lldb_dest.rglob("*") if _.is_file())
    print(f"  ✓ Copied {file_count} files ({total_size / (1024*1024):.1f} MB)")

    # Create symlink for _lldb.so
    # Format: _lldb.cpython-310-{arch}-linux-gnu.so
    arch_triple = LLDB_PYTHON_PACKAGES[arch]["arch_triple"]
    lldb_so_name = f"_lldb.cpython-310-{arch_triple}.so"
    lldb_so_path = lldb_dest / lldb_so_name

    if not lldb_so_path.exists():
        print(f"  ⚠️  Warning: {lldb_so_name} not found, looking for alternatives...")
        # List all .so files
        so_files = list(lldb_dest.glob("*.so"))
        if so_files:
            print(f"  Found .so files: {[f.name for f in so_files]}")
            # Use first .so file
            lldb_so_path = so_files[0]
            lldb_so_name = lldb_so_path.name
            print(f"  Using: {lldb_so_name}")

    if lldb_so_path.exists():
        # Check if it's already a symlink or a real file
        if lldb_so_path.is_symlink():
            print(f"  ✓ {lldb_so_name} is already a symlink")
            print(f"    Target: {lldb_so_path.readlink()}")
        else:
            print(f"  ⚠️  {lldb_so_name} is a real file, not a symlink")
            print(f"    Size: {lldb_so_path.stat().st_size / (1024*1024):.1f} MB")
            print("    Note: Debian package may include actual binary instead of symlink")
    else:
        print(f"  ⚠️  {lldb_so_name} not found in LLDB module")

    # Clean up temp directory
    shutil.rmtree(temp_extract)

    print("  ✓ LLDB Python module ready")


def create_python_environment_linux(arch: str, work_dir: Path, output_dir: Path) -> None:
    """
    Create complete Python environment for Linux LLDB.

    Args:
        arch: Architecture (x86_64 or arm64)
        work_dir: Working directory for downloads and temp files
        output_dir: Output directory for python/ structure
    """
    print("=" * 70)
    print(f"CREATING PYTHON ENVIRONMENT FOR LINUX LLDB ({arch})")
    print("=" * 70)
    print()

    work_dir.mkdir(parents=True, exist_ok=True)
    if output_dir.exists():
        print(f"Removing existing output directory: {output_dir}")
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Download Python source
    print("[1/4] Downloading Python 3.10.19 source...")
    python_tarball = work_dir / f"Python-{PYTHON_VERSION}.tar.xz"
    download_file(PYTHON_SOURCE_URL, python_tarball)

    # Step 2: Extract Python stdlib
    lib_dir = output_dir / "Lib"
    extract_python_stdlib(python_tarball, lib_dir)

    # Step 3: Download Debian LLDB package
    print("\n[3/4] Downloading Debian python3-lldb-21 package...")
    pkg_info = LLDB_PYTHON_PACKAGES.get(arch)
    if not pkg_info:
        raise ValueError(f"Unsupported architecture: {arch}")

    deb_filename = Path(pkg_info["url"]).name
    deb_path = work_dir / deb_filename
    download_file(pkg_info["url"], deb_path)

    # Step 4: Extract LLDB Python module
    site_packages = lib_dir / "site-packages"
    site_packages.mkdir(parents=True, exist_ok=True)

    # Placeholder lib dir (actual liblldb.so.21 will be in LLDB archive)
    # Symlinks will be created relative to final deployment location
    placeholder_lib = output_dir.parent / "lib"

    extract_lldb_python_module(deb_path, arch, site_packages, placeholder_lib)

    # Summary
    print("\n" + "=" * 70)
    print("PYTHON ENVIRONMENT CREATED")
    print("=" * 70)
    print(f"Location: {output_dir}")
    print()

    # Calculate total size
    total_size = sum(f.stat().st_size for f in output_dir.rglob("*") if f.is_file())
    total_files = sum(1 for _ in output_dir.rglob("*") if _.is_file())
    print(f"Total: {total_files} files, {total_size / (1024*1024):.1f} MB uncompressed")
    print(f"Expected compressed (zstd-22): ~{total_size / (1024*1024) * 0.2:.1f} MB")

    # Show directory structure
    print("\nDirectory structure:")
    for item in sorted(output_dir.rglob("*")):
        if item.is_file():
            rel_path = item.relative_to(output_dir)
            size = item.stat().st_size / (1024 * 1024)
            # Only show large files (>0.5 MB) and symlinks
            if size > 0.5 or item.is_symlink():
                symlink_info = f" → {item.readlink()}" if item.is_symlink() else ""
                print(f"  {rel_path}{symlink_info} ({size:.1f} MB)")

    print("\n✅ SUCCESS!")


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Prepare Python 3.10 environment for Linux LLDB",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--arch",
        choices=["x86_64", "arm64"],
        default="x86_64",
        help="Target architecture (default: x86_64)",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path("work/python_linux"),
        help="Working directory for downloads and temp files (default: work/python_linux)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output directory for python/ structure",
    )

    args = parser.parse_args()

    work_dir = args.work_dir.resolve()
    output_dir = args.output.resolve()

    print("Configuration:")
    print(f"  Architecture: {args.arch}")
    print(f"  Work dir:     {work_dir}")
    print(f"  Output:       {output_dir}")
    print()

    try:
        create_python_environment_linux(args.arch, work_dir, output_dir)

        print("\nNext steps:")
        print("1. Use this python/ directory with create_lldb_archives.py")
        print("2. Test LLDB with Python modules on Linux")
        print("\nExample:")
        print("  python tools/create_lldb_archives.py \\")
        print("    --platform linux \\")
        print(f"    --arch {args.arch} \\")
        print("    --with-python \\")
        print(f"    --python-dir {output_dir}")

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
