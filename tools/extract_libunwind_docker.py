#!/usr/bin/env python3
"""
Extract libunwind headers and libraries from Ubuntu Docker container.

This script extracts libunwind headers and shared libraries from an Ubuntu 22.04
Docker container for bundling with the Linux clang-tool-chain archives.

Usage:
    python extract_libunwind_docker.py --arch x86_64 --output-dir ./libunwind_output
    python extract_libunwind_docker.py --arch arm64 --output-dir ./libunwind_output

Requirements:
    - Docker installed and running
    - Internet connection (to pull Ubuntu image if not cached)
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def print_section(title: str) -> None:
    """Print a formatted section header."""
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def check_docker_available() -> bool:
    """Check if Docker is available and running."""
    try:
        result = subprocess.run(
            ["docker", "version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def extract_libunwind_from_docker(
    output_dir: Path,
    arch: str,
    docker_image: str = "ubuntu:22.04",
) -> bool:
    """
    Extract libunwind headers and libraries from Ubuntu Docker container.

    Args:
        output_dir: Directory to store extracted headers and libraries
        arch: Architecture ("x86_64" or "arm64")
        docker_image: Docker image to use (default: ubuntu:22.04)

    Returns:
        True if extraction was successful, False otherwise
    """
    print_section(f"EXTRACTING LIBUNWIND FROM DOCKER ({arch})")

    # Validate architecture
    if arch == "x86_64":
        lib_arch = "x86_64-linux-gnu"
        header_arch = "x86_64"
        docker_platform = "linux/amd64"
    elif arch in ("arm64", "aarch64"):
        lib_arch = "aarch64-linux-gnu"
        header_arch = "aarch64"
        docker_platform = "linux/arm64"
    else:
        print(f"ERROR: Unsupported architecture: {arch}")
        return False

    # Check Docker availability
    if not check_docker_available():
        print("ERROR: Docker is not available or not running")
        print("Please install Docker and ensure the Docker daemon is running")
        return False

    print(f"Docker image: {docker_image}")
    print(f"Platform: {docker_platform}")
    print(f"Library architecture: {lib_arch}")
    print(f"Output directory: {output_dir}")
    print()

    # Create output directories
    output_dir = Path(output_dir)
    include_dir = output_dir / "include"
    lib_dir = output_dir / "lib"

    include_dir.mkdir(parents=True, exist_ok=True)
    lib_dir.mkdir(parents=True, exist_ok=True)

    # Create extraction script to run inside container
    # This script installs libunwind-dev and copies the files
    extract_script = f"""#!/bin/bash
set -e

echo "Updating package lists..."
apt-get update -qq

echo "Installing libunwind-dev..."
apt-get install -y -qq libunwind-dev

echo "Creating output directories..."
mkdir -p /output/include /output/lib

echo "Copying headers..."
# Copy all libunwind headers
for header in libunwind.h libunwind-common.h libunwind-{header_arch}.h libunwind-dynamic.h libunwind-ptrace.h unwind.h; do
    if [ -f "/usr/include/$header" ]; then
        cp -v "/usr/include/$header" /output/include/
    else
        echo "Warning: $header not found"
    fi
done

echo "Copying libraries..."
# Copy libunwind libraries with symlinks preserved
# Use -P to preserve symlinks, -r for recursive (handles directories)
# The libraries are typically:
#   libunwind.so -> libunwind.so.8
#   libunwind.so.8 -> libunwind.so.8.0.1
#   libunwind.so.8.0.1 (actual library)
#   libunwind-{header_arch}.so -> libunwind-{header_arch}.so.8
#   etc.

# Copy all libunwind related files from the lib directory
for lib in /usr/lib/{lib_arch}/libunwind*.so*; do
    if [ -e "$lib" ]; then
        # If it's a symlink, preserve it
        if [ -L "$lib" ]; then
            cp -Pv "$lib" /output/lib/
        else
            cp -v "$lib" /output/lib/
        fi
    fi
done

echo "Setting permissions..."
chmod 644 /output/include/*
chmod 755 /output/lib/*.so* 2>/dev/null || true

echo "Listing extracted files..."
echo ""
echo "Headers:"
ls -la /output/include/
echo ""
echo "Libraries:"
ls -la /output/lib/

echo ""
echo "Extraction complete!"
"""

    # Run Docker container with the extraction script
    print("Running Docker container...")
    print("(This may take a moment if pulling the image)")
    print()

    try:
        # We mount the output directory and run the extraction script
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--platform",
                docker_platform,
                "-v",
                f"{output_dir.absolute()}:/output",
                docker_image,
                "bash",
                "-c",
                extract_script,
            ],
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
        )

        print(result.stdout)

        if result.returncode != 0:
            print("ERROR: Docker extraction failed")
            print(result.stderr)
            return False

    except subprocess.TimeoutExpired:
        print("ERROR: Docker extraction timed out")
        return False
    except FileNotFoundError:
        print("ERROR: Docker not found")
        return False
    except Exception as e:
        print(f"ERROR: Unexpected error during Docker extraction: {e}")
        return False

    # Verify extraction results
    print()
    print("Verifying extraction results...")

    # Check for critical headers
    required_headers = ["libunwind.h", f"libunwind-{header_arch}.h"]
    missing_headers = []
    for header in required_headers:
        header_path = include_dir / header
        if not header_path.exists():
            missing_headers.append(header)

    if missing_headers:
        print(f"WARNING: Missing headers: {', '.join(missing_headers)}")

    # Check for libraries
    lib_files = list(lib_dir.glob("libunwind*.so*"))
    if not lib_files:
        print("ERROR: No libunwind libraries were extracted")
        return False

    print(f"Extracted {len(list(include_dir.iterdir()))} header files")
    print(f"Extracted {len(lib_files)} library files")

    # Calculate total size
    total_size = sum(f.stat().st_size for f in include_dir.iterdir() if f.is_file())
    total_size += sum(f.stat().st_size for f in lib_dir.iterdir() if f.is_file())
    print(f"Total size: {total_size / 1024:.1f} KB")

    print()
    print("✓ Libunwind extraction complete!")

    return True


def main() -> None:
    """Main entry point for command-line usage."""
    parser = argparse.ArgumentParser(
        description="Extract libunwind from Ubuntu Docker container",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python extract_libunwind_docker.py --arch x86_64 --output-dir ./libunwind_output
  python extract_libunwind_docker.py --arch arm64 --output-dir ./libunwind_output

This script requires Docker to be installed and running.
        """,
    )

    parser.add_argument(
        "--arch",
        required=True,
        choices=["x86_64", "arm64", "aarch64"],
        help="Target architecture",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("libunwind_output"),
        help="Output directory for extracted files (default: libunwind_output)",
    )
    parser.add_argument(
        "--docker-image",
        default="ubuntu:22.04",
        help="Docker image to use (default: ubuntu:22.04)",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Clean output directory before extraction",
    )

    args = parser.parse_args()

    # Clean output directory if requested
    if args.clean and args.output_dir.exists():
        print(f"Cleaning output directory: {args.output_dir}")
        shutil.rmtree(args.output_dir)

    # Run extraction
    success = extract_libunwind_from_docker(
        output_dir=args.output_dir,
        arch=args.arch,
        docker_image=args.docker_image,
    )

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
