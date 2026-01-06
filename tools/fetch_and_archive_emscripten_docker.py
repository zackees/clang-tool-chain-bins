#!/usr/bin/env python3
"""
Docker-based Emscripten archive generator for clang-tool-chain.

This script uses the official emscripten/emsdk Docker image to extract
a clean Emscripten installation and package it as a .tar.zst archive.

Advantages over direct emsdk installation:
1. No host Python environment issues (works on Windows/MSYS2)
2. Reproducible across platforms
3. Faster than full emsdk download (reuses cached Docker layers)
4. Clean, tested Emscripten installation

Usage:
    python3 fetch_and_archive_emscripten_docker.py --platform linux --arch x86_64
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def check_docker_available() -> bool:
    """Check if Docker is available and running."""
    try:
        result = subprocess.run(
            ["docker", "--version"],
            capture_output=True,
            check=True,
            text=True
        )
        print(f"✓ Docker found: {result.stdout.strip()}")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("❌ Docker not found or not running")
        print("Install Docker: https://docs.docker.com/get-docker/")
        return False


def pull_docker_image(image: str = "emscripten/emsdk:latest", platform: str = None) -> bool:
    """Pull the official Emscripten Docker image."""
    print(f"\nPulling Docker image: {image}")
    if platform:
        print(f"Platform: {platform}")
    print("This may take a few minutes...")

    try:
        cmd = ["docker", "pull"]
        if platform:
            cmd.extend(["--platform", platform])
        cmd.append(image)

        subprocess.run(
            cmd,
            check=True,
            stdout=sys.stdout,
            stderr=sys.stderr
        )
        print(f"✓ Pulled {image}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to pull Docker image: {e}")
        return False


def extract_from_docker(image: str, output_dir: Path, platform: str = None) -> bool:
    """
    Extract Emscripten files from Docker container.

    The official emscripten/emsdk image has Emscripten installed at /emsdk/upstream/
    """
    print("\nExtracting Emscripten from Docker image...")

    container_name = "emsdk-temp-extract"

    try:
        # Create a temporary container
        print(f"Creating temporary container: {container_name}")
        cmd = ["docker", "create", "--name", container_name]
        if platform:
            cmd.extend(["--platform", platform])
        cmd.append(image)

        subprocess.run(
            cmd,
            check=True,
            capture_output=True
        )

        # Copy /emsdk/upstream directory from container
        upstream_dir = output_dir / "upstream"
        upstream_dir.mkdir(parents=True, exist_ok=True)

        print("Copying /emsdk/upstream from container...")
        subprocess.run(
            ["docker", "cp", f"{container_name}:/emsdk/upstream/.", str(upstream_dir)],
            check=True,
            stdout=sys.stdout,
            stderr=sys.stderr
        )

        print(f"✓ Extracted to {output_dir}")
        return True

    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to extract from Docker: {e}")
        return False

    finally:
        # Clean up temporary container
        print(f"Removing temporary container: {container_name}")
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True
        )


def get_emscripten_version(upstream_dir: Path) -> str:
    """
    Detect Emscripten version from extracted files.

    Looks for version in emscripten/emscripten-version.txt
    """
    version_file = upstream_dir / "emscripten" / "emscripten-version.txt"

    if version_file.exists():
        version = version_file.read_text().strip().strip('"')
        print(f"✓ Detected Emscripten version: {version}")
        return version

    # Fallback: try to find version from library path
    lib_dir = upstream_dir / "emscripten" / "cache" / "sysroot" / "lib" / "wasm32-emscripten"
    if lib_dir.exists():
        print("✓ Emscripten installation found (version detection uncertain)")
        return "latest"

    print("⚠️ Could not detect version, using 'latest'")
    return "latest"


def create_archive_structure(upstream_dir: Path, output_dir: Path) -> Path:
    """
    Create the final archive structure with required directories.

    The packaging script expects:
    - emsdk/upstream/emscripten/ (Python scripts)
    - emsdk/upstream/bin/ (LLVM/Clang binaries)
    - emsdk/upstream/lib/ (system libraries)
    - emsdk/upstream/share/ (additional resources)
    """
    print("\nCreating archive structure...")

    # Create emsdk/upstream/ structure for packaging script compatibility
    structure_dir = output_dir / "archive_structure" / "upstream"
    structure_dir.mkdir(parents=True, exist_ok=True)

    essential_dirs = {
        "emscripten": upstream_dir / "emscripten",
        "bin": upstream_dir / "bin",
        "lib": upstream_dir / "lib",
        "share": upstream_dir / "share",
    }

    for name, source in essential_dirs.items():
        if source.exists():
            dest = structure_dir / name
            print(f"  Copying {name}/")
            shutil.copytree(source, dest, symlinks=True)
        else:
            print(f"  ⚠️ Missing: {name}/ (expected at {source})")

    # Calculate size
    archive_root = output_dir / "archive_structure"
    total_size = sum(f.stat().st_size for f in archive_root.rglob('*') if f.is_file())
    print(f"\nTotal size: {total_size / (1024*1024):.2f} MB")

    return archive_root


def main():
    parser = argparse.ArgumentParser(
        description="Extract and package Emscripten from Docker image"
    )
    parser.add_argument(
        "--platform",
        choices=["linux", "darwin", "win"],
        default="linux",
        help="Target platform (Docker extraction always produces Linux binaries)"
    )
    parser.add_argument(
        "--arch",
        choices=["x86_64", "arm64"],
        default="x86_64",
        help="Target architecture"
    )
    parser.add_argument(
        "--image",
        default="emscripten/emsdk:latest",
        help="Docker image to use (default: emscripten/emsdk:latest)"
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        help="Working directory for extraction (default: temp directory)"
    )
    parser.add_argument(
        "--no-package",
        action="store_true",
        help="Extract only, don't create final archive"
    )

    args = parser.parse_args()

    # Check prerequisites
    if not check_docker_available():
        return 1

    # Set up work directory
    if args.work_dir:
        work_dir = args.work_dir
        work_dir.mkdir(parents=True, exist_ok=True)
    else:
        work_dir = Path(tempfile.mkdtemp(prefix="emsdk_docker_"))

    print(f"\nWorking directory: {work_dir}")

    # Determine Docker platform string
    docker_platform = f"{args.platform}/{args.arch}"
    print(f"Target platform: {docker_platform}")

    # Pull Docker image
    if not pull_docker_image(args.image, platform=docker_platform):
        return 1

    # Extract from Docker
    extract_dir = work_dir / "extracted"
    if not extract_from_docker(args.image, extract_dir, platform=docker_platform):
        return 1

    # Detect version
    upstream_dir = extract_dir / "upstream"
    version = get_emscripten_version(upstream_dir)

    # Create archive structure
    archive_structure = create_archive_structure(upstream_dir, work_dir)

    if args.no_package:
        print(f"\n✓ Extraction complete: {archive_structure}")
        print("\nTo create archive, run:")
        print("python3 fetch_and_archive_emscripten.py \\")
        print(f"  --platform {args.platform} --arch {args.arch} \\")
        print(f"  --skip-install --work-dir {archive_structure.parent}")
        return 0

    # Rename archive_structure to emsdk for packaging script compatibility
    emsdk_dir = work_dir / "emsdk"
    if emsdk_dir.exists():
        shutil.rmtree(emsdk_dir)
    archive_structure.rename(emsdk_dir)
    print(f"✓ Renamed archive structure to: {emsdk_dir}")

    # Call the main packaging script
    print("\n" + "=" * 70)
    print("CREATING COMPRESSED ARCHIVE")
    print("=" * 70)

    script_dir = Path(__file__).parent
    packaging_script = script_dir / "fetch_and_archive_emscripten.py"

    if not packaging_script.exists():
        print(f"❌ Packaging script not found: {packaging_script}")
        print(f"\nExtracted files are in: {emsdk_dir}")
        return 1

    try:
        subprocess.run(
            [
                sys.executable,
                str(packaging_script),
                "--platform", args.platform,
                "--arch", args.arch,
                "--version", version,
                "--work-dir", str(work_dir),
                "--skip-install"
            ],
            check=True,
            stdout=sys.stdout,
            stderr=sys.stderr
        )
        print("\n✓ Archive created successfully!")
        return 0

    except subprocess.CalledProcessError as e:
        print(f"\n❌ Failed to create archive: {e}")
        print(f"\nExtracted files are in: {emsdk_dir}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
