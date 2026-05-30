"""CLI shims that delegate to the Rust `ctcb` binary."""

import os
import subprocess
import sys
from pathlib import Path


def _binary_name() -> str:
    return "ctcb.exe" if sys.platform == "win32" else "ctcb"


def _find_binary() -> Path:
    """Find the ctcb binary: check alongside Python executable first, then PATH."""
    exe_dir = Path(sys.executable).parent
    candidate = exe_dir / _binary_name()
    if candidate.exists():
        return candidate

    # Search PATH
    for path_dir in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(path_dir) / _binary_name()
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        f"Could not find {_binary_name()}. "
        "Install with: cargo install --path crates/ctcb-cli"
    )


def _run(subcommand: str) -> None:
    """Run a ctcb subcommand, forwarding all arguments."""
    binary = _find_binary()
    result = subprocess.run(
        [str(binary), subcommand, *sys.argv[1:]],
        check=False,
    )
    sys.exit(result.returncode)


def fetch_and_archive() -> None:
    _run("fetch")

def download_binaries() -> None:
    _run("download")

def strip_binaries() -> None:
    _run("strip")

def deduplicate_binaries() -> None:
    _run("dedup")

def create_hardlink_archive() -> None:
    _run("hardlink-archive")

def expand_archive() -> None:
    _run("expand")

def test_compression() -> None:
    _run("bench-compression")

def create_iwyu_archives() -> None:
    _run("iwyu")

def extract_mingw_sysroot() -> None:
    _run("mingw-sysroot")

def emscripten() -> None:
    _run("emscripten")

def emscripten_docker() -> None:
    _run("emscripten-docker")

def nodejs() -> None:
    _run("nodejs")
