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

    install_parser = subparsers.add_parser("install", help="Install an archive that contains the requested tool.")
    install_parser.add_argument("tool", help="Exact tool name such as llvm-pdbutil.")
    install_parser.add_argument("--platform", default=None, help="Filter by platform.")
    install_parser.add_argument("--arch", default=None, help="Filter by architecture.")
    install_parser.add_argument("--version", default=None, help="Filter by version.")
    install_parser.add_argument("--component", default=None, help="Filter by component family.")
    install_parser.add_argument("--all", action="store_true", help="Install every matching archive.")
    install_parser.add_argument("--home-dir", type=Path, default=None, help="Override the local install/cache root.")
    install_parser.add_argument("--index", type=Path, default=None, help="Override the aggregate index path.")

    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "query":
        return query.main(
            [
                *args.patterns,
                *(["--platform", args.platform] if args.platform else []),
                *(["--arch", args.arch] if args.arch else []),
                *(["--version", args.version] if args.version else []),
                *(["--component", args.component] if args.component else []),
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
                *(["--home-dir", str(args.home_dir)] if args.home_dir else []),
                *(["--index", str(args.index)] if args.index else []),
            ]
        )

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
