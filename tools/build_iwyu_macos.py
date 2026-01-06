#!/usr/bin/env python3
"""
Build IWYU from source for macOS (both x86_64 and ARM64).

This script:
1. Downloads IWYU source code (version matched to LLVM)
2. Builds IWYU using the local LLVM installation
3. Creates archives using create_iwyu_archives.py
4. Generates manifests

LLVM Version Requirements:
- macOS x86_64: LLVM 19.1.7 -> IWYU 0.22
- macOS ARM64: LLVM 21.1.6 -> IWYU 0.25
"""

import platform
import shutil
import subprocess
import sys
from pathlib import Path

# IWYU version mapping based on LLVM versions
IWYU_VERSION_MAP = {
    "19.1.7": "0.22",  # macOS x86_64 (legacy)
    "21.1.6": "0.25",  # macOS x86_64 and ARM64 (current)
    "21.1.5": "0.25",  # Linux/Windows (not used here but for reference)
}

# LLVM versions by architecture
LLVM_VERSIONS = {
    "x86_64": "21.1.6",  # Upgraded from 19.1.7 for IWYU compatibility
    "arm64": "21.1.6",
}


def get_current_arch():
    """Get current macOS architecture."""
    machine = platform.machine()
    if machine == "x86_64":
        return "x86_64"
    elif machine in ("arm64", "aarch64"):
        return "arm64"
    else:
        raise RuntimeError(f"Unsupported architecture: {machine}")


def download_iwyu_source(version: str, work_dir: Path) -> Path:
    """Download IWYU source code."""
    print(f"\n{'='*70}")
    print(f"DOWNLOADING IWYU {version} SOURCE")
    print(f"{'='*70}\n")

    url = f"https://github.com/include-what-you-use/include-what-you-use/archive/refs/tags/{version}.tar.gz"
    tarball = work_dir / f"iwyu-{version}.tar.gz"

    print(f"URL: {url}")
    print(f"Output: {tarball}")

    subprocess.run(["curl", "-L", url, "-o", str(tarball)], check=True)

    print(f"✓ Downloaded {tarball.stat().st_size / (1024*1024):.2f} MB")

    return tarball


def extract_source(tarball: Path, work_dir: Path) -> Path:
    """Extract IWYU source tarball."""
    print(f"\n{'='*70}")
    print("EXTRACTING SOURCE")
    print(f"{'='*70}\n")

    subprocess.run(["tar", "-xzf", str(tarball), "-C", str(work_dir)], check=True)

    # Find extracted directory
    version = tarball.stem.replace("iwyu-", "").replace(".tar", "")
    source_dir = work_dir / f"include-what-you-use-{version}"

    if not source_dir.exists():
        raise RuntimeError(f"Source directory not found: {source_dir}")

    print(f"✓ Extracted to {source_dir}")

    return source_dir


