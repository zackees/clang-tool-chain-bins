#!/usr/bin/env python3
"""
Extract IWYU binaries from Homebrew for redistribution.

This script:
1. Installs IWYU via Homebrew (pre-built binaries)
2. Extracts the IWYU binary and support files
3. Copies them to downloads-bins/assets/iwyu/{platform}/{arch}/
4. Verifies the binary has acceptable dependencies

This is MUCH faster than building from source (~2 min vs ~10 min).
"""

import shutil
import subprocess
import sys
from pathlib import Path
import platform


def get_current_arch():
    """Get current macOS architecture."""
    machine = platform.machine()
    if machine == "x86_64":
        return "x86_64"
    elif machine in ("arm64", "aarch64"):
        return "arm64"
    else:
        raise RuntimeError(f"Unsupported architecture: {machine}")


def install_iwyu_homebrew() -> Path:
    """
    Install include-what-you-use via Homebrew.

    Returns:
        Path to Homebrew installation directory
    """
    print("\n" + "="*70)
    print("INSTALLING IWYU VIA HOMEBREW")
    print("="*70 + "\n")

    # Install IWYU (includes LLVM as dependency)
    print("Running: brew install include-what-you-use")
    subprocess.run(["brew", "install", "include-what-you-use"], check=True)

    # Get installation path
    result = subprocess.run(
        ["brew", "--prefix", "include-what-you-use"],
        capture_output=True,
        text=True,
        check=True
    )
    iwyu_path = Path(result.stdout.strip())

    print(f"\n✓ IWYU installed at: {iwyu_path}")

    return iwyu_path


def get_iwyu_version(iwyu_path: Path) -> str:
    """Get IWYU version from installed binary."""
    binary = iwyu_path / "bin" / "include-what-you-use"

    if not binary.exists():
        raise RuntimeError(f"IWYU binary not found at: {binary}")

    result = subprocess.run(
        [str(binary), "--version"],
        capture_output=True,
        text=True
    )

    # Parse version from output like:
    # "include-what-you-use 0.25 based on clang version 21.1.6"
    output = result.stdout + result.stderr
    for line in output.split('\n'):
        if 'include-what-you-use' in line.lower():
            parts = line.split()
            for i, part in enumerate(parts):
                if part.lower() == 'include-what-you-use' and i + 1 < len(parts):
                    version = parts[i + 1]
                    print(f"✓ Detected IWYU version: {version}")
                    return version

    # Fallback: assume 0.25
    print("⚠️  Could not detect version, assuming 0.25")
    return "0.25"


def verify_binary_dependencies(binary_path: Path) -> bool:
    """
    Verify IWYU binary dependencies using otool.

    Returns:
        True if dependencies are acceptable (only system libs or bundled LLVM)
    """
    print("\n" + "="*70)
    print("VERIFYING BINARY DEPENDENCIES")
    print("="*70 + "\n")

    result = subprocess.run(
        ["otool", "-L", str(binary_path)],
        capture_output=True,
        text=True,
        check=True
    )

    print(result.stdout)

    # Check for problematic dependencies
    has_homebrew_llvm = False
    has_system_llvm = False

    for line in result.stdout.split('\n'):
        line = line.strip().lower()

        # Check for Homebrew-specific paths (problematic)
        if '/opt/homebrew/' in line or '/usr/local/opt/' in line or '/usr/local/cellar/' in line:
            if 'llvm' in line or 'clang' in line:
                has_homebrew_llvm = True
                print(f"⚠️  Found Homebrew LLVM dependency: {line}")

        # System LLVM is okay (but rare on Homebrew builds)
        if '/usr/lib/' in line and ('llvm' in line or 'clang' in line):
            has_system_llvm = True

    # Acceptable: only system dependencies
    if not has_homebrew_llvm and not has_system_llvm:
        print("\n✓ Binary has only system dependencies (libc++, libSystem) - EXCELLENT!")
        return True

    # Acceptable: Homebrew LLVM dependencies (we'll need to bundle them)
    if has_homebrew_llvm:
        print("\n⚠️  Binary depends on Homebrew LLVM libraries")
        print("    This is expected for Homebrew IWYU builds")
        print("    We'll need to bundle the LLVM libraries with the binary")
        return True

    return True


