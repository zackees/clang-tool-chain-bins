"""Workflow-only release helpers for ctcb (PyPI + crates.io)."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ci.release_checks import (
    RUST_PUBLISH_ORDER,
    ReleaseCheckError,
    read_pyproject_version,
    read_workspace_version,
    stamp_internal_dependency_versions,
    validate_release_metadata,
)

ROOT = Path(__file__).resolve().parent.parent
PYPI_PROJECT_NAME = "clang-tool-chain-bins"


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    log(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, check=True, **kwargs)


def write_github_output(values: dict[str, str]) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as f:
        for key, value in values.items():
            f.write(f"{key}={value}\n")


def pypi_version_exists(name: str, version: str) -> bool:
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
        files = data.get("urls") or []
        return len(files) > 0
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        log(f"  WARNING: PyPI lookup for {name} {version} returned HTTP {e.code}; treating as not-published")
        return False
    except (urllib.error.URLError, TimeoutError) as e:
        log(f"  WARNING: Could not reach PyPI ({e}); treating as not-published")
        return False


def crate_version_exists(name: str, version: str) -> bool:
    url = f"https://crates.io/api/v1/crates/{name}/{version}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            json.loads(resp.read())
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise


def check_pypi(version: str) -> bool:
    log(f"\n=== Pre-check PyPI for {PYPI_PROJECT_NAME} {version} ===")
    if pypi_version_exists(PYPI_PROJECT_NAME, version):
        log(f"  EXISTS: {PYPI_PROJECT_NAME} {version} already on PyPI; publish-pypi will be skipped")
        return True
    log(f"  OK: {PYPI_PROJECT_NAME} {version} is available")
    return False


def check_crates(version: str) -> set[str]:
    log(f"\n=== Pre-check crates.io for ctcb crates {version} ===")
    existing: list[str] = []
    for crate in RUST_PUBLISH_ORDER:
        try:
            if crate_version_exists(crate, version):
                existing.append(crate)
                log(f"  EXISTS: {crate} {version}")
            else:
                log(f"  OK: {crate} {version} is available")
        except (urllib.error.URLError, TimeoutError) as e:
            log(f"  WARNING: could not reach crates.io for {crate} ({e})")

    if len(existing) == len(RUST_PUBLISH_ORDER):
        log("  All crates already published; publish-crates will be skipped")
    elif existing:
        log("  Resuming partial crates.io release; published crates will be skipped per-crate")

    return set(existing)


def verify_crate_visible(crate: str, version: str) -> None:
    url = f"https://crates.io/api/v1/crates/{crate}/{version}"
    timeout = 300
    interval = 10
    start = time.time()

    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read())
            if (data.get("version") or {}).get("num") == version:
                return
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
            pass
        time.sleep(interval)

    raise SystemExit(f"ERROR: timed out waiting for {crate} {version} on crates.io")


def publish_rust_crates(version: str, existing_crates: set[str]) -> None:
    try:
        validate_release_metadata()
        stamped = stamp_internal_dependency_versions()
    except ReleaseCheckError as e:
        raise SystemExit(f"ERROR: {e}") from e

    if stamped:
        log(
            f"  Stamped exact internal dep versions in {len(stamped)} manifest(s)"
        )

    for crate in RUST_PUBLISH_ORDER:
        if crate in existing_crates:
            log(f"  Skipping {crate} {version}; already published")
            continue
        run(
            ["cargo", "publish", "--allow-dirty", "--no-verify", "-p", crate],
            cwd=ROOT,
        )
        verify_crate_visible(crate, version)


def command_check_registries(_: argparse.Namespace) -> None:
    workspace_version = read_workspace_version()
    pyproject_version = read_pyproject_version()
    if pyproject_version != workspace_version:
        raise SystemExit(
            f"ERROR: pyproject.toml version {pyproject_version} != "
            f"Cargo.toml workspace version {workspace_version}"
        )
    pypi_complete = check_pypi(workspace_version)
    existing_crates = check_crates(workspace_version)
    crates_complete = len(existing_crates) == len(RUST_PUBLISH_ORDER)
    write_github_output(
        {
            "pypi_complete": str(pypi_complete).lower(),
            "crates_complete": str(crates_complete).lower(),
        }
    )
    log(
        "\n=== Registry publish plan ===\n"
        f"  PyPI: {'skip' if pypi_complete else 'publish'}\n"
        f"  crates.io: {'skip' if crates_complete else 'publish missing crates'}"
    )


def command_publish_crates(_: argparse.Namespace) -> None:
    if not os.environ.get("CARGO_REGISTRY_TOKEN"):
        raise SystemExit("ERROR: CARGO_REGISTRY_TOKEN is required for crates.io publish")
    version = read_workspace_version()
    existing = check_crates(version)
    publish_rust_crates(version, existing)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Workflow-only release helpers for ctcb.")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser(
        "check-registries",
        help="Report which registries still need publishing for the current version.",
    )
    check.set_defaults(func=command_check_registries)

    publish = sub.add_parser(
        "publish-crates",
        help="Publish workspace crates in dependency order, skipping existing versions.",
    )
    publish.set_defaults(func=command_publish_crates)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
