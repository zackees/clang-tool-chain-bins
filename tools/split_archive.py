#!/usr/bin/env python3
"""
Split large archives into parts for GitHub storage.

GitHub has a 100 MB file size limit. This tool splits large .tar.zst archives
into parts (<100 MB each) and generates updated manifest with checksums.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path


def calculate_sha256(file_path: Path) -> str:
    """Calculate SHA256 checksum of a file.

    Args:
        file_path: Path to file

    Returns:
        SHA256 checksum as hex string
    """
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096 * 1024), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def split_archive(
    archive_path: Path, part_size_mb: int = 95, output_dir: Path | None = None
) -> list[tuple[Path, str]]:
    """Split a large archive into parts.

    Args:
        archive_path: Path to .tar.zst archive
        part_size_mb: Size of each part in MB (default: 95 MB)
        output_dir: Directory to write parts to (default: same as archive)

    Returns:
        List of (part_path, sha256) tuples
    """
    if not archive_path.exists():
        raise FileNotFoundError(f"Archive not found: {archive_path}")

    if output_dir is None:
        output_dir = archive_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    part_size_bytes = part_size_mb * 1024 * 1024
    parts = []

    print(f"Splitting {archive_path.name}...")
    print(f"Part size: {part_size_mb} MB ({part_size_bytes:,} bytes)")

    with open(archive_path, "rb") as f:
        part_num = 1
        total_bytes = 0

        while True:
            chunk = f.read(part_size_bytes)
            if not chunk:
                break

            part_name = f"{archive_path.name}.part{part_num}"
            part_path = output_dir / part_name

            with open(part_path, "wb") as part_file:
                part_file.write(chunk)

            # Calculate SHA256 for this part
            sha256 = hashlib.sha256(chunk).hexdigest()
            chunk_size = len(chunk)
            total_bytes += chunk_size

            print(f"  Part {part_num}: {part_name} ({chunk_size:,} bytes, SHA256: {sha256[:16]}...)")

            parts.append((part_path, sha256))
            part_num += 1

    print(f"Created {len(parts)} parts ({total_bytes:,} bytes total)")
    return parts


def update_manifest_with_parts(
    manifest_path: Path,
    archive_name: str,
    parts: list[tuple[Path, str]],
    base_url: str,
) -> None:
    """Update manifest.json with multi-part archive information.

    Args:
        manifest_path: Path to manifest.json
        archive_name: Base name of archive (without .part* suffix)
        parts: List of (part_path, sha256) tuples
        base_url: Base URL for downloading parts
    """
    if not manifest_path.exists():
        print(f"Warning: Manifest not found at {manifest_path}")
        print("You'll need to manually update the manifest with part information.")
        return

    with open(manifest_path) as f:
        manifest = json.load(f)

    # Calculate full archive checksum
    print("Calculating full archive checksum...")
    full_archive_sha256 = calculate_sha256(parts[0][0].parent / archive_name)
    print(f"Full archive SHA256: {full_archive_sha256}")

    # Find the version entry for this archive
    version_key = None
    for key, value in manifest.items():
        if key == "latest":
            continue
        if isinstance(value, dict) and "href" in value:
            if archive_name in value["href"]:
                version_key = key
                break

    # Also check nested versions structure
    if version_key is None and "versions" in manifest:
        for key, value in manifest["versions"].items():
            if isinstance(value, dict) and "href" in value:
                if archive_name in value["href"]:
                    version_key = key
                    manifest = manifest["versions"]  # Work with nested structure
                    break

    if version_key is None:
        print(f"Warning: Could not find version entry for {archive_name}")
        print("You'll need to manually update the manifest.")
        return

    # Create parts list
    parts_list = []
    for part_path, sha256 in parts:
        part_url = f"{base_url}/{part_path.name}"
        parts_list.append({"href": part_url, "sha256": sha256})

    # Update manifest
    manifest[version_key]["sha256"] = full_archive_sha256
    manifest[version_key]["parts"] = parts_list

    # Write updated manifest
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")

    print(f"Updated {manifest_path} with {len(parts)} parts")


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Split large archives for GitHub storage",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Split Emscripten archive
  python split_archive.py ../assets/emscripten/linux/x86_64/emscripten-4.0.15-linux-x86_64.tar.zst

  # Split with custom part size
  python split_archive.py --part-size 90 archive.tar.zst

  # Split and update manifest
  python split_archive.py archive.tar.zst --manifest manifest.json \\
    --base-url https://raw.githubusercontent.com/user/repo/main/assets/path
""",
    )

    parser.add_argument("archive", type=Path, help="Path to .tar.zst archive to split")
    parser.add_argument(
        "--part-size",
        type=int,
        default=95,
        help="Size of each part in MB (default: 95 MB)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Directory to write parts to (default: same as archive)",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        help="Path to manifest.json to update with part information",
    )
    parser.add_argument(
        "--base-url",
        help="Base URL for downloading parts (required with --manifest)",
    )

    args = parser.parse_args()

    # Validate arguments
    if args.manifest and not args.base_url:
        parser.error("--base-url is required when --manifest is specified")

    try:
        # Split archive
        parts = split_archive(args.archive, args.part_size, args.output_dir)

        if not parts:
            print("Error: No parts created")
            return 1

        # Update manifest if requested
        if args.manifest:
            update_manifest_with_parts(
                args.manifest,
                args.archive.name,
                parts,
                args.base_url,
            )

        print("\nSuccess! Part files created:")
        for part_path, sha256 in parts:
            print(f"  {part_path.name} (SHA256: {sha256})")

        print("\nNext steps:")
        print("1. Remove the original large archive from git")
        print("2. Add the part files to git")
        print("3. Update the manifest if not done automatically")
        print("4. Commit and push changes")

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