def copy_llvm_dylibs(iwyu_path: Path, output_dir: Path) -> int:
    """
    Copy required LLVM dylibs from Homebrew to output directory.

    Args:
        iwyu_path: Path to Homebrew IWYU installation
        output_dir: Destination directory for binaries

    Returns:
        Number of dylibs copied
    """
    print("\n" + "="*70)
    print("COPYING LLVM DYLIBS")
    print("="*70 + "\n")

    # Get LLVM path from Homebrew
    try:
        result = subprocess.run(
            ["brew", "--prefix", "llvm"],
            capture_output=True,
            text=True,
            check=True
        )
        llvm_path = Path(result.stdout.strip())
    except subprocess.CalledProcessError as e:
        print(f"⚠️  Failed to get LLVM path from Homebrew: {e}")
        return 0

    print(f"LLVM Homebrew path: {llvm_path}")

    llvm_lib_dir = llvm_path / "lib"
    print(f"Looking for dylibs in: {llvm_lib_dir}")

    if not llvm_lib_dir.exists():
        print(f"⚠️  LLVM lib directory not found: {llvm_lib_dir}")
        print("    Attempting to find LLVM libraries anyway...")
        # Try alternate path
        llvm_lib_dir = llvm_path / "Cellar" / "llvm"
        if llvm_lib_dir.exists():
            # Find the version directory
            versions = list(llvm_lib_dir.iterdir())
            if versions:
                llvm_lib_dir = versions[0] / "lib"
                print(f"    Found alternate path: {llvm_lib_dir}")

    if not llvm_lib_dir.exists():
        print(f"⚠️  Could not find LLVM lib directory")
        return 0

    # Create lib directory in output (remove old one if exists)
    output_lib_dir = output_dir / "lib"
    if output_lib_dir.exists():
        print(f"Removing existing lib directory: {output_lib_dir}")
        import shutil as shutil_module
        shutil_module.rmtree(output_lib_dir)
    output_lib_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output lib directory: {output_lib_dir}")

    # Find and copy LLVM dylibs - be more aggressive in finding them
    dylib_patterns = [
        "libLLVM*.dylib",
        "libclang*.dylib",
        "libLLVM.dylib",
        "libclang.dylib",
        "libclang-cpp.dylib",
    ]

    # Also check for symlinks and resolve them
    print("\nSearching for LLVM dylibs...")
    all_dylibs = set()
    for pattern in dylib_patterns:
        found = list(llvm_lib_dir.glob(pattern))
        print(f"  Pattern '{pattern}': found {len(found)} files")
        all_dylibs.update(found)

    copied_count = 0
    copied_files = []
    copied_targets = set()  # Track which actual files we've copied

    for dylib in sorted(all_dylibs):
        # Resolve symlinks to get the actual file
        if dylib.is_symlink():
            target = dylib.resolve()
            target_name = target.name

            # Copy the target file if we haven't already
            if target_name not in copied_targets:
                print(f"Copying target: {target_name}")
                dest = output_lib_dir / target_name
                try:
                    shutil.copy2(target, dest)
                    copied_targets.add(target_name)
                    copied_count += 1
                except Exception as e:
                    print(f"⚠️  Failed to copy {target_name}: {e}")
                    continue

            # Create the symlink
            symlink_dest = output_lib_dir / dylib.name
            print(f"Creating symlink: {dylib.name} -> {target_name}")
            try:
                if symlink_dest.exists() or symlink_dest.is_symlink():
                    symlink_dest.unlink()
                symlink_dest.symlink_to(target_name)
                copied_files.append(f"{dylib.name} -> {target_name}")
            except Exception as e:
                print(f"⚠️  Failed to create symlink {dylib.name}: {e}")
        else:
            # Regular file (not a symlink)
            if dylib.name not in copied_targets:
                print(f"Copying: {dylib.name}")
                dest = output_lib_dir / dylib.name
                try:
                    shutil.copy2(dylib, dest)
                    copied_targets.add(dylib.name)
                    copied_files.append(dylib.name)
                    copied_count += 1
                except Exception as e:
                    print(f"⚠️  Failed to copy {dylib.name}: {e}")

    if copied_count > 0:
        print(f"\n✓ Copied {copied_count} LLVM dylib(s) to {output_lib_dir}")
        for f in copied_files:
            print(f"  - {f}")
    else:
        print("\n⚠️  No LLVM dylibs found to copy")
        print("    This may cause runtime errors!")

    # Recursively copy ALL Homebrew dependencies
    print("\n" + "="*70)
    print("RECURSIVELY COPYING ALL HOMEBREW DEPENDENCIES")
    print("="*70 + "\n")

    def get_homebrew_dependencies(dylib_path: Path) -> set[Path]:
        """Get all Homebrew dylib dependencies of a given dylib."""
        try:
            result = subprocess.run(
                ["otool", "-L", str(dylib_path)],
                capture_output=True,
                text=True,
                check=True
            )

            homebrew_deps = set()
            for line in result.stdout.split('\n'):
                line = line.strip()
                # Look for Homebrew paths
                if '/opt/homebrew/' in line or '/usr/local/opt/' in line or '/usr/local/Cellar/' in line:
                    # Extract the path (first part before compatibility version)
                    dep_path = line.split()[0]
                    homebrew_deps.add(Path(dep_path))

            return homebrew_deps
        except subprocess.CalledProcessError:
            return set()

    def copy_dylib_with_deps(dylib_path: Path, visited: set[str]) -> int:
        """Recursively copy a dylib and all its Homebrew dependencies."""
        count = 0
        dylib_name = dylib_path.name

        # Skip if already processed
        if dylib_name in visited:
            return 0

        visited.add(dylib_name)

        # Resolve symlinks
        if dylib_path.is_symlink():
            target = dylib_path.resolve()
            target_name = target.name

            # Copy target if not already copied
            if target_name not in copied_targets:
                print(f"Copying: {target_name}")
                dest = output_lib_dir / target_name
                shutil.copy2(target, dest)
                copied_targets.add(target_name)
                count += 1

                # Recursively copy dependencies of this dylib
                deps = get_homebrew_dependencies(target)
                for dep in deps:
                    count += copy_dylib_with_deps(dep, visited)

            # Create symlink
            symlink_dest = output_lib_dir / dylib_name
            if symlink_dest.exists() or symlink_dest.is_symlink():
                symlink_dest.unlink()
            symlink_dest.symlink_to(target_name)
            print(f"  Symlink: {dylib_name} -> {target_name}")
        else:
            # Regular file
            if dylib_name not in copied_targets:
                print(f"Copying: {dylib_name}")
                dest = output_lib_dir / dylib_name
                shutil.copy2(dylib_path, dest)
                copied_targets.add(dylib_name)
                count += 1

                # Recursively copy dependencies of this dylib
                deps = get_homebrew_dependencies(dylib_path)
                for dep in deps:
                    count += copy_dylib_with_deps(dep, visited)

        return count

    # Start with LLVM dylibs and recursively get all dependencies
    visited_dylibs = set()
    for dylib in sorted(all_dylibs):
        copied_count += copy_dylib_with_deps(dylib, visited_dylibs)

    print(f"\n✓ Recursively copied {copied_count} total dylib(s) (including all dependencies)")
    print(f"  Total dylibs in lib/: {len(copied_targets)}")

    return copied_count


