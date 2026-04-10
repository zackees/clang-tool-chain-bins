from __future__ import annotations

import hashlib
import os
from pathlib import Path

EXECUTABLE_EXTENSIONS = {".exe", ".bat", ".cmd", ".ps1", ".sh", ".py"}
NON_TOOL_EXTENSIONS = {
    ".a",
    ".bc",
    ".c",
    ".cc",
    ".cfg",
    ".cmake",
    ".cpp",
    ".def",
    ".dll",
    ".dylib",
    ".h",
    ".hpp",
    ".inc",
    ".json",
    ".lib",
    ".md",
    ".o",
    ".obj",
    ".pdb",
    ".pyc",
    ".rst",
    ".so",
    ".txt",
    ".yaml",
    ".yml",
}


def get_home_dir() -> Path:
    override = os.environ.get("CLANG_TOOL_CHAIN_DOWNLOAD_PATH")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".clang-tool-chain"


def get_cache_path(component: str, platform: str | None, arch: str | None, sha256: str, home_dir: Path | None = None) -> Path:
    base = (home_dir or get_home_dir()).expanduser().resolve()
    cache_dir = base / "archives"
    platform_name = platform or "universal"
    arch_name = arch or "universal"
    return cache_dir / f"{component}-{platform_name}-{arch_name}-{sha256[:16]}.tar.zst"


def get_install_dir(component: str, platform: str | None, arch: str | None, home_dir: Path | None = None) -> Path:
    base = (home_dir or get_home_dir()).expanduser().resolve()
    component_dir = component.replace("-", "_")
    if platform and arch:
        return base / component_dir / platform / arch
    return base / component_dir / "universal"


def get_lock_path(component: str, platform: str | None, arch: str | None, home_dir: Path | None = None) -> Path:
    base = (home_dir or get_home_dir()).expanduser().resolve()
    base.mkdir(parents=True, exist_ok=True)
    platform_name = platform or "universal"
    arch_name = arch or "universal"
    return base / f"{component}-{platform_name}-{arch_name}.lock"


def normalize_tool_name(filename: str) -> str:
    path = Path(filename)
    suffix = path.suffix.lower()
    if suffix in EXECUTABLE_EXTENSIONS:
        return path.stem
    return path.name


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
