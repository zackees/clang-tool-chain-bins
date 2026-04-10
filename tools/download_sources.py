from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from collections.abc import Iterable


DEFAULT_OWNER_REPO = "zackees/clang-tool-chain-bins"
DEFAULT_BRANCH = "main"


class DownloadKind(str, Enum):
    RAW = "raw"
    LFS = "lfs"
    MULTIPART = "multipart"


@dataclass(frozen=True)
class DownloadSource:
    href: str
    kind: DownloadKind


@dataclass(frozen=True)
class DownloadDescriptor:
    kind: DownloadKind
    href: str
    parts: tuple[DownloadSource, ...]

    @property
    def probe_urls(self) -> tuple[str, ...]:
        if self.parts:
            return tuple(part.href for part in self.parts)
        return (self.href,)


@dataclass(frozen=True)
class _FilterRule:
    pattern: str
    filter_value: str | None


def default_repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_assets_root() -> Path:
    return default_repo_root() / "assets"


def asset_repo_relative_path(
    asset_path: Path | str,
    *,
    repo_root: Path | None = None,
) -> PurePosixPath:
    rel_path = _normalize_repo_relative_path(asset_path, repo_root=repo_root)
    if rel_path.parts[:1] == ("assets",):
        return rel_path
    return PurePosixPath("assets").joinpath(rel_path)


def _normalize_repo_relative_path(path: Path | str, *, repo_root: Path | None = None) -> PurePosixPath:
    root = (repo_root or default_repo_root()).resolve()
    candidate = Path(path)
    if candidate.is_absolute():
        rel = candidate.resolve().relative_to(root)
    else:
        rel = candidate
    return PurePosixPath(rel.as_posix())


def raw_github_url(repo_relative_path: Path | str, *, owner_repo: str = DEFAULT_OWNER_REPO, branch: str = DEFAULT_BRANCH) -> str:
    rel = _normalize_repo_relative_path(repo_relative_path)
    return f"https://raw.githubusercontent.com/{owner_repo}/{branch}/{rel.as_posix()}"


def media_github_url(repo_relative_path: Path | str, *, owner_repo: str = DEFAULT_OWNER_REPO, branch: str = DEFAULT_BRANCH) -> str:
    rel = _normalize_repo_relative_path(repo_relative_path)
    return f"https://media.githubusercontent.com/media/{owner_repo}/{branch}/{rel.as_posix()}"


@lru_cache(maxsize=128)
def _compile_gitattributes_pattern(pattern: str) -> re.Pattern[str]:
    regex_parts: list[str] = ["^"]
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            regex_parts.append("(?:.*/)?")
            i += 3
            continue
        if pattern.startswith("**", i):
            regex_parts.append(".*")
            i += 2
            continue

        char = pattern[i]
        if char == "*":
            regex_parts.append("[^/]*")
        elif char == "?":
            regex_parts.append("[^/]")
        else:
            regex_parts.append(re.escape(char))
        i += 1
    regex_parts.append("$")
    return re.compile("".join(regex_parts))


def _match_gitattributes_pattern(path: PurePosixPath, pattern: str) -> bool:
    candidate = path.as_posix()
    return _compile_gitattributes_pattern(pattern).match(candidate) is not None


@lru_cache(maxsize=8)
def _load_filter_rules(repo_root_str: str) -> tuple[_FilterRule, ...]:
    root = Path(repo_root_str)
    gitattributes_path = root / ".gitattributes"
    if not gitattributes_path.exists():
        return ()

    rules: list[_FilterRule] = []
    for raw_line in gitattributes_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        pattern = parts[0]
        filter_value: str | None | object = ...
        for token in parts[1:]:
            if token == "!filter" or token == "-filter":
                filter_value = None
            elif token.startswith("filter="):
                filter_value = token.split("=", 1)[1]
        if filter_value is ...:
            continue
        rules.append(_FilterRule(pattern=pattern, filter_value=filter_value))
    return tuple(rules)


def _lookup_filter_from_gitattributes(repo_relative_path: PurePosixPath, *, repo_root: Path | None = None) -> str | None:
    root = (repo_root or default_repo_root()).resolve()
    value: str | None = None
    for rule in _load_filter_rules(str(root)):
        if _match_gitattributes_pattern(repo_relative_path, rule.pattern):
            value = rule.filter_value
    return value


