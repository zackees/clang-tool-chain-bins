#!/usr/bin/env python3
"""Integrate validated clang-extra CI artifacts and regenerate all indexes."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from clang_tool_chain_bins._impl.archive_index import (
    build_aggregate_index,
    build_manifest_lookup,
    build_meta_index,
    write_archive_index,
)


def integrate(artifacts_dir: Path, repo_root: Path) -> list[Path]:
    source = artifacts_dir / "assets" / "clang-extra"
    if not source.is_dir():
        raise FileNotFoundError(f"expected artifact directory {source}")
    destination = repo_root / "assets" / "clang-extra"
    archives: list[Path] = []
    for source_file in sorted(path for path in source.rglob("*") if path.is_file()):
        relative = source_file.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target)
        if source_file.suffix == ".zst" and source_file.name.endswith(".tar.zst"):
            archives.append(target)

    lookup = build_manifest_lookup(repo_root / "assets")
    for archive in archives:
        write_archive_index(archive, repo_root / "assets", lookup)
    build_aggregate_index(repo_root / "assets")
    build_meta_index(repo_root / "assets")
    return archives


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts_dir", type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    archives = integrate(args.artifacts_dir.resolve(), args.repo_root.resolve())
    print(f"integrated {len(archives)} clang-extra archives")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