def build_iwyu(source_dir: Path, llvm_path: Path, arch: str, static_linking: bool = True) -> Path:
    """Build IWYU with CMake.

    Args:
        source_dir: IWYU source directory
        llvm_path: Path to LLVM installation (not used, kept for compatibility)
        arch: Target architecture (x86_64 or arm64)
        static_linking: If True, link LLVM libraries statically (default: True)
    """
    print(f"\n{'='*70}")
    print(f"BUILDING IWYU FOR {arch} ({'STATIC' if static_linking else 'DYNAMIC'} linking)")
    print(f"{'='*70}\n")

    build_dir = source_dir / "build"
    build_dir.mkdir(exist_ok=True)

    # Determine LLVM version for this architecture
    llvm_version = LLVM_VERSIONS[arch]

    # Install LLVM via Homebrew for CMake config files
    print(f"Installing LLVM {llvm_version} via Homebrew (for CMake configs)...")
    # Use LLVM 21 for both x86_64 and ARM64 (LLVM current stable)
    llvm_formula = "llvm"
    subprocess.run(["brew", "install", llvm_formula], check=True)

    # Find Homebrew LLVM path
    result = subprocess.run(
        ["brew", "--prefix", llvm_formula],
        capture_output=True,
        text=True,
        check=True
    )
    homebrew_llvm_path = result.stdout.strip()

    print(f"Homebrew LLVM Path: {homebrew_llvm_path}")
    print(f"Build Dir: {build_dir}")

    # Check for static libraries
    if static_linking:
        print("\nChecking for LLVM static libraries...")
        lib_dir = Path(homebrew_llvm_path) / "lib"
        static_libs = list(lib_dir.glob("*.a"))
        if static_libs:
            print(f"✓ Found {len(static_libs)} static library files (.a)")
            print(f"  Example: {static_libs[0].name}")
        else:
            print("⚠️  WARNING: No static libraries (.a) found!")
            print("   Static linking may not work. Consider building LLVM from source.")

    # CMake configuration using Homebrew LLVM
    cmake_cmd = [
        "cmake",
        "-G", "Unix Makefiles",
        f"-DCMAKE_PREFIX_PATH={homebrew_llvm_path}",
        "-DCMAKE_BUILD_TYPE=Release",
    ]

    # Add static linking flags
    if static_linking:
        print("\n🔗 Configuring for STATIC linking...")
        cmake_cmd.extend([
            # Prefer static libraries over dynamic
            "-DCMAKE_FIND_LIBRARY_SUFFIXES=.a;.dylib",
            # Don't link against monolithic libLLVM.dylib - use component libs
            "-DLLVM_LINK_LLVM_DYLIB=OFF",
            # Don't build shared libraries
            "-DBUILD_SHARED_LIBS=OFF",
        ])

    cmake_cmd.append("..")

    print("\nCMake command:")
    print(" ".join(cmake_cmd))
    subprocess.run(cmake_cmd, cwd=build_dir, check=True)

    # Build
    import os
    cpu_count = os.cpu_count() or 4
    make_cmd = ["make", f"-j{cpu_count}"]

    print(f"\n{' '.join(make_cmd)}")
    subprocess.run(make_cmd, cwd=build_dir, check=True)

    # Verify linking and strip if static
    binary_path = build_dir / "bin" / "include-what-you-use"
    if binary_path.exists():
        print("\n" + "="*70)
        print("VERIFYING BINARY")
        print("="*70)

        # Check dependencies
        print("\nChecking dynamic library dependencies...")
        result = subprocess.run(
            ["otool", "-L", str(binary_path)],
            capture_output=True,
            text=True,
            check=True
        )
        print(result.stdout)

        # Check for LLVM dependencies
        if "LLVM" in result.stdout or "clang" in result.stdout.lower():
            print("\n⚠️  WARNING: Binary has LLVM/Clang dynamic dependencies!")
            print("   Static linking may have failed.")
            if static_linking:
                print("   This could cause runtime errors on systems without LLVM installed.")
        else:
            print("\n✓ No LLVM dependencies found - binary is self-contained!")

        # Strip debug symbols to reduce size
        if static_linking:
            print("\nStripping debug symbols to reduce binary size...")
            original_size = binary_path.stat().st_size
            subprocess.run(["strip", "-S", str(binary_path)], check=True)
            new_size = binary_path.stat().st_size
            savings = (1 - new_size/original_size) * 100
            print(f"✓ Stripped: {original_size/(1024*1024):.1f} MB -> {new_size/(1024*1024):.1f} MB ({savings:.1f}% reduction)")

    print("\n✓ Build completed successfully")

    return build_dir


