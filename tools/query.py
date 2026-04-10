from __future__ import annotations

import argparse
import json
import os
import subprocess
import urllib.request
from dataclasses import asdict, dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .archive_index import aggregate_index_path
from .common import get_cache_path, get_home_dir, get_install_dir
from .json_utils import load, load_path

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


REMOTE_INDEX_URL_ENV = "CLANG_TOOL_CHAIN_BINS_INDEX_URL"
_REMOTE_INDEX_ATTEMPTED = False
_REMOTE_INDEX_PAYLOAD: dict[str, Any] | None = None


@dataclass(frozen=True)
class ToolRecord:
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


def _reset_remote_index_cache() -> None:
    global _REMOTE_INDEX_ATTEMPTED, _REMOTE_INDEX_PAYLOAD
    _REMOTE_INDEX_ATTEMPTED = False
    _REMOTE_INDEX_PAYLOAD = None


def _git_output(repo_root: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    output = completed.stdout.strip()
    return output or None


def _github_raw_url_from_remote(remote_url: str, branch: str) -> str | None:
    normalized = remote_url.strip()
    if normalized.endswith(".git"):
        normalized = normalized[:-4]

    owner_repo: str | None = None
    if normalized.startswith("git@github.com:"):
        owner_repo = normalized.split("git@github.com:", 1)[1]
    elif normalized.startswith("https://github.com/"):
        owner_repo = normalized.split("https://github.com/", 1)[1]

    if not owner_repo or "/" not in owner_repo:
        return None
    return f"https://raw.githubusercontent.com/{owner_repo}/{branch}/tools/data/tool-index.json"


def _discover_remote_index_url() -> str | None:
    override = os.environ.get(REMOTE_INDEX_URL_ENV)
    if override:
        return override

    repo_root = Path(__file__).resolve().parents[1]
    remote_url = _git_output(repo_root, "config", "--get", "remote.origin.url")
    if remote_url is None:
        return None

    branch = _git_output(repo_root, "rev-parse", "--abbrev-ref", "HEAD") or "main"
    if branch == "HEAD":
        branch = "main"
    return _github_raw_url_from_remote(remote_url, branch)


def _load_remote_index_payload() -> dict[str, Any] | None:
    global _REMOTE_INDEX_ATTEMPTED, _REMOTE_INDEX_PAYLOAD
    if _REMOTE_INDEX_ATTEMPTED:
        return _REMOTE_INDEX_PAYLOAD

    _REMOTE_INDEX_ATTEMPTED = True
    url = _discover_remote_index_url()
    if url is None:
        return None

    try:
        with urllib.request.urlopen(url) as response:
            payload = load(response)
    except Exception:
        return None

    if not isinstance(payload, dict) or not isinstance(payload.get("tools"), list):
        return None

    _REMOTE_INDEX_PAYLOAD = payload
    return _REMOTE_INDEX_PAYLOAD


def _load_aggregate_records(index_path: Path | None = None) -> list[ToolRecord]:
    data: dict[str, Any] | None = None
    if index_path is None:
        data = _load_remote_index_payload()

    if data is None:
        path = index_path or aggregate_index_path()
        data = load_path(path)
    return [ToolRecord(**entry) for entry in data.get("tools", [])]


def _has_glob(pattern: str) -> bool:
    return any(char in pattern for char in "*?[")


def _record_matches(pattern: str, record: ToolRecord) -> bool:
    pattern_lower = pattern.lower()
    candidates = (
        record.tool_name.lower(),
        record.file_name.lower(),
    )
    if _has_glob(pattern):
        return any(fnmatchcase(candidate, pattern_lower) for candidate in candidates)
    return pattern_lower in candidates


def _record_passes_filters(
    record: ToolRecord,
    *,
    platform: str | None,
    arch: str | None,
    version: str | None,
    component: str | None,
) -> bool:
    if platform and record.platform != platform:
        return False
    if arch and record.arch != arch:
        return False
    if version and record.version != version:
        return False
    return not (component and record.component != component)


def _record_source_urls(record: ToolRecord) -> list[str]:
    part_urls = [part["href"] for part in record.parts if isinstance(part, dict) and isinstance(part.get("href"), str)]
    if part_urls:
        return part_urls
    if record.archive_url:
        return [record.archive_url]
    if record.probe_urls:
        return [url for url in record.probe_urls if isinstance(url, str)]
    return []


def _match_source_urls(match: dict[str, Any]) -> list[str]:
    source_urls = match.get("source_urls")
    if isinstance(source_urls, list):
        urls = [url for url in source_urls if isinstance(url, str)]
        if urls:
            return urls

    parts = match.get("parts")
    if isinstance(parts, list):
        part_urls = [part["href"] for part in parts if isinstance(part, dict) and isinstance(part.get("href"), str)]
        if part_urls:
            return part_urls

    archive_url = match.get("archive_url")
    if isinstance(archive_url, str) and archive_url:
        return [archive_url]

    probe_urls = match.get("probe_urls")
    if isinstance(probe_urls, list):
        return [url for url in probe_urls if isinstance(url, str)]
    return []


def query_records(
    patterns: Sequence[str],
    *,
    home_dir: Path | None = None,
    records: Sequence[ToolRecord] | None = None,
    index_path: Path | None = None,
    platform: str | None = None,
    arch: str | None = None,
    version: str | None = None,
    component: str | None = None,
) -> list[dict[str, Any]]:
    resolved_home = (home_dir or get_home_dir()).expanduser().resolve()
    loaded_records = list(records) if records is not None else _load_aggregate_records(index_path)
    output: list[dict[str, Any]] = []

    for pattern in patterns:
        matches: list[dict[str, Any]] = []
        for record in loaded_records:
            if not _record_matches(pattern, record):
                continue
            if not _record_passes_filters(
                record,
                platform=platform,
                arch=arch,
                version=version,
                component=component,
            ):
                continue

            install_dir = get_install_dir(record.component, record.platform, record.arch, resolved_home)
            match = asdict(record)
            match["local_cache_path"] = str(
                get_cache_path(record.component, record.platform, record.arch, record.archive_sha256, resolved_home)
            )
            match["install_path"] = str(install_dir)
            match["installed"] = (install_dir / "done.txt").exists() or install_dir.exists()
            match["source_urls"] = _record_source_urls(record)
            match["url"] = record.archive_url
            matches.append(match)

        output.append({"query": pattern, "matches": matches})

    return output


def format_query_results(results: Iterable[dict[str, Any]]) -> str:
    lines: list[str] = []
    for result in results:
        query_name = result["query"]
        matches = list(result["matches"])
        if not matches:
            lines.append(json.dumps({"query": query_name, "matched": False}, sort_keys=True))
            continue
        for match in matches:
            lines.append(json.dumps({"query": query_name, **match}, sort_keys=True))
    return "\n".join(lines)


def _format_size(size: int) -> str:
    units = ["B", "KiB", "MiB", "GiB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _format_pretty_table(rows: list[list[str]]) -> str:
    widths = [max(len(row[index]) for row in rows) for index in range(len(rows[0]))]
    return "\n".join("  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)).rstrip() for row in rows)


def format_pretty_results(results: Iterable[dict[str, Any]]) -> str:
    sections: list[str] = []

    for result in results:
        query_name = result["query"]
        matches = list(result["matches"])
        header = f"Query: {query_name}"
        if not matches:
            sections.append(f"{header}\nNo matches.")
            continue

        table_rows = [["Tool", "Installed", "Version", "Size", "Platform", "Arch"]]
        for match in matches:
            table_rows.append(
                [
                    match["tool_name"],
                    "yes" if match["installed"] else "no",
                    match.get("version") or "-",
                    _format_size(int(match["size"])),
                    match.get("platform") or "-",
                    match.get("arch") or "-",
                ]
            )

        section_lines = [header, _format_pretty_table(table_rows)]

        section_lines.append("")
        section_lines.append("Match Details")
        for match in matches:
            source_urls = _match_source_urls(match)
            section_lines.append(
                f"{match['tool_name']} ({match.get('version') or '-'}, {match.get('platform') or '-'}, {match.get('arch') or '-'})"
            )
            section_lines.append(f"  Archive: {match.get('archive_filename') or '-'}")
            section_lines.append(f"  Path In Archive: {match.get('path_in_archive') or match.get('file_name') or '-'}")
            section_lines.append(f"  Install Path: {match.get('install_path') or '-'}")
            if len(source_urls) <= 1:
                section_lines.append(f"  Source URL: {source_urls[0] if source_urls else '-'}")
            else:
                section_lines.append(f"  Source URLs ({len(source_urls)}):")
                for url in source_urls:
                    section_lines.append(f"    {url}")
            section_lines.append("")
        if section_lines[-1] == "":
            section_lines.pop()

        installed_matches = [match for match in matches if match["installed"]]
        if installed_matches:
            section_lines.append("")
            section_lines.append("Installed Matches")
            grouped: dict[str, list[dict[str, Any]]] = {}
            for match in installed_matches:
                grouped.setdefault(match["install_path"], []).append(match)
            for install_path in sorted(grouped):
                section_lines.append(install_path)
                entries = sorted(grouped[install_path], key=lambda entry: (entry["tool_name"], entry.get("version") or ""))
                for entry in entries:
                    section_lines.append(
                        f"|-- {entry['tool_name']} ({entry.get('version') or '-'}, {_format_size(int(entry['size']))})"
                    )

        sections.append("\n".join(section_lines))

    return "\n\n".join(sections)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Query the clang-tool-chain-bins aggregate tool index.")
    parser.add_argument("patterns", nargs="+", help='One or more glob-style patterns such as "clang*" or "llvm-*".')
    parser.add_argument("--platform", default=None, help="Filter by platform.")
    parser.add_argument("--arch", default=None, help="Filter by architecture.")
    parser.add_argument("--version", default=None, help="Filter by version.")
    parser.add_argument("--component", default=None, help="Filter by component family.")
    parser.add_argument("--home-dir", type=Path, default=None, help="Override the local install/cache root.")
    parser.add_argument("--index", type=Path, default=None, help="Override the aggregate index path.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print query results instead of JSON Lines.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    results = query_records(
        args.patterns,
        home_dir=args.home_dir,
        index_path=args.index,
        platform=args.platform,
        arch=args.arch,
        version=args.version,
        component=args.component,
    )
    output = format_pretty_results(results) if args.pretty else format_query_results(results)
    if output:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
