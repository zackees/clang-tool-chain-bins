#!/usr/bin/env python3
"""Integrate validated clang-extra CI artifacts and regenerate all indexes."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from clang_tool_chain_bins._impl.archive_index import (
    build_aggregate_index,
    build_manifest_lookup,
    build_meta_index,
    write_archive_index,
)


def merge_component_manifest(destination: Path, archives: list[Path]) -> None:
    manifest_path = destination / "manifest.json"
    root = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {"platforms": []}
    for archive in archives:
        relative = archive.relative_to(destination)
        if len(relative.parts) < 3:
            raise ValueError(f"unexpected clang-extra archive path: {archive}")
        platform, arch = relative.parts[0], relative.parts[1]
        platform_entry = next((item for item in root["platforms"] if item.get("platform") == platform), None)
        if platform_entry is None:
            platform_entry = {"platform": platform, "architectures": []}
            root["platforms"].append(platform_entry)
        if not any(item.get("arch") == arch for item in platform_entry["architectures"]):
            platform_entry["architectures"].append(
                {"arch": arch, "manifest_path": f"{platform}/{arch}/manifest.json"}
            )
    manifest_path.write_text(json.dumps(root, indent=2) + "\n", encoding="utf-8")


def _record_group_key(item: dict, list_key: str) -> tuple[str, ...]:
    if list_key == "tools":
        return item["tool_name"], item["component"]
    return (item["component"],)


def _record_order_key(item: dict, list_key: str) -> tuple[str, ...]:
    final_key = item["path_in_archive"] if list_key == "tools" else item["filename"]
    return (
        item.get("platform") or "",
        item.get("arch") or "",
        item.get("version") or "",
        final_key,
    )


def merge_archive_records(
    existing: dict, generated: dict, list_key: str, replace_archive_paths: set[str]
) -> dict:
    """Replace integrated archive records while retaining all untouched baseline data."""
    archive_key = "relative_path" if list_key == "archives" else "archive_path"
    normalized_paths = {path.replace("\\", "/") for path in replace_archive_paths}
    replacements = [
        item
        for item in generated[list_key]
        if item.get(archive_key, "").replace("\\", "/") in normalized_paths
    ]
    found_paths = {item[archive_key].replace("\\", "/") for item in replacements}
    missing_paths = normalized_paths - found_paths
    if missing_paths:
        raise ValueError(f"generated {list_key} index is missing archives: {sorted(missing_paths)}")
    replacements_by_path: dict[str, list[dict]] = {}
    for item in replacements:
        replacements_by_path.setdefault(item[archive_key].replace("\\", "/"), []).append(item)

    merged: list[dict] = []
    replaced_paths: set[str] = set()
    for item in existing[list_key]:
        path = item.get(archive_key, "").replace("\\", "/")
        if path in replacements_by_path:
            if path not in replaced_paths:
                merged.extend(replacements_by_path[path])
                replaced_paths.add(path)
        else:
            merged.append(item)

    remaining = [
        item for item in replacements if item[archive_key].replace("\\", "/") not in replaced_paths
    ]
    if list_key not in {"archives", "tools", "indexes"}:
        raise ValueError(f"unsupported index list: {list_key}")

    for replacement in remaining:
        matching_positions = [
            index
            for index, item in enumerate(merged)
            if _record_group_key(item, list_key) == _record_group_key(replacement, list_key)
        ]
        insert_at = len(merged)
        for index in matching_positions:
            if _record_order_key(merged[index], list_key) > _record_order_key(replacement, list_key):
                insert_at = index
                break
        else:
            if matching_positions:
                insert_at = matching_positions[-1] + 1
        merged.insert(insert_at, replacement)
    return {**existing, list_key: merged}


def rebuild_indexes(repo_root: Path, archives: list[Path]) -> None:
    """Rebuild clang-extra index records without dropping legacy assets lacking sidecars."""
    tools_data = repo_root / "tools" / "data"
    package_data = repo_root / "clang_tool_chain_bins" / "_impl" / "data"
    existing_aggregate = json.loads((tools_data / "tool-index.json").read_text(encoding="utf-8"))
    existing_meta = json.loads((tools_data / "index-meta.json").read_text(encoding="utf-8"))

    with tempfile.TemporaryDirectory(prefix="clang-extra-index-") as directory:
        generated_aggregate_path = Path(directory) / "tool-index.json"
        generated_meta_path = Path(directory) / "index-meta.json"
        build_aggregate_index(repo_root / "assets", generated_aggregate_path)
        build_meta_index(repo_root / "assets", generated_meta_path)
        generated_aggregate = json.loads(generated_aggregate_path.read_text(encoding="utf-8"))
        generated_meta = json.loads(generated_meta_path.read_text(encoding="utf-8"))

    assets_root = repo_root / "assets"
    replace_archive_paths = {str(archive.relative_to(assets_root)) for archive in archives}
    aggregate = merge_archive_records(
        existing_aggregate, generated_aggregate, "archives", replace_archive_paths
    )
    aggregate = merge_archive_records(aggregate, generated_aggregate, "tools", replace_archive_paths)
    aggregate["archive_count"] = len(aggregate["archives"])
    aggregate["tool_count"] = len(aggregate["tools"])
    meta = merge_archive_records(existing_meta, generated_meta, "indexes", replace_archive_paths)
    meta["index_count"] = len(meta["indexes"])

    for data_dir in (tools_data, package_data):
        (data_dir / "tool-index.json").write_text(
            json.dumps(aggregate, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (data_dir / "index-meta.json").write_text(
            json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8"
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

    merge_component_manifest(destination, archives)
    lookup = build_manifest_lookup(repo_root / "assets")
    for archive in archives:
        write_archive_index(archive, repo_root / "assets", lookup)
    rebuild_indexes(repo_root, archives)
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