def install_iwyu(build_dir: Path, output_dir: Path) -> None:
    """Install IWYU to output directory."""
    print(f"\n{'='*70}")
    print("INSTALLING IWYU")
    print(f"{'='*70}\n")

    output_dir.mkdir(parents=True, exist_ok=True)

    # Create bin/ and share/ directories
    bin_dir = output_dir / "bin"
    share_dir = output_dir / "share" / "include-what-you-use"

    bin_dir.mkdir(parents=True, exist_ok=True)
    share_dir.mkdir(parents=True, exist_ok=True)

    # Copy binary
    binary_src = build_dir / "bin" / "include-what-you-use"
    if not binary_src.exists():
        raise RuntimeError(f"Binary not found: {binary_src}")

    shutil.copy2(binary_src, bin_dir / "include-what-you-use")
    print(f"✓ Copied {binary_src} -> {bin_dir}")

    # Copy iwyu_tool.py if it exists
    iwyu_tool = build_dir.parent / "iwyu_tool.py"
    if iwyu_tool.exists():
        shutil.copy2(iwyu_tool, bin_dir / "iwyu_tool.py")
        print(f"✓ Copied {iwyu_tool} -> {bin_dir}")

    # Copy mapping files
    mappings_src = build_dir.parent
    for mapping_file in mappings_src.glob("*.imp"):
        shutil.copy2(mapping_file, share_dir)
        print(f"✓ Copied {mapping_file.name} -> {share_dir}")

    print(f"\n✓ IWYU installed to {output_dir}")


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Build IWYU from source for macOS")
    parser.add_argument("--arch", choices=["x86_64", "arm64"],
                       help="Target architecture (default: current)")
    parser.add_argument("--work-dir", type=Path, default=Path("work_iwyu"),
                       help="Working directory for build")
    parser.add_argument("--output-dir", type=Path,
                       default=Path("downloads-bins/assets/iwyu/darwin"),
                       help="Output directory for binaries")
    parser.add_argument("--llvm-path", type=Path,
                       help="Path to LLVM installation (default: ~/.clang-tool-chain/clang/darwin/<arch>)")
    parser.add_argument("--static", action="store_true", default=True,
                       help="Use static linking (default: True, recommended)")
    parser.add_argument("--dynamic", action="store_true",
                       help="Use dynamic linking (not recommended, for debugging)")

    args = parser.parse_args()

    # Determine linking mode
    static_linking = args.static and not args.dynamic

    # Determine architecture
    current_arch = get_current_arch()
    target_arch = args.arch or current_arch

    if target_arch != current_arch:
        print("WARNING: Cross-compilation not supported yet!")
        print(f"Current: {current_arch}, Target: {target_arch}")
        sys.exit(1)

    print(f"\n{'='*70}")
    print(f"IWYU BUILD SCRIPT FOR macOS {target_arch}")
    print(f"{'='*70}\n")

    # Get LLVM version for this arch
    llvm_version = LLVM_VERSIONS[target_arch]
    iwyu_version = IWYU_VERSION_MAP[llvm_version]

    print(f"LLVM Version: {llvm_version}")
    print(f"IWYU Version: {iwyu_version}")
    print(f"Architecture: {target_arch}")
    print(f"Linking Mode: {'STATIC' if static_linking else 'DYNAMIC'}")

    # Determine LLVM path (not used anymore - Homebrew LLVM will be installed during build)
    if args.llvm_path:
        llvm_path = args.llvm_path
    else:
        llvm_path = Path.home() / ".clang-tool-chain" / "clang" / "darwin" / target_arch

    # Note: LLVM path check removed - Homebrew LLVM will be installed during build_iwyu()

    # Create work directory
    work_dir = args.work_dir
    work_dir.mkdir(parents=True, exist_ok=True)

    # Build pipeline
    try:
        # Step 1: Download source
        tarball = download_iwyu_source(iwyu_version, work_dir)

        # Step 2: Extract source
        source_dir = extract_source(tarball, work_dir)

        # Step 3: Build
        build_dir = build_iwyu(source_dir, llvm_path, target_arch, static_linking)

        # Step 4: Install to output directory
        output_dir = args.output_dir / target_arch
        install_iwyu(build_dir, output_dir)

        print(f"\n{'='*70}")
        print("SUCCESS!")
        print(f"{'='*70}\n")
        print(f"IWYU {iwyu_version} built for macOS {target_arch}")
        print(f"Binaries: {output_dir}")
        print("\nNext steps:")
        print("1. Run create_iwyu_archives.py to compress binaries")
        print("2. Upload archives to downloads-bins repository")
        print("3. Update manifest.json")

    except subprocess.CalledProcessError as e:
        print(f"\n❌ Build failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
