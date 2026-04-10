from __future__ import annotations

import argparse
import contextlib
import json
import tarfile
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

import pyzstd

from .common import EXECUTABLE_EXTENSIONS, NON_TOOL_EXTENSIONS, normalize_tool_name, sha256_file

INDEX_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ManifestArchiveInfo:
    filename: str
    url: str
    sha256: str
    version: str | None
    manifest_path: str
    parts: list[dict[str, Any]]


def default_assets_root() -> Path:
    return Path(__file__).resolve().parents[1] / "assets"


def aggregate_index_path() -> Path:
    return Path(__file__).resolve().parent / "data" / "tool-index.json"


def sidecar_path_for_archive(archive_path: Path) -> Path:
    return Path(f"{archive_path}.json")


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _iter_manifest_archive_infos(assets_root: Path) -> list[ManifestArchiveInfo]:
    infos_by_filename: dict[str, ManifestArchiveInfo] = {}
    for manifest_path in sorted(assets_root.rglob("manifest*.json")):
        data = _load_json(manifest_path)
        if not isinstance(data, dict):
            continue

        entries: list[tuple[str | None, dict[str, Any]]] = []
        if isinstance(data.get("assets"), list):
            for entry in data["assets"]:
                if isinstance(entry, dict) and "href" in entry and "sha256" in entry:
                    entries.append((entry.get("version") or entry.get("clang_version") or data.get("clang_version"), entry))
        if isinstance(data.get("versions"), dict):
            for version_key, entry in data["versions"].items():
                if isinstance(entry, dict) and "href" in entry and "sha256" in entry:
                    entries.append((entry.get("version") or version_key, entry))
        for key, entry in data.items():
            if key in {"latest", "versions", "assets", "platforms", "architectures", "platform"}:
                continue
            if isinstance(entry, dict) and "href" in entry and "sha256" in entry:
                entries.append((entry.get("version") or key, entry))
        if isinstance(data, dict) and "href" in data and "sha256" in data:
            entries.append((data.get("version") or data.get("latest"), data))

        rel_manifest = str(manifest_path.relative_to(assets_root))
        for version, entry in entries:
            filename = Path(urlparse(entry["href"]).path).name
            info = ManifestArchiveInfo(
                filename=filename,
                url=entry["href"],
                sha256=entry["sha256"],
                version=str(version) if version is not None else None,
                manifest_path=rel_manifest,
                parts=list(entry.get("parts", [])) if isinstance(entry.get("parts"), list) else [],
            )
            current = infos_by_filename.get(filename)
            if current is None or len(Path(info.manifest_path).parts) > len(Path(current.manifest_path).parts):
                infos_by_filename[filename] = info

    return sorted(infos_by_filename.values(), key=lambda item: item.filename)


def build_manifest_lookup(assets_root: Path) -> dict[str, ManifestArchiveInfo]:
    return {info.filename: info for info in _iter_manifest_archive_infos(assets_root)}


def _infer_component_platform_arch(archive_path: Path, assets_root: Path) -> tuple[str, str | None, str | None]:
    rel_parts = archive_path.relative_to(assets_root).parts
    component = rel_parts[0]
    platform: str | None = None
    arch: str | None = None

    if len(rel_parts) >= 2 and rel_parts[1] in {"win", "linux", "darwin"}:
        platform = rel_parts[1]
        if len(rel_parts) >= 4 and rel_parts[2] == "mingw":
            arch = f"mingw-{rel_parts[3]}"
        elif len(rel_parts) >= 3:
            arch = rel_parts[2]

    return component, platform, arch


def _guess_version_from_filename(component: str, filename: str) -> str | None:
    stem = filename[:-8] if filename.endswith(".tar.zst") else filename
    if component == "clang" and stem.startswith("llvm-mingw-"):
        parts = stem.split("-")
        if len(parts) >= 3:
            return parts[2]
    prefixes = {
        "clang": "llvm-",
        "clang-extra": "clang-extra-",
        "cosmocc": "cosmocc-universal-",
        "emscripten": "emscripten-",
        "iwyu": "iwyu-",
        "lldb": "lldb-",
        "mingw": "mingw-sysroot-",
        "nodejs": "nodejs-",
        "valgrind": "valgrind-",
    }
    prefix = prefixes.get(component)
    if prefix and stem.startswith(prefix):
        return stem[len(prefix) :].split("-")[0]
    return None


def _fallback_archive_url(archive_path: Path, assets_root: Path) -> str:
    rel = archive_path.relative_to(assets_root).as_posix()
    return f"https://raw.githubusercontent.com/zackees/clang-tool-chain-bins/main/assets/{rel}"


