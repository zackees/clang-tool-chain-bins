#!/usr/bin/env python3
"""Validate clang-extra discovery and clean install APIs against a local archive."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

import clang_tool_chain_bins as bins

EXPECTED_TOOLS = {
    "clang-format",
    "clang-query",
    "clang-tidy",
    "clangd",
    "git-clang-format",
    "run-clang-tidy",
}


def validate(
    index_path: Path,
    archive_path: Path,
    *,
    platform: str,
    arch: str,
    version: str,
    expected_major: int,
) -> None:
    index = json.loads(index_path.read_text(encoding="utf-8"))
    target_entries = [
        entry
        for entry in index["tools"]
        if entry.get("component") == "clang-extra"
        and entry.get("platform") == platform
        and entry.get("arch") == arch
        and entry.get("version") == version
    ]
    actual_tools = {entry["tool_name"] for entry in target_entries}
    if actual_tools != EXPECTED_TOOLS:
        raise RuntimeError(f"unexpected indexed tools: {sorted(actual_tools)}")

    local_url = archive_path.resolve().as_uri()
    for entry in target_entries:
        entry["archive_url"] = local_url
        entry["probe_urls"] = [local_url]
        entry["parts"] = []

    with tempfile.TemporaryDirectory(prefix="clang-extra-install-validation-") as directory:
        root = Path(directory)
        local_index = root / "tool-index.json"
        local_index.write_text(json.dumps(index), encoding="utf-8")
        filters = {
            "index_path": local_index,
            "platform": platform,
            "arch": arch,
            "version": version,
            "component": "clang-extra",
        }

        queried = bins.query("clangd", home_dir=root / "query-home", **filters)
        if len(queried) != 1 or len(queried[0].matches) != 1:
            raise RuntimeError("query did not return exactly one clangd match")
        match = bins.resolve_one("clangd", **filters)

        install_home = root / "install-home"
        installed = bins.install("clangd", home_dir=install_home, **filters)[0]
        if installed.status != "installed":
            raise RuntimeError(f"clean install returned {installed.status!r}")
        tool_path = Path(installed.install_path).joinpath(*PurePosixPath(match.path_in_archive).parts)
        version_result = subprocess.run(
            [str(tool_path), "--version"], capture_output=True, text=True, check=True
        )
        if f"version {expected_major}." not in version_result.stdout:
            raise RuntimeError(f"unexpected clangd version: {version_result.stdout.strip()}")

        tool_path.unlink()
        if bins.is_installed("clangd", home_dir=install_home, **filters):
            raise RuntimeError("stale done marker was accepted after clangd was removed")
        repaired = bins.ensure("clangd", home_dir=install_home, **filters)[0]
        if repaired.status != "installed" or not tool_path.is_file():
            raise RuntimeError("ensure did not repair a stale installation")

        ensure_home = root / "ensure-home"
        ensured = bins.ensure("clangd", home_dir=ensure_home, **filters)[0]
        ensured_path = Path(ensured.install_path).joinpath(*PurePosixPath(match.path_in_archive).parts)
        if ensured.status != "installed" or not ensured_path.is_file():
            raise RuntimeError("ensure did not produce a usable clean-home installation")

    print("clang-extra query/resolve/install/ensure validation passed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("index", type=Path)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--arch", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--expected-major", required=True, type=int)
    args = parser.parse_args()
    validate(
        args.index,
        args.archive,
        platform=args.platform,
        arch=args.arch,
        version=args.version,
        expected_major=args.expected_major,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