def _lookup_filter_from_git(repo_relative_path: PurePosixPath, *, repo_root: Path | None = None) -> str | None:
    root = (repo_root or default_repo_root()).resolve()
    try:
        completed = subprocess.run(
            ["git", "check-attr", "filter", "--", repo_relative_path.as_posix()],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None

    output = completed.stdout.strip()
    if not output or ": filter:" not in output:
        return None
    value = output.rsplit(": filter:", 1)[1].strip()
    if value in {"", "unspecified", "unset"}:
        return None
    return value


def parse_git_lfs_pointer(path: Path) -> dict[str, str] | None:
    if not path.exists() or path.stat().st_size > 1024:
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


def classify_download_kind(
    repo_relative_path: Path | str,
    *,
    repo_root: Path | None = None,
    part_repo_relative_paths: Iterable[Path | str] | None = None,
) -> DownloadKind:
    if part_repo_relative_paths:
        return DownloadKind.MULTIPART

    root = (repo_root or default_repo_root()).resolve()
    rel_path = _normalize_repo_relative_path(repo_relative_path, repo_root=root)

    filter_value = _lookup_filter_from_gitattributes(rel_path, repo_root=root)
    if filter_value is None:
        filter_value = _lookup_filter_from_git(rel_path, repo_root=root)
    if filter_value == "lfs":
        return DownloadKind.LFS

    local_path = root / Path(rel_path.as_posix())
    if parse_git_lfs_pointer(local_path) is not None:
        return DownloadKind.LFS
    return DownloadKind.RAW


def build_download_descriptor(
    repo_relative_path: Path | str,
    *,
    repo_root: Path | None = None,
    owner_repo: str = DEFAULT_OWNER_REPO,
    branch: str = DEFAULT_BRANCH,
    part_repo_relative_paths: Iterable[Path | str] | None = None,
) -> DownloadDescriptor:
    root = (repo_root or default_repo_root()).resolve()
    rel_path = _normalize_repo_relative_path(repo_relative_path, repo_root=root)
    part_paths = tuple(_normalize_repo_relative_path(path, repo_root=root) for path in (part_repo_relative_paths or ()))

    kind = classify_download_kind(rel_path, repo_root=root, part_repo_relative_paths=part_paths)
    if kind == DownloadKind.LFS:
        href = media_github_url(rel_path, owner_repo=owner_repo, branch=branch)
        return DownloadDescriptor(kind=kind, href=href, parts=())
    if kind == DownloadKind.RAW:
        href = raw_github_url(rel_path, owner_repo=owner_repo, branch=branch)
        return DownloadDescriptor(kind=kind, href=href, parts=())

    archive_kind = classify_download_kind(rel_path, repo_root=root)
    href = (
        media_github_url(rel_path, owner_repo=owner_repo, branch=branch)
        if archive_kind == DownloadKind.LFS
        else raw_github_url(rel_path, owner_repo=owner_repo, branch=branch)
    )
    parts: list[DownloadSource] = []
    for part_path in part_paths:
        part_kind = classify_download_kind(part_path, repo_root=root)
        part_href = (
            media_github_url(part_path, owner_repo=owner_repo, branch=branch)
            if part_kind == DownloadKind.LFS
            else raw_github_url(part_path, owner_repo=owner_repo, branch=branch)
        )
        parts.append(DownloadSource(href=part_href, kind=part_kind))
    return DownloadDescriptor(kind=kind, href=href, parts=tuple(parts))


def build_asset_download_descriptor(
    asset_path: Path | str,
    *,
    repo_root: Path | None = None,
    owner_repo: str = DEFAULT_OWNER_REPO,
    branch: str = DEFAULT_BRANCH,
    part_asset_paths: Iterable[Path | str] | None = None,
) -> DownloadDescriptor:
    root = (repo_root or default_repo_root()).resolve()
    rel_path = asset_repo_relative_path(asset_path, repo_root=root)
    part_paths = tuple(asset_repo_relative_path(path, repo_root=root) for path in (part_asset_paths or ()))
    return build_download_descriptor(
        rel_path,
        repo_root=root,
        owner_repo=owner_repo,
        branch=branch,
        part_repo_relative_paths=part_paths,
    )


def asset_repo_relative_path_from_url(url: str, *, owner_repo: str = DEFAULT_OWNER_REPO, branch: str = DEFAULT_BRANCH) -> PurePosixPath | None:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if hostname not in {"raw.githubusercontent.com", "media.githubusercontent.com"}:
        return None
    path = parsed.path
    raw_prefix = f"/{owner_repo}/{branch}/"
    media_prefix = f"/media/{owner_repo}/{branch}/"
    for prefix in (raw_prefix, media_prefix):
        if path.startswith(prefix):
            return PurePosixPath(path[len(prefix):].lstrip("/"))
    return None
