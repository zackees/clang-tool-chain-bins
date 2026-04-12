from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from tools import install as install_impl
from tools import query as query_impl

OperationName = Literal["install", "ensure", "tryinstall"]


@dataclass(frozen=True)
class ToolRequest:
    tool: str
    platform: str | None = None
    arch: str | None = None
    version: str | None = None
    component: str | None = None


@dataclass(frozen=True)
class ToolMatch:
    tool_name: str
    file_name: str
    path_in_archive: str
    tool_sha256: str | None
    tool_type: str
    size: int
    component: str
    version: str | None
    platform: str | None
    arch: str | None
    archive_path: str
    archive_filename: str
    archive_sha256: str
    archive_url: str | None
    parts: list[dict[str, Any]]
    download_kind: str | None = None
    probe_urls: list[str] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "file_name": self.file_name,
            "path_in_archive": self.path_in_archive,
            "tool_sha256": self.tool_sha256,
            "tool_type": self.tool_type,
            "size": self.size,
            "component": self.component,
            "version": self.version,
            "platform": self.platform,
            "arch": self.arch,
            "archive_path": self.archive_path,
            "archive_filename": self.archive_filename,
            "archive_sha256": self.archive_sha256,
            "archive_url": self.archive_url,
            "parts": self.parts,
            "download_kind": self.download_kind,
            "probe_urls": self.probe_urls,
        }


@dataclass(frozen=True)
class QueryResult:
    query: str
    matches: list[ToolMatch]


@dataclass(frozen=True)
class InstallResult:
    operation: str
    status: str
    dry_run: bool
    tool_name: str
    component: str
    platform: str | None
    arch: str | None
    version: str | None
    install_path: str
    archive_sha256: str
    archive_url: str


def _coerce_match(match: dict[str, Any]) -> ToolMatch:
    return ToolMatch(
        tool_name=match["tool_name"],
        file_name=match["file_name"],
        path_in_archive=match["path_in_archive"],
        tool_sha256=match.get("tool_sha256"),
        tool_type=match["tool_type"],
        size=int(match["size"]),
        component=match["component"],
        version=match.get("version"),
        platform=match.get("platform"),
        arch=match.get("arch"),
        archive_path=match["archive_path"],
        archive_filename=match["archive_filename"],
        archive_sha256=match["archive_sha256"],
        archive_url=match.get("archive_url"),
        parts=list(match.get("parts") or []),
        download_kind=match.get("download_kind"),
        probe_urls=list(match.get("probe_urls") or []),
    )


def query(
    *patterns: str,
    home_dir: Path | None = None,
    index_path: Path | None = None,
    platform: str | None = None,
    arch: str | None = None,
    version: str | None = None,
    component: str | None = None,
) -> list[QueryResult]:
    results = query_impl.query_records(
        list(patterns),
        home_dir=home_dir,
        index_path=index_path,
        platform=platform,
        arch=arch,
        version=version,
        component=component,
    )
    return [
        QueryResult(
            query=result["query"],
            matches=[_coerce_match(match) for match in result["matches"]],
        )
        for result in results
    ]


def resolve(
    tool: str,
    *,
    index_path: Path | None = None,
    platform: str | None = None,
    arch: str | None = None,
    version: str | None = None,
    component: str | None = None,
) -> list[ToolMatch]:
    data = install_impl._load_aggregate_index(index_path)
    matches = install_impl._filter_matches(
        data,
        tool,
        platform=platform,
        arch=arch,
        version=version,
        component=component,
    )
    return [_coerce_match(match) for match in matches]


def resolve_one(
    tool: str,
    *,
    index_path: Path | None = None,
    platform: str | None = None,
    arch: str | None = None,
    version: str | None = None,
    component: str | None = None,
) -> ToolMatch:
    matches = resolve(
        tool,
        index_path=index_path,
        platform=platform,
        arch=arch,
        version=version,
        component=component,
    )
    if not matches:
        raise RuntimeError(f"No install candidates found for {tool}")
    if len(matches) > 1:
        raise RuntimeError(
            "Multiple install candidates found. Provide --platform/--arch/--version/--component or use resolve()."
        )
    return matches[0]


def is_installed(
    tool: str,
    *,
    home_dir: Path | None = None,
    index_path: Path | None = None,
    platform: str | None = None,
    arch: str | None = None,
    version: str | None = None,
    component: str | None = None,
) -> bool:
    match = resolve_one(
        tool,
        index_path=index_path,
        platform=platform,
        arch=arch,
        version=version,
        component=component,
    )
    return install_impl.is_match_installed(match.as_dict(), home_dir=home_dir)


def _run(
    tool: str,
    *,
    operation: OperationName,
    home_dir: Path | None = None,
    index_path: Path | None = None,
    platform: str | None = None,
    arch: str | None = None,
    version: str | None = None,
    component: str | None = None,
    all_matches: bool = False,
    dry_run: bool = False,
) -> list[InstallResult]:
    matches = resolve(
        tool,
        index_path=index_path,
        platform=platform,
        arch=arch,
        version=version,
        component=component,
    )
    if not matches:
        raise RuntimeError(f"No install candidates found for {tool}")
    if len(matches) > 1 and not all_matches:
        raise RuntimeError(
            "Multiple install candidates found. Provide --platform/--arch/--version/--component or set all_matches=True."
        )

    selected = matches if all_matches else [matches[0]]
    results = [
        InstallResult(
            **install_impl._run_operation(
                operation,
                match.as_dict(),
                home_dir=home_dir,
                dry_run=dry_run,
            )
        )
        for match in selected
    ]
    return results


def install(
    tool: str,
    **kwargs: Any,
) -> list[InstallResult]:
    return _run(tool, operation="install", **kwargs)


def ensure(
    tool: str,
    **kwargs: Any,
) -> list[InstallResult]:
    return _run(tool, operation="ensure", **kwargs)


def try_install(
    tool: str,
    **kwargs: Any,
) -> list[InstallResult]:
    return _run(tool, operation="tryinstall", **kwargs)


__all__ = [
    "InstallResult",
    "OperationName",
    "QueryResult",
    "ToolMatch",
    "ToolRequest",
    "ensure",
    "install",
    "is_installed",
    "query",
    "resolve",
    "resolve_one",
    "try_install",
]

# Rust native bindings (available when built with maturin)
try:
    from clang_tool_chain_bins._native import (  # noqa: F401
        create_tar_zst,
        expand_archive,
        generate_checksum_files,
        lfs_media_url,
        md5_file,
        read_platform_manifest,
        sha256_file,
        sha256_verify,
        update_platform_manifest,
    )

    __all__ += [
        "expand_archive",
        "create_tar_zst",
        "sha256_file",
        "md5_file",
        "sha256_verify",
        "generate_checksum_files",
        "read_platform_manifest",
        "update_platform_manifest",
        "lfs_media_url",
    ]
except ImportError:
    pass
