from __future__ import annotations

import argparse
import os
import shlex
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


def _run(command: Sequence[str], *, env: dict[str, str] | None = None) -> None:
    print(f"  $ {shlex.join(list(command))}")
    subprocess.run(list(command), check=True, env=env)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build and publish clang-tool-chain-bins to PyPI or TestPyPI.\n\n"
        "For cross-platform builds, use the `./publish` script which triggers\n"
        "remote CI builds. This script builds a LOCAL wheel only (current platform).",
    )
    parser.add_argument("--testpypi", action="store_true", help="Publish to TestPyPI instead of PyPI.")
    parser.add_argument("--skip-upload", action="store_true", help="Build and validate but do not upload.")
    parser.add_argument("--token-env", default="PYPI_TOKEN", help="Environment variable containing the PyPI token.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    token = os.environ.get(args.token_env)

    dist_dir = Path("dist")
    if dist_dir.exists():
        for path in dist_dir.iterdir():
            if path.is_file():
                path.unlink()

    # Build wheel with maturin (includes Rust native extension)
    _run(["uv", "run", "maturin", "build", "--release", "--out", "dist"])

    # Build sdist
    _run(["uv", "run", "maturin", "sdist", "--out", "dist"])

    # List built artifacts
    print("\nBuilt artifacts:")
    for path in sorted(dist_dir.iterdir()):
        print(f"  {path.name} ({path.stat().st_size / 1024:.0f} KB)")

    if args.skip_upload:
        return 0

    upload_env = os.environ.copy()
    if token:
        upload_env["TWINE_USERNAME"] = "__token__"
        upload_env["TWINE_PASSWORD"] = token
    else:
        print(f"\n{args.token_env} is not set; relying on uv publish config or keyring.")

    command = ["uv", "publish"]
    if args.testpypi:
        command.extend(["--publish-url", "https://test.pypi.org/legacy/"])

    print(f"\nRunning: {shlex.join(command)}")
    _run(command, env=upload_env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