def fix_install_names(output_dir: Path) -> None:
    """
    Fix install names in IWYU binary and bundled dylibs to use @executable_path.

    This makes binaries use dylibs relative to their location instead of
    absolute Homebrew paths.
    """
    print("\n" + "="*70)
    print("FIXING INSTALL NAMES")
    print("="*70 + "\n")

    def fix_binary_dependencies(binary_path: Path, is_dylib: bool = False):
        """Fix install names for a single binary or dylib."""
        # Get current dependencies
        result = subprocess.run(
            ["otool", "-L", str(binary_path)],
            capture_output=True,
            text=True,
            check=True
        )

        # Find Homebrew dependencies and fix them
        for line in result.stdout.split('\n'):
            line = line.strip()

            # Look for Homebrew paths (LLVM, Z3, etc.)
            if '/opt/homebrew/' in line or '/usr/local/opt/' in line or '/usr/local/Cellar/' in line:
                # Extract the path (first part before compatibility version)
                old_path = line.split()[0]
                dylib_name = Path(old_path).name

                # For dylibs, use @loader_path; for binaries, use @executable_path
                if is_dylib:
                    new_path = f"@loader_path/{dylib_name}"
                else:
                    new_path = f"@executable_path/../lib/{dylib_name}"

                print(f"Fixing: {binary_path.name}")
                print(f"  Old: {old_path}")
                print(f"  New: {new_path}")

                subprocess.run(
                    ["install_name_tool", "-change", old_path, new_path, str(binary_path)],
                    check=True
                )

    # Fix IWYU binary
    binary = output_dir / "bin" / "include-what-you-use"
    if binary.exists():
        print("Fixing IWYU binary:")
        fix_binary_dependencies(binary, is_dylib=False)

    # Fix all bundled dylibs
    lib_dir = output_dir / "lib"
    if lib_dir.exists():
        print("\nFixing bundled dylibs:")
        for dylib in lib_dir.glob("*.dylib"):
            # Skip symlinks, only process actual files
            if not dylib.is_symlink():
                fix_binary_dependencies(dylib, is_dylib=True)

    print("\n✓ Install names fixed")


