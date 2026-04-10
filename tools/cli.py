from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING

from . import install, query

if TYPE_CHECKING:
    from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="clang-tool-chain-bins")
    subparsers = parser.add_subparsers(dest="command")

    query_parser = subparsers.add_parser("query", help="Query available archives and cache/install status.")
    query_parser.add_argument(
        "patterns",
        nargs="+",
        help='One or more glob-style patterns such as "clang*" or "llvm-*".',
    )
    query_parser.add_argument("--platform", default=None, help="Filter by platform.")
    query_parser.add_argument("--arch", default=None, help="Filter by architecture.")
    query_parser.add_argument("--version", default=None, help="Filter by version.")
    query_parser.add_argument("--component", default=None, help="Filter by component family.")
    query_parser.add_argument("--home-dir", type=Path, default=None, help="Override the local install/cache root.")
    query_parser.add_argument("--index", type=Path, default=None, help="Override the aggregate index path.")
    query_parser.add_argument("--pretty", action="store_true", help="Pretty-print results instead of JSON Lines.")

    install_parser = subparsers.add_parser("install", help="Install an archive that contains the requested tool.")
    install_parser.add_argument("tool", help="Exact tool name such as llvm-pdbutil.")
    install_parser.add_argument("--platform", default=None, help="Filter by platform.")
    install_parser.add_argument("--arch", default=None, help="Filter by architecture.")
    install_parser.add_argument("--version", default=None, help="Filter by version.")
    install_parser.add_argument("--component", default=None, help="Filter by component family.")
    install_parser.add_argument("--all", action="store_true", help="Install every matching archive.")
    install_parser.add_argument("--dry-run", action="store_true", help="Print the install plan without modifying disk.")
    install_parser.add_argument("--home-dir", type=Path, default=None, help="Override the local install/cache root.")
    install_parser.add_argument("--index", type=Path, default=None, help="Override the aggregate index path.")

    ensure_parser = subparsers.add_parser("ensure", help="Ensure an archive containing the requested tool is installed.")
    ensure_parser.add_argument("tool", help="Exact tool name such as llvm-pdbutil.")
    ensure_parser.add_argument("--platform", default=None, help="Filter by platform.")
    ensure_parser.add_argument("--arch", default=None, help="Filter by architecture.")
    ensure_parser.add_argument("--version", default=None, help="Filter by version.")
    ensure_parser.add_argument("--component", default=None, help="Filter by component family.")
    ensure_parser.add_argument("--all", action="store_true", help="Operate on every matching archive.")
    ensure_parser.add_argument("--dry-run", action="store_true", help="Print the install plan without modifying disk.")
    ensure_parser.add_argument("--home-dir", type=Path, default=None, help="Override the local install/cache root.")
    ensure_parser.add_argument("--index", type=Path, default=None, help="Override the aggregate index path.")

    tryinstall_parser = subparsers.add_parser(
        "tryinstall",
        help="Try a non-blocking install; returns immediately if the target is already installed or locked.",
    )
    tryinstall_parser.add_argument("tool", help="Exact tool name such as llvm-pdbutil.")
    tryinstall_parser.add_argument("--platform", default=None, help="Filter by platform.")
    tryinstall_parser.add_argument("--arch", default=None, help="Filter by architecture.")
    tryinstall_parser.add_argument("--version", default=None, help="Filter by version.")
    tryinstall_parser.add_argument("--component", default=None, help="Filter by component family.")
    tryinstall_parser.add_argument("--all", action="store_true", help="Operate on every matching archive.")
    tryinstall_parser.add_argument("--dry-run", action="store_true", help="Print the install plan without modifying disk.")
    tryinstall_parser.add_argument("--home-dir", type=Path, default=None, help="Override the local install/cache root.")
    tryinstall_parser.add_argument("--index", type=Path, default=None, help="Override the aggregate index path.")

    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "query":
        return query.main(
            [
                *args.patterns,
                *(["--platform", args.platform] if args.platform else []),
                *(["--arch", args.arch] if args.arch else []),
                *(["--version", args.version] if args.version else []),
                *(["--component", args.component] if args.component else []),
                *(["--pretty"] if args.pretty else []),
                *(["--home-dir", str(args.home_dir)] if args.home_dir else []),
                *(["--index", str(args.index)] if args.index else []),
            ]
        )
    if args.command == "install":
        return install.main(
            [
                args.tool,
                *(["--platform", args.platform] if args.platform else []),
                *(["--arch", args.arch] if args.arch else []),
                *(["--version", args.version] if args.version else []),
                *(["--component", args.component] if args.component else []),
                *(["--all"] if args.all else []),
                *(["--dry-run"] if args.dry_run else []),
                *(["--home-dir", str(args.home_dir)] if args.home_dir else []),
                *(["--index", str(args.index)] if args.index else []),
            ],
            operation="install",
        )
    if args.command == "ensure":
        return install.main(
            [
                args.tool,
                *(["--platform", args.platform] if args.platform else []),
                *(["--arch", args.arch] if args.arch else []),
                *(["--version", args.version] if args.version else []),
                *(["--component", args.component] if args.component else []),
                *(["--all"] if args.all else []),
                *(["--dry-run"] if args.dry_run else []),
                *(["--home-dir", str(args.home_dir)] if args.home_dir else []),
                *(["--index", str(args.index)] if args.index else []),
            ],
            operation="ensure",
        )
    if args.command == "tryinstall":
        return install.main(
            [
                args.tool,
                *(["--platform", args.platform] if args.platform else []),
                *(["--arch", args.arch] if args.arch else []),
                *(["--version", args.version] if args.version else []),
                *(["--component", args.component] if args.component else []),
                *(["--all"] if args.all else []),
                *(["--dry-run"] if args.dry_run else []),
                *(["--home-dir", str(args.home_dir)] if args.home_dir else []),
                *(["--index", str(args.index)] if args.index else []),
            ],
            operation="tryinstall",
        )

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
