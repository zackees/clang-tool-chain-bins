#!/usr/bin/env python3
"""
Fetch and archive Emscripten SDK for clang-tool-chain.

This script downloads Emscripten via emsdk and creates a portable
.tar.zst archive for distribution.
"""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

# Emscripten version to package
EMSCRIPTEN_VERSION = "latest"  # Can be specific version like "3.1.50"
EMSDK_REPO = "https://github.com/emscripten-core/emsdk.git"


def clone_emsdk(work_dir: Path) -> Path:
    """Clone or update emsdk repository."""
    emsdk_dir = work_dir / "emsdk"

    if emsdk_dir.exists():
        print(f"emsdk already cloned at: {emsdk_dir}")
        return emsdk_dir

    print(f"Cloning emsdk from: {EMSDK_REPO}")
    print(f"To: {emsdk_dir}")

    try:
        subprocess.run(
            ["git", "clone", EMSDK_REPO, str(emsdk_dir)],
            check=True,
            capture_output=True,
            text=True
        )
        print("✓ Cloned emsdk")
    except subprocess.CalledProcessError as e:
        print(f"Error cloning emsdk: {e}")
        print(f"stdout: {e.stdout}")
        print(f"stderr: {e.stderr}")
        raise

    return emsdk_dir


def install_emscripten(emsdk_dir: Path, version: str, platform: str) -> str:
    """Install Emscripten using emsdk."""
    print(f"\nInstalling Emscripten {version}...")

    # Determine emsdk executable based on platform
    # Use python emsdk.py for cross-platform compatibility
    emsdk_py = emsdk_dir / "emsdk.py"
    # Use python emsdk.py on all platforms for consistency and reliability
    # Absolute path avoids path resolution issues
    emsdk_cmd = ["python3", str(emsdk_py.resolve())]

    try:
        # Install
        print(f"Running: {' '.join(emsdk_cmd)} install {version}")
        result = subprocess.run(
            emsdk_cmd + ["install", version],
            cwd=emsdk_dir,
            check=True,
            capture_output=True,
            text=True
        )
        print(result.stdout)

        # Activate
        print(f"Running: {' '.join(emsdk_cmd)} activate {version}")
        result = subprocess.run(
            emsdk_cmd + ["activate", version],
            cwd=emsdk_dir,
            check=True,
            capture_output=True,
            text=True
        )
        print(result.stdout)

        # Get installed version info
        result = subprocess.run(
            emsdk_cmd + ["list"],
            cwd=emsdk_dir,
            check=True,
            capture_output=True,
            text=True
        )

        # Parse installed version
        for line in result.stdout.split('\n'):
            if 'sdk-' in line and '*' in line:  # Active version
                # Extract version number from line like: "  * sdk-3.1.50-64bit  INSTALLED"
                parts = line.split()
                for part in parts:
                    if part.startswith('sdk-'):
                        installed_version = part.replace('sdk-', '').replace('-64bit', '')
                        print(f"✓ Installed Emscripten version: {installed_version}")
                        return installed_version

        # Fallback: assume version is "latest"
        print("✓ Installed Emscripten (version detection uncertain)")
        return version

    except subprocess.CalledProcessError as e:
        print(f"Error installing Emscripten: {e}")
        print(f"stdout: {e.stdout}")
        print(f"stderr: {e.stderr}")
        raise


def identify_minimal_files(emsdk_dir: Path) -> list[Path]:
    """
    Identify the minimal set of files needed for Emscripten.

    Returns list of (source_path, relative_archive_path) tuples.
    """
    print("\nIdentifying minimal required files...")

    upstream = emsdk_dir / "upstream"

    essential_dirs = [
        "upstream/emscripten",  # Emscripten Python scripts and tools
        "upstream/bin",         # LLVM/Clang binaries
        "upstream/lib",         # System libraries
        "upstream/share",       # Additional resources
    ]

    files_to_include = []

    for dir_path in essential_dirs:
        full_path = emsdk_dir / dir_path
        if full_path.exists():
            # Add entire directory
            rel_path = dir_path.replace("upstream/", "")
            files_to_include.append((full_path, rel_path))
            print(f"  + {dir_path}")

    # NOTE: .emscripten config file is NOT included in the archive
    # The installer creates its own .emscripten with correct paths via create_emscripten_config()
    # Including the emsdk .emscripten would cause path conflicts (upstream/bin vs bin)

    return files_to_include