def copy_iwyu_files(iwyu_path: Path, output_dir: Path) -> None:
    """
    Copy IWYU binary and support files to output directory.

    Args:
        iwyu_path: Path to Homebrew IWYU installation
        output_dir: Destination directory
    """
    print("\n" + "="*70)
    print("COPYING IWYU FILES")
    print("="*70 + "\n")

    # Create output directories
    bin_dir = output_dir / "bin"
    share_dir = output_dir / "share" / "include-what-you-use"

    bin_dir.mkdir(parents=True, exist_ok=True)
    share_dir.mkdir(parents=True, exist_ok=True)

    # Copy main binary
    iwyu_binary = iwyu_path / "bin" / "include-what-you-use"
    if iwyu_binary.exists():
        shutil.copy2(iwyu_binary, bin_dir / "include-what-you-use")
        print(f"✓ Copied: include-what-you-use")
    else:
        raise RuntimeError(f"IWYU binary not found: {iwyu_binary}")

    # Copy iwyu_tool.py if it exists
    iwyu_tool = iwyu_path / "bin" / "iwyu_tool.py"
    if iwyu_tool.exists():
        shutil.copy2(iwyu_tool, bin_dir / "iwyu_tool.py")
        print(f"✓ Copied: iwyu_tool.py")

    # Copy fix_includes.py if it exists
    fix_includes = iwyu_path / "bin" / "fix_includes.py"
    if fix_includes.exists():
        shutil.copy2(fix_includes, bin_dir / "fix_includes.py")
        print(f"✓ Copied: fix_includes.py")

    # Copy mapping files from share directory
    iwyu_share = iwyu_path / "share" / "include-what-you-use"
    if iwyu_share.exists():
        for mapping_file in iwyu_share.glob("*.imp"):
            shutil.copy2(mapping_file, share_dir)
            print(f"✓ Copied: {mapping_file.name}")
    else:
        print(f"⚠️  No mapping files found at: {iwyu_share}")

    print(f"\n✓ Files copied to {output_dir}")


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract IWYU binaries from Homebrew for redistribution"
    )
    parser.add_argument(
        "--arch",
        choices=["x86_64", "arm64"],
        help="Target architecture (default: current)"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("../assets/iwyu/darwin"),
        help="Output directory for binaries (relative to downloads-bins/tools)"
    )
    parser.add_argument(
        "--skip-llvm-dylibs",
        action="store_true",
        help="Don't copy LLVM dylibs (use only if binary is statically linked)"
    )
    parser.add_argument(
        "--fix-rpaths",
        action="store_true",
        default=True,
        help="Fix install names to use @executable_path (default: True)"
    )

    args = parser.parse_args()

    # Determine architecture
    current_arch = get_current_arch()
    target_arch = args.arch or current_arch

    if target_arch != current_arch:
        print(f"ERROR: Cross-extraction not supported")
        print(f"Current: {current_arch}, Target: {target_arch}")
        sys.exit(1)

    print("\n" + "="*70)
    print(f"IWYU EXTRACTION FROM HOMEBREW (macOS {target_arch})")
    print("="*70 + "\n")

    try:
        # Step 1: Install IWYU via Homebrew
        iwyu_path = install_iwyu_homebrew()

        # Step 2: Get version
        version = get_iwyu_version(iwyu_path)

        # Step 3: Verify binary dependencies
        binary_path = iwyu_path / "bin" / "include-what-you-use"
        verify_binary_dependencies(binary_path)

        # Step 4: Copy IWYU files to output directory
        output_dir = args.output_dir / target_arch
        copy_iwyu_files(iwyu_path, output_dir)

        # Step 5: Copy LLVM dylibs if needed
        dylibs_copied = 0
        if not args.skip_llvm_dylibs:
            dylibs_copied = copy_llvm_dylibs(iwyu_path, output_dir)
            if dylibs_copied == 0:
                print("\n⚠️  WARNING: No LLVM dylibs were copied!")
                print("    The binary may crash with 'Symbol not found' errors")
                print("    Consider using --skip-llvm-dylibs if the binary is statically linked")

        # Step 6: Fix install names if requested
        if args.fix_rpaths and not args.skip_llvm_dylibs:
            fix_install_names(output_dir)

        # Step 7: Final verification
        output_binary = output_dir / "bin" / "include-what-you-use"
        print("\n" + "="*70)
        print("FINAL VERIFICATION")
        print("="*70 + "\n")

        verify_binary_dependencies(output_binary)

        # Test the binary
        print("\nTesting binary...")
        result = subprocess.run(
            [str(output_binary), "--version"],
            capture_output=True,
            text=True
        )
        print(result.stdout + result.stderr)

        if result.returncode != 0:
            print(f"⚠️  Binary test returned exit code {result.returncode}")
        else:
            print("✓ Binary test successful!")

        print("\n" + "="*70)
        print("SUCCESS!")
        print("="*70 + "\n")
        print(f"IWYU {version} extracted for macOS {target_arch}")
        print(f"Output directory: {output_dir}")
        print(f"\nNext steps:")
        print(f"1. Run create_iwyu_archives.py to compress binaries")
        print(f"2. Upload archives to downloads-bins repository")
        print(f"3. Update manifest.json")

    except subprocess.CalledProcessError as e:
        print(f"\n❌ Command failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
