from __future__ import annotations

import argparse
import json
import os
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

import fasteners
import pyzstd

from .archive_index import aggregate_index_path
from .common import get_cache_path, get_home_dir, get_install_dir, get_lock_path, sha256_file

if TYPE_CHECKING:
    from collections.abc import Sequence


def _load_aggregate_index(index_path: Path | None = None) -> dict[str, Any]:
    path = index_path or aggregate_index_path()
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _filter_matches(
    data: dict[str, Any],
    tool: str,
    *,
    platform: str | None,
    arch: str | None,
    version: str | None,
    component: str | None,
) -> list[dict[str, Any]]:
    matches = []
    for entry in data.get("tools", []):
        if entry["tool_name"] != tool and entry["file_name"] != tool:
            continue
        if platform and entry.get("platform") != platform:
            continue
        if arch and entry.get("arch") != arch:
            continue
        if version and entry.get("version") != version:
            continue
        if component and entry.get("component") != component:
            continue
        matches.append(entry)
    return matches


def _download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as response, destination.open("wb") as out_f:
        shutil.copyfileobj(response, out_f)


def _download_multipart(parts: list[dict[str, Any]], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as out_f:
        for part in parts:
            with urllib.request.urlopen(part["href"]) as response:
                shutil.copyfileobj(response, out_f)


def _validated_member_path(base_dir: Path, member_name: str) -> Path:
    target_path = (base_dir / member_name).resolve()
    try:
        target_path.relative_to(base_dir.resolve())
    except ValueError as exc:
        raise RuntimeError(f"Archive member escapes install root: {member_name}") from exc
    return target_path


def _safe_extractall(tar: tarfile.TarFile, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for member in tar:
        _validated_member_path(destination, member.name)
        if member.issym() or member.islnk():
            if not member.linkname:
                raise RuntimeError(f"Archive link member missing target: {member.name}")
            if os.path.isabs(member.linkname):
                raise RuntimeError(f"Archive link escapes install root: {member.name} -> {member.linkname}")
            if ".." in PurePosixPath(member.linkname).parts:
                raise RuntimeError(f"Archive link escapes install root: {member.name} -> {member.linkname}")
        tar.extract(member, destination, filter="fully_trusted")


def _ensure_cached(match: dict[str, Any], home_dir: Path) -> Path:
    cache_path = get_cache_path(match["component"], match.get("platform"), match.get("arch"), match["archive_sha256"], home_dir)
    if cache_path.exists() and sha256_file(cache_path) == match["archive_sha256"]:
        return cache_path

    tmp_path = cache_path.with_suffix(".tmp")
    if tmp_path.exists():
        tmp_path.unlink()

    parts = match.get("parts") or []
    if parts:
        _download_multipart(parts, tmp_path)
    else:
        _download(match["archive_url"], tmp_path)

    actual_sha = sha256_file(tmp_path)
    if actual_sha != match["archive_sha256"]:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError(f"Downloaded archive hash mismatch for {match['archive_filename']}: {actual_sha} != {match['archive_sha256']}")

    tmp_path.replace(cache_path)
    return cache_path


def _extract_archive(archive_path: Path, install_dir: Path) -> None:
    root_like_dirnames = {"bin", "etc", "include", "lib", "lib64", "opt", "sbin", "share", "usr"}
    parent = install_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=parent, prefix=f".{install_dir.name}.") as tmp:
        tmp_path = Path(tmp)
        with archive_path.open("rb") as raw_f, pyzstd.ZstdFile(raw_f) as zstd_f, tarfile.open(
            fileobj=zstd_f, mode="r|"
        ) as tar:
            _safe_extractall(tar, tmp_path)

        extracted_root = tmp_path
        children = list(tmp_path.iterdir())
        if len(children) == 1 and children[0].is_dir() and children[0].name not in root_like_dirnames:
            extracted_root = children[0]

        if install_dir.exists():
            shutil.rmtree(install_dir)
        shutil.move(str(extracted_root), str(install_dir))


def install_match(match: dict[str, Any], *, home_dir: Path | None = None) -> Path:
    resolved_home = (home_dir or get_home_dir()).expanduser().resolve()
    install_dir = get_install_dir(match["component"], match.get("platform"), match.get("arch"), resolved_home)
    lock_path = get_lock_path(match["component"], match.get("platform"), match.get("arch"), resolved_home)
    lock = fasteners.InterProcessLock(str(lock_path))

    with lock:
        done_file = install_dir / "done.txt"
        if done_file.exists():
            content = done_file.read_text(encoding="utf-8")
            if match["archive_sha256"] in content:
                return install_dir

        cache_path = _ensure_cached(match, resolved_home)
        _extract_archive(cache_path, install_dir)
        done_file.write_text(
            "\n".join(
                [
                    f"tool_name={match['tool_name']}",
                    f"component={match['component']}",
                    f"version={match.get('version')}",
                    f"platform={match.get('platform')}",
                    f"arch={match.get('arch')}",
                    f"archive_sha256={match['archive_sha256']}",
                    f"archive_url={match['archive_url']}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return install_dir


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install an archive containing the requested tool.")
    parser.add_argument("tool", help="Exact tool name such as llvm-pdbutil or clang-format.")
    parser.add_argument("--platform", default=None, help="Filter by platform.")
    parser.add_argument("--arch", default=None, help="Filter by architecture.")
    parser.add_argument("--version", default=None, help="Filter by version.")
    parser.add_argument("--component", default=None, help="Filter by component family.")
    parser.add_argument("--all", action="store_true", help="Install every matching archive.")
    parser.add_argument("--home-dir", type=Path, default=None, help="Override the install/cache root.")
    parser.add_argument("--index", type=Path, default=None, help="Override the aggregate index path.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    data = _load_aggregate_index(args.index)
    matches = _filter_matches(
        data,
        args.tool,
        platform=args.platform,
        arch=args.arch,
        version=args.version,
        component=args.component,
    )

    if not matches:
        raise SystemExit(f"No install candidates found for {args.tool}")
    if len(matches) > 1 and not args.all:
        raise SystemExit(
            "Multiple install candidates found. Provide --platform/--arch/--version/--component or use --all."
        )

    selected = matches if args.all else [matches[0]]
    results = []
    for match in selected:
        install_dir = install_match(match, home_dir=args.home_dir)
        results.append(
            {
                "tool_name": match["tool_name"],
                "component": match["component"],
                "platform": match.get("platform"),
                "arch": match.get("arch"),
                "version": match.get("version"),
                "install_path": str(install_dir),
            }
        )

    for result in results:
        print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