def _parse_sha256_sidefile(path: Path) -> str | None:
    if not path.exists():
        return None
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        return None
    return content.split()[0]


def _write_sha256_sidefile(path: Path, sha256: str, archive_name: str) -> None:
    path.write_text(f"{sha256}  {archive_name}\n", encoding="utf-8")


def _parse_git_lfs_pointer(path: Path) -> dict[str, str] | None:
    if path.stat().st_size > 1024:
        return None
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines or lines[0] != "version https://git-lfs.github.com/spec/v1":
        return None
    payload: dict[str, str] = {}
    for line in lines[1:]:
        if line.startswith("oid sha256:"):
            payload["sha256"] = line.split("oid sha256:", 1)[1]
        elif line.startswith("size "):
            payload["size"] = line.split("size ", 1)[1]
    return payload if "sha256" in payload else None


def _member_type(member: tarfile.TarInfo) -> str:
    if member.isdir():
        return "dir"
    if member.isfile():
        return "file"
    if member.issym():
        return "symlink"
    if member.islnk():
        return "hardlink"
    return "other"


def _is_tool_candidate(member: tarfile.TarInfo) -> bool:
    path = PurePosixPath(member.name)
    if "bin" not in path.parts:
        return False
    if member.isfile():
        suffix = path.suffix.lower()
        if suffix in NON_TOOL_EXTENSIONS:
            return False
        if suffix in EXECUTABLE_EXTENSIONS:
            return True
        if not suffix:
            return bool(member.mode & 0o111)
        return bool(member.mode & 0o111)
    return member.issym() or member.islnk()


@contextlib.contextmanager
def _materialized_archive_path(archive_path: Path, manifest_info: ManifestArchiveInfo | None):
    pointer_info = _parse_git_lfs_pointer(archive_path)
    if pointer_info is None:
        yield archive_path, None
        return

    if manifest_info is None or not manifest_info.url:
        raise RuntimeError(f"Archive {archive_path} is a Git LFS pointer but no download URL was found")

    with tempfile.TemporaryDirectory(prefix=f"{archive_path.stem}.", suffix=".archive") as tmp:
        tmp_path = Path(tmp) / archive_path.name
        with urllib.request.urlopen(manifest_info.url) as response, tmp_path.open("wb") as out_f:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                out_f.write(chunk)
        yield tmp_path, pointer_info


def index_archive(archive_path: Path, assets_root: Path, manifest_lookup: dict[str, ManifestArchiveInfo]) -> dict[str, Any]:
    component, platform, arch = _infer_component_platform_arch(archive_path, assets_root)
    manifest_info = manifest_lookup.get(archive_path.name)
    pointer_info = _parse_git_lfs_pointer(archive_path)
    archive_sha256 = pointer_info["sha256"] if pointer_info else sha256_file(archive_path)

    sha_sidecar = Path(f"{archive_path}.sha256")
    existing_sha = _parse_sha256_sidefile(sha_sidecar)
    if existing_sha != archive_sha256:
        _write_sha256_sidefile(sha_sidecar, archive_sha256, archive_path.name)

    files: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []

    with _materialized_archive_path(archive_path, manifest_info) as (source_path, _), source_path.open(
        "rb"
    ) as raw_f, pyzstd.ZstdFile(raw_f) as zstd_f, tarfile.open(fileobj=zstd_f, mode="r|") as tar:
        for member in tar:
            entry = {
                "path": member.name,
                "type": _member_type(member),
                "size": member.size,
                "mode": oct(member.mode),
            }
            if member.linkname:
                entry["linkname"] = member.linkname
            files.append(entry)

            if not _is_tool_candidate(member):
                continue

            tool_entry = {
                "path": member.name,
                "file_name": PurePosixPath(member.name).name,
                "tool_name": normalize_tool_name(PurePosixPath(member.name).name),
                "type": _member_type(member),
                "size": member.size,
                "mode": oct(member.mode),
            }
            if member.isfile():
                extracted = tar.extractfile(member)
                if extracted is not None:
                    import hashlib

                    digest = hashlib.sha256()
                    while True:
                        chunk = extracted.read(1024 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
                    tool_entry["sha256"] = digest.hexdigest()
            elif member.linkname:
                tool_entry["linkname"] = member.linkname
            tools.append(tool_entry)

    tools.sort(key=lambda item: (item["tool_name"], item["path"]))

    relative_archive_path = str(archive_path.relative_to(assets_root))
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "archive": {
            "relative_path": relative_archive_path,
            "filename": archive_path.name,
            "component": component,
            "version": manifest_info.version if manifest_info and manifest_info.version else _guess_version_from_filename(component, archive_path.name),
            "platform": platform,
            "arch": arch,
            "size_bytes": archive_path.stat().st_size,
            "archive_size_bytes": int(pointer_info["size"]) if pointer_info and "size" in pointer_info else archive_path.stat().st_size,
            "sha256": archive_sha256,
            "url": manifest_info.url if manifest_info else _fallback_archive_url(archive_path, assets_root),
            "manifest_path": manifest_info.manifest_path if manifest_info else None,
            "parts": manifest_info.parts if manifest_info else [],
            "git_lfs_pointer": bool(pointer_info),
        },
        "file_count": len(files),
        "tool_count": len(tools),
        "files": files,
        "tools": tools,
        "hash_tree": None,
    }