def strip_unnecessary_files(extract_dir: Path) -> int:
    """
    Remove unnecessary files to reduce archive size.

    Returns number of bytes saved.
    """
    print("\nStripping unnecessary files...")

    initial_size = sum(f.stat().st_size for f in extract_dir.rglob('*') if f.is_file())

    # Critical files that must NOT be removed
    critical_files = {
        "emscripten-version.txt",  # Required by emscripten initialization
        "LICENSE.txt",             # License files should be preserved
        "COPYING",                 # GPL copying files
        "COPYING.txt",             # GPL copying files
    }

    patterns_to_remove = [
        "**/*.md",          # Documentation
        "**/docs/**",       # Documentation directories
        "**/tests/**",      # Test files
        "**/test/**",       # Test files
        "**/examples/**",   # Example code
        "**/.git*",         # Git metadata
        "**/__pycache__",   # Python cache
        "**/*.pyc",         # Python bytecode
    ]

    files_removed = 0
    bytes_removed = 0

    # Process general patterns first (directories and .md files)
    for pattern in patterns_to_remove:
        try:
            for path in extract_dir.glob(pattern):
                if path.is_file():
                    size = path.stat().st_size
                    path.unlink()
                    files_removed += 1
                    bytes_removed += size
                elif path.is_dir():
                    size = sum(f.stat().st_size for f in path.rglob('*') if f.is_file())
                    shutil.rmtree(path)
                    files_removed += 1
                    bytes_removed += size
        except (FileNotFoundError, OSError):
            # Skip patterns that don't match or have path issues
            continue

    # Now handle .txt files separately - remove only non-critical ones
    print("  Processing .txt files (preserving critical files)...")
    txt_files_removed = 0
    for txt_file in extract_dir.rglob("*.txt"):
        if txt_file.name not in critical_files:
            try:
                size = txt_file.stat().st_size
                txt_file.unlink()
                files_removed += 1
                txt_files_removed += 1
                bytes_removed += size
            except (FileNotFoundError, OSError):
                pass
        else:
            print(f"    Preserving: {txt_file.relative_to(extract_dir)}")

    print(f"  Removed {txt_files_removed} .txt files, preserved {len([f for f in extract_dir.rglob('*.txt')])} critical .txt files")

    final_size = sum(f.stat().st_size for f in extract_dir.rglob('*') if f.is_file())
    actual_saved = initial_size - final_size

    print(f"  Removed {files_removed} items total")
    print(f"  Saved: {actual_saved / (1024*1024):.2f} MB")

    return actual_saved


def create_archive(source_dir: Path, output_dir: Path, platform: str, arch: str, version: str) -> Path:
    """Create compressed archive of Emscripten."""
    try:
        import pyzstd
    except ImportError:
        print("Error: pyzstd module not installed")
        print("Install with: pip install pyzstd")
        sys.exit(1)

    archive_name = f"emscripten-{version}-{platform}-{arch}.tar.zst"
    archive_path = output_dir / archive_name

    print(f"\nCreating archive: {archive_path}")

    # Create tar archive in memory, then compress
    import io

    tar_buffer = io.BytesIO()

    with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
        for item in source_dir.iterdir():
            print(f"  Adding: {item.name}/")
            tar.add(item, arcname=item.name)

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


