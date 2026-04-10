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
from .json_utils import load_path

if TYPE_CHECKING:
    from collections.abc import Sequence


OperationName = str
INSTALL_OPERATIONS = {"install", "ensure", "tryinstall"}


def _load_aggregate_index(index_path: Path | None = None) -> dict[str, Any]:
    path = index_path or aggregate_index_path()
    return load_path(path)


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
        raise RuntimeError(
            f"Downloaded archive hash mismatch for {match['archive_filename']}: {actual_sha} != {match['archive_sha256']}"
        )

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


def _resolve_install_context(match: dict[str, Any], home_dir: Path | None = None) -> tuple[Path, Path, Path]:
    resolved_home = (home_dir or get_home_dir()).expanduser().resolve()
    install_dir = get_install_dir(match["component"], match.get("platform"), match.get("arch"), resolved_home)
    lock_path = get_lock_path(match["component"], match.get("platform"), match.get("arch"), resolved_home)
    return resolved_home, install_dir, lock_path


def is_match_installed(match: dict[str, Any], *, home_dir: Path | None = None) -> bool:
    _, install_dir, _ = _resolve_install_context(match, home_dir)
    done_file = install_dir / "done.txt"
    if not done_file.exists():
        return False
    return match["archive_sha256"] in done_file.read_text(encoding="utf-8")


def _write_done_file(match: dict[str, Any], install_dir: Path) -> None:
    done_file = install_dir / "done.txt"
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


def _result_payload(
    match: dict[str, Any],
    install_dir: Path,
    *,
    operation: OperationName,
    status: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    return {
        "operation": operation,
        "status": status,
        "dry_run": dry_run,
        "tool_name": match["tool_name"],
        "component": match["component"],
        "platform": match.get("platform"),
        "arch": match.get("arch"),
        "version": match.get("version"),
        "install_path": str(install_dir),
        "archive_sha256": match["archive_sha256"],
        "archive_url": match["archive_url"],
    }


def _install_unlocked(match: dict[str, Any], *, home_dir: Path | None = None, dry_run: bool = False) -> dict[str, Any]:
    resolved_home, install_dir, _ = _resolve_install_context(match, home_dir)
    if dry_run:
        return _result_payload(match, install_dir, operation="install", status="dry_run", dry_run=True)

    cache_path = _ensure_cached(match, resolved_home)
    _extract_archive(cache_path, install_dir)
    _write_done_file(match, install_dir)
    return _result_payload(match, install_dir, operation="install", status="installed")


def install_match(match: dict[str, Any], *, home_dir: Path | None = None, dry_run: bool = False) -> dict[str, Any]:
    _, install_dir, lock_path = _resolve_install_context(match, home_dir)
    lock = fasteners.InterProcessLock(str(lock_path))

    with lock:
        if is_match_installed(match, home_dir=home_dir):
            return _result_payload(match, install_dir, operation="install", status="already_installed", dry_run=dry_run)
        result = _install_unlocked(match, home_dir=home_dir, dry_run=dry_run)
        result["operation"] = "install"
        return result


def ensure_match(match: dict[str, Any], *, home_dir: Path | None = None, dry_run: bool = False) -> dict[str, Any]:
    _, install_dir, _ = _resolve_install_context(match, home_dir)
    if is_match_installed(match, home_dir=home_dir):
        return _result_payload(match, install_dir, operation="ensure", status="already_installed", dry_run=dry_run)

    result = install_match(match, home_dir=home_dir, dry_run=dry_run)
    result["operation"] = "ensure"
    return result


def tryinstall_match(match: dict[str, Any], *, home_dir: Path | None = None, dry_run: bool = False) -> dict[str, Any]:
    _, install_dir, lock_path = _resolve_install_context(match, home_dir)
    if is_match_installed(match, home_dir=home_dir):
        return _result_payload(match, install_dir, operation="tryinstall", status="already_installed", dry_run=dry_run)

    lock = fasteners.InterProcessLock(str(lock_path))
    acquired = lock.acquire(blocking=False)
    if not acquired:
        lock._do_close()
        return _result_payload(match, install_dir, operation="tryinstall", status="locked", dry_run=dry_run)

    try:
        if is_match_installed(match, home_dir=home_dir):
            return _result_payload(match, install_dir, operation="tryinstall", status="already_installed", dry_run=dry_run)
        result = _install_unlocked(match, home_dir=home_dir, dry_run=dry_run)
        result["operation"] = "tryinstall"
        return result
    finally:
        lock.release()


def _run_operation(
    operation: OperationName,
    match: dict[str, Any],
    *,
    home_dir: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    if operation == "install":
        return install_match(match, home_dir=home_dir, dry_run=dry_run)
    if operation == "ensure":
        return ensure_match(match, home_dir=home_dir, dry_run=dry_run)
    if operation == "tryinstall":
        return tryinstall_match(match, home_dir=home_dir, dry_run=dry_run)
    raise ValueError(f"Unknown install operation: {operation}")


def main(argv: Sequence[str] | None = None, *, operation: OperationName = "install") -> int:
    if operation not in INSTALL_OPERATIONS:
        raise ValueError(f"Unknown install operation: {operation}")

    parser = argparse.ArgumentParser(description=f"{operation.capitalize()} an archive containing the requested tool.")
    parser.add_argument("tool", help="Exact tool name such as llvm-pdbutil or clang-format.")
    parser.add_argument("--platform", default=None, help="Filter by platform.")
    parser.add_argument("--arch", default=None, help="Filter by architecture.")
    parser.add_argument("--version", default=None, help="Filter by version.")
    parser.add_argument("--component", default=None, help="Filter by component family.")
    parser.add_argument("--all", action="store_true", help="Operate on every matching archive.")
    parser.add_argument("--dry-run", action="store_true", help="Print the install plan without downloading or extracting.")
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
    results = [_run_operation(operation, match, home_dir=args.home_dir, dry_run=args.dry_run) for match in selected]

    for result in results:
        print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
