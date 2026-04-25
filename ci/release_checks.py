"""Shared release metadata validation for ctcb."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent.resolve()

RUST_PUBLISH_ORDER = [
    "ctcb-core",
    "ctcb-checksum",
    "ctcb-archive",
    "ctcb-dedup",
    "ctcb-download",
    "ctcb-strip",
    "ctcb-manifest",
    "ctcb-split",
    "ctcb-cli",
]

INTERNAL_CRATE_PREFIX = "ctcb-"

INLINE_DEPENDENCY_RE = re.compile(
    r'(?P<lead>(?:^|\n)(?P<name>ctcb-[A-Za-z0-9_-]+)\s*=\s*\{[^{}\n]*?version\s*=\s*")'
    r'(?P<version>[^"]+)'
    r'(?P<trail>")',
)


class ReleaseCheckError(RuntimeError):
    """Raised when release metadata is inconsistent."""


def _read_toml(path: Path) -> dict[str, Any]:
    with open(path, "rb") as f:
        return tomllib.load(f)


def read_workspace_version() -> str:
    data = _read_toml(ROOT / "Cargo.toml")
    return data["workspace"]["package"]["version"]


def read_pyproject_version() -> str:
    data = _read_toml(ROOT / "pyproject.toml")
    return data["project"]["version"]


def crate_manifest_paths() -> list[Path]:
    return [ROOT / "crates" / crate / "Cargo.toml" for crate in RUST_PUBLISH_ORDER]


def _strip_exact(version: str) -> str:
    return version.lstrip("=").strip()


def stamp_internal_dependency_versions(
    manifest_paths: list[Path] | None = None,
) -> list[Path]:
    """Rewrite internal `ctcb-* = { path, version = "X.Y.Z" }` to use `=X.Y.Z`.

    crates.io requires every published crate to pin its internal deps to a
    single resolvable version. Local dev manifests use caret ranges (`"0.4.1"`)
    so workspace builds resolve to the path dep; CI calls this helper in its
    disposable checkout before `cargo publish` so consumers cannot drift across
    patch versions.
    """

    paths = manifest_paths or crate_manifest_paths()
    expected = read_workspace_version()
    expected_pinned = f"={expected}"
    rewritten: list[Path] = []

    for manifest in paths:
        if not manifest.exists():
            raise ReleaseCheckError(f"Missing crate manifest: {manifest}")
        text = manifest.read_text(encoding="utf-8")

        def stamp(match: re.Match[str]) -> str:
            literal = _strip_exact(match.group("version"))
            if literal != expected:
                raise ReleaseCheckError(
                    f"{manifest.relative_to(ROOT)} pins {match.group('name')} "
                    f"to version {match.group('version')!r}, expected {expected!r}"
                )
            return f'{match.group("lead")}{expected_pinned}{match.group("trail")}'

        new_text = INLINE_DEPENDENCY_RE.sub(stamp, text)
        if new_text != text:
            manifest.write_text(new_text, encoding="utf-8")
            rewritten.append(manifest)

    return rewritten


def validate_release_versions() -> None:
    workspace_version = read_workspace_version()
    pyproject_version = read_pyproject_version()
    errors: list[str] = []

    if pyproject_version != workspace_version:
        errors.append(
            f"pyproject.toml version {pyproject_version!r} does not match "
            f"Cargo.toml workspace version {workspace_version!r}"
        )

    for manifest in crate_manifest_paths():
        if not manifest.exists():
            errors.append(f"Missing crate manifest: {manifest.relative_to(ROOT)}")
            continue
        text = manifest.read_text(encoding="utf-8")
        for match in INLINE_DEPENDENCY_RE.finditer(text):
            literal = _strip_exact(match.group("version"))
            if literal != workspace_version:
                errors.append(
                    f"{manifest.relative_to(ROOT)} pins {match.group('name')} "
                    f"to version {match.group('version')!r}, expected {workspace_version!r}"
                )

    if errors:
        raise ReleaseCheckError(
            "Release version checks failed:\n  - " + "\n  - ".join(errors)
        )


def validate_rust_publish_order() -> None:
    """Confirm every workspace member is listed in RUST_PUBLISH_ORDER."""

    workspace = _read_toml(ROOT / "Cargo.toml")["workspace"]
    members = {Path(member).name for member in workspace.get("members", [])}
    configured = set(RUST_PUBLISH_ORDER)

    missing = sorted(members - configured)
    extra = sorted(configured - members)
    errors: list[str] = []
    if missing:
        errors.append(
            f"RUST_PUBLISH_ORDER is missing workspace member(s): {', '.join(missing)}"
        )
    if extra:
        errors.append(
            f"RUST_PUBLISH_ORDER lists non-member(s): {', '.join(extra)}"
        )

    if errors:
        raise ReleaseCheckError(
            "Rust publish-order checks failed:\n  - " + "\n  - ".join(errors)
        )


def validate_release_metadata() -> None:
    validate_release_versions()
    validate_rust_publish_order()