def create_manifest(output_dir: Path, version: str, platform: str, arch: str, checksums: dict[str, str]) -> None:
    """Create manifest.json for the archive."""
    manifest_path = output_dir / "manifest.json"

    archive_name = f"emscripten-{version}-{platform}-{arch}.tar.zst"

    manifest_data = {
        "latest": version,
        "versions": {
            version: {
                "version": version,
                "href": f"https://media.githubusercontent.com/media/zackees/clang-tool-chain-bins/main/assets/emscripten/{platform}/{arch}/{archive_name}",
                "sha256": checksums["sha256"],
            }
        },
    }

    with open(manifest_path, "w") as f:
        json.dump(manifest_data, f, indent=2)

    print(f"\n✓ Manifest written to: {manifest_path}")


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Fetch and archive Emscripten for clang-tool-chain")
    parser.add_argument(
        "--platform",
        required=True,
        choices=["win", "linux", "darwin"],
        help="Target platform"
    )
    parser.add_argument(
        "--arch",
        required=True,
        choices=["x86_64", "arm64"],
        help="Target architecture"
    )
    parser.add_argument(
        "--version",
        default=EMSCRIPTEN_VERSION,
        help=f"Emscripten version (default: {EMSCRIPTEN_VERSION})"
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path("work_emscripten"),
        help="Working directory for downloads and extraction"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory for final archives (default: ../assets/emscripten/{platform}/{arch})"
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="Skip emsdk installation (use existing work-dir)"
    )

    args = parser.parse_args()

    # Set default output directory
    if args.output_dir is None:
        args.output_dir = Path(__file__).parent.parent / "assets" / "emscripten" / args.platform / args.arch

    work_dir = args.work_dir
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("EMSCRIPTEN ARCHIVE CREATION")
    print("=" * 70)
    print(f"Platform: {args.platform}")
    print(f"Architecture: {args.arch}")
    print(f"Version: {args.version}")
    print(f"Work directory: {work_dir}")
    print(f"Output directory: {output_dir}")
    print()

    # Step 1: Clone emsdk
    if not args.skip_install:
        emsdk_dir = clone_emsdk(work_dir)

        # Step 2: Install Emscripten
        installed_version = install_emscripten(emsdk_dir, args.version, args.platform)
    else:
        emsdk_dir = work_dir / "emsdk"
        if not emsdk_dir.exists():
            print(f"Error: emsdk directory not found at {emsdk_dir}")
            print("Remove --skip-install flag to download")
            sys.exit(1)
        installed_version = args.version
        print(f"Using existing emsdk installation at: {emsdk_dir}")

    # Step 3: Copy minimal files to staging directory
    staging_dir = work_dir / "staging"
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nCopying files to staging directory: {staging_dir}")

    upstream_dir = emsdk_dir / "upstream"
    if not upstream_dir.exists():
        print(f"Error: upstream directory not found at {upstream_dir}")
        print("Emscripten installation may have failed")
        sys.exit(1)

    # Copy essential directories directly to staging root (NOT into upstream/)
    # This ensures archive has correct structure: bin/, emscripten/, lib/
    # Previously, files were copied to staging/upstream/ which caused archives
    # to have upstream/ prefix, breaking the installer which expects bin/ directly
    for src_name in ["emscripten", "bin", "lib"]:
        src = upstream_dir / src_name
        if src.exists():
            dst = staging_dir / src_name  # Copy directly to staging root
            print(f"  Copying {src_name} to staging root...")
            shutil.copytree(src, dst, symlinks=True)

    # Copy emscripten-version.txt to staging root (critical file)
    version_file = upstream_dir / "emscripten" / "emscripten-version.txt"
    if version_file.exists():
        dst_version = staging_dir / "emscripten-version.txt"
        print("  Copying emscripten-version.txt to root...")
        shutil.copy2(version_file, dst_version)
    else:
        print(f"  Warning: emscripten-version.txt not found at {version_file}")

    # Copy .emscripten config file to staging root
    config_file = emsdk_dir / ".emscripten"
    if config_file.exists():
        dst_config = staging_dir / ".emscripten"
        print("  Copying .emscripten config to root...")
        shutil.copy2(config_file, dst_config)
    else:
        print(f"  Warning: .emscripten config file not found at {config_file}")

    # Step 4: Strip unnecessary files
    bytes_saved = strip_unnecessary_files(staging_dir)

    # Step 5: Create archive
    final_archive = create_archive(staging_dir, output_dir, args.platform, args.arch, installed_version)

    # Step 6: Generate checksums
    checksums = generate_checksums(final_archive)

    # Step 7: Create manifest
    create_manifest(output_dir, installed_version, args.platform, args.arch, checksums)

    print("\n" + "=" * 70)
    print("COMPLETE")
    print("=" * 70)
    print(f"Archive: {final_archive}")
    print(f"Size: {final_archive.stat().st_size / (1024*1024):.2f} MB")
    print(f"SHA256: {checksums['sha256']}")
    print("\nNext steps:")
    print("1. Test archive extraction")
    print("2. Test compilation with emcc")
    print("3. Upload to clang-tool-chain-bins repository")
    print("4. Update manifest URL to GitHub raw URL")


if __name__ == "__main__":
    main()