def write_archive_index(archive_path: Path, assets_root: Path, manifest_lookup: dict[str, ManifestArchiveInfo]) -> Path:
    sidecar_path = sidecar_path_for_archive(archive_path)
    data = index_archive(archive_path, assets_root, manifest_lookup)
    sidecar_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return sidecar_path


def build_sidecar_indexes(assets_root: Path | None = None) -> list[Path]:
    root = (assets_root or default_assets_root()).resolve()
    manifest_lookup = build_manifest_lookup(root)
    written: list[Path] = []
    for archive_path in sorted(root.rglob("*.tar.zst")):
        written.append(write_archive_index(archive_path, root, manifest_lookup))
    return written


def build_aggregate_index(assets_root: Path | None = None, output_path: Path | None = None) -> Path:
    root = (assets_root or default_assets_root()).resolve()
    output = output_path or aggregate_index_path()
    output.parent.mkdir(parents=True, exist_ok=True)

    sidecars = sorted(root.rglob("*.tar.zst.json"))
    if not sidecars and any(root.rglob("*.tar.zst")):
        build_sidecar_indexes(root)
        sidecars = sorted(root.rglob("*.tar.zst.json"))

    archives: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []

    for sidecar_path in sidecars:
        data = _load_json(sidecar_path)
        archive = data["archive"]
        archives.append(
            {
                "relative_path": archive["relative_path"],
                "filename": archive["filename"],
                "component": archive["component"],
                "version": archive.get("version"),
                "platform": archive.get("platform"),
                "arch": archive.get("arch"),
                "sha256": archive["sha256"],
                "url": archive.get("url"),
                "parts": archive.get("parts", []),
                "index_path": str(sidecar_path.relative_to(root)),
                "tool_count": data.get("tool_count", 0),
                "file_count": data.get("file_count", 0),
            }
        )
        for tool in data.get("tools", []):
            tools.append(
                {
                    "tool_name": tool["tool_name"],
                    "file_name": tool["file_name"],
                    "path_in_archive": tool["path"],
                    "tool_sha256": tool.get("sha256"),
                    "tool_type": tool["type"],
                    "size": tool["size"],
                    "component": archive["component"],
                    "version": archive.get("version"),
                    "platform": archive.get("platform"),
                    "arch": archive.get("arch"),
                    "archive_path": archive["relative_path"],
                    "archive_filename": archive["filename"],
                    "archive_sha256": archive["sha256"],
                    "archive_url": archive.get("url"),
                    "parts": archive.get("parts", []),
                }
            )

    archives.sort(key=lambda item: (item["component"], item.get("platform") or "", item.get("arch") or "", item.get("version") or "", item["filename"]))
    tools.sort(key=lambda item: (item["tool_name"], item["component"], item.get("platform") or "", item.get("arch") or "", item.get("version") or "", item["path_in_archive"]))

    payload = {
        "schema_version": INDEX_SCHEMA_VERSION,
        "archive_count": len(archives),
        "tool_count": len(tools),
        "archives": archives,
        "tools": tools,
    }
    output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate per-archive JSON indexes and an aggregate tool index.")
    parser.add_argument("--assets-root", type=Path, default=default_assets_root(), help="Assets root to scan.")
    parser.add_argument("--skip-sidecars", action="store_true", help="Skip per-archive sidecar generation.")
    parser.add_argument("--skip-aggregate", action="store_true", help="Skip aggregate index generation.")
    parser.add_argument("--aggregate-output", type=Path, default=aggregate_index_path(), help="Aggregate index output path.")
    args = parser.parse_args(argv)

    root = args.assets_root.resolve()
    if not args.skip_sidecars:
        build_sidecar_indexes(root)
    if not args.skip_aggregate:
        build_aggregate_index(root, args.aggregate_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
