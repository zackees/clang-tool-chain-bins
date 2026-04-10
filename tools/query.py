from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .archive_index import aggregate_index_path
from .common import get_cache_path, get_home_dir, get_install_dir

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


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


def _load_aggregate_records(index_path: Path | None = None) -> list[ToolRecord]:
    path = index_path or aggregate_index_path()
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return [ToolRecord(**entry) for entry in data.get("tools", [])]


def _record_matches(pattern: str, record: ToolRecord) -> bool:
    pattern_lower = pattern.lower()
    candidates = (
        record.tool_name.lower(),
        record.file_name.lower(),
        record.component.lower(),
        record.archive_filename.lower(),
        record.path_in_archive.lower(),
    )
    return any(fnmatchcase(candidate, pattern_lower) for candidate in candidates)


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
            match["url"] = record.archive_url
            matches.append(match)

        output.append({"query": pattern, "matches": matches})

    return output


def format_query_results(results: Iterable[dict[str, Any]]) -> str:
    return "\n".join(json.dumps(result, sort_keys=True) for result in results)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Query the clang-tool-chain-bins aggregate tool index.")
    parser.add_argument("patterns", nargs="+", help='One or more glob-style patterns such as "clang*" or "llvm-*".')
    parser.add_argument("--platform", default=None, help="Filter by platform.")
    parser.add_argument("--arch", default=None, help="Filter by architecture.")
    parser.add_argument("--version", default=None, help="Filter by version.")
    parser.add_argument("--component", default=None, help="Filter by component family.")
    parser.add_argument("--home-dir", type=Path, default=None, help="Override the local install/cache root.")
    parser.add_argument("--index", type=Path, default=None, help="Override the aggregate index path.")
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
    output = format_query_results(results)
    if output:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
