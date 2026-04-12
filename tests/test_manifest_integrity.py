"""
Manifest integrity tests — validates that all published manifests conform to
the contract expected by the consumer API (clang-tool-chain).

These tests run in CI to catch manifest issues BEFORE they reach downstream.
If a test here fails, the downstream clang-tool-chain project WILL break.

Consumer contract (from clang_tool_chain/manifest.py + installers/base.py):

Root manifest:
  - platforms[].platform: str (REQUIRED)
  - platforms[].architectures[].arch: str (REQUIRED)
  - platforms[].architectures[].manifest_path: str (points to platform manifest)

Platform manifest:
  - latest: str (REQUIRED, must not be null, must reference existing version)
  - Version entries (flat keys or nested "versions" dict):
    - href: str (REQUIRED, download URL)
    - sha256: str (REQUIRED, hex hash)
    - parts[]: optional, for multi-part archives
      - parts[].href: str (REQUIRED)
      - parts[].sha256: str (REQUIRED)
      - parts[].size: int (optional)
"""

import json
import re
from pathlib import Path

import pytest

ASSETS = Path(__file__).parent.parent / "assets"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_tools():
    """Yield (tool_name, tool_dir) for each tool in assets/."""
    for d in sorted(ASSETS.iterdir()):
        if d.is_dir() and (d / "manifest.json").exists():
            yield d.name, d


def _iter_platform_manifests(tool_dir: Path):
    """Yield (platform, arch, manifest_path, manifest_dict) for every platform manifest."""
    root = _load_json(tool_dir / "manifest.json")
    for plat_entry in root.get("platforms", []):
        platform = plat_entry["platform"]
        for arch_entry in plat_entry.get("architectures", []):
            arch = arch_entry["arch"]
            rel_path = arch_entry.get("manifest_path")
            if not rel_path:
                continue
            manifest_path = tool_dir / rel_path
            if not manifest_path.exists():
                pytest.fail(f"Platform manifest missing: {manifest_path}")
            yield platform, arch, manifest_path, _load_json(manifest_path)


def _get_versions(manifest: dict) -> dict:
    """Extract version entries from either flat or nested manifest format."""
    if "versions" in manifest and isinstance(manifest["versions"], dict):
        return manifest["versions"]
    return {k: v for k, v in manifest.items()
            if k != "latest" and isinstance(v, dict)}


# ===========================================================================
# Root manifest validation
# ===========================================================================

class TestRootManifests:
    """Every root manifest must have valid structure."""

    def test_root_manifests_have_platforms_array(self):
        for tool, tool_dir in _iter_tools():
            root = _load_json(tool_dir / "manifest.json")
            assert "platforms" in root, (
                f"{tool}/manifest.json: missing 'platforms' key"
            )
            assert isinstance(root["platforms"], list), (
                f"{tool}/manifest.json: 'platforms' must be an array"
            )

    def test_platform_entries_have_required_fields(self):
        for tool, tool_dir in _iter_tools():
            root = _load_json(tool_dir / "manifest.json")
            for i, plat in enumerate(root.get("platforms", [])):
                assert "platform" in plat, (
                    f"{tool}/manifest.json: platforms[{i}] missing 'platform'"
                )
                assert "architectures" in plat, (
                    f"{tool}/manifest.json: platforms[{i}] ({plat.get('platform')}) "
                    f"missing 'architectures'"
                )
                for j, arch in enumerate(plat.get("architectures", [])):
                    assert "arch" in arch, (
                        f"{tool}/manifest.json: platforms[{i}].architectures[{j}] "
                        f"missing 'arch'"
                    )

    def test_manifest_path_entries_point_to_existing_files(self):
        for tool, tool_dir in _iter_tools():
            root = _load_json(tool_dir / "manifest.json")
            for plat in root.get("platforms", []):
                for arch in plat.get("architectures", []):
                    rel = arch.get("manifest_path")
                    if not rel:
                        continue
                    full = tool_dir / rel
                    assert full.exists(), (
                        f"{tool}: root manifest references '{rel}' but file "
                        f"does not exist at {full}"
                    )


# ===========================================================================
# Platform manifest validation
# ===========================================================================

class TestPlatformManifests:
    """Every platform manifest must satisfy the consumer API contract."""

    def test_latest_is_not_null(self):
        """Consumer does manifest.versions[manifest.latest] — null latest = KeyError: None."""
        for tool, tool_dir in _iter_tools():
            for plat, arch, path, manifest in _iter_platform_manifests(tool_dir):
                latest = manifest.get("latest")
                assert latest is not None, (
                    f"{tool}/{plat}/{arch}: 'latest' is null. "
                    f"Consumer does manifest.versions[manifest.latest] -> KeyError: None"
                )

    def test_latest_references_existing_version(self):
        """Consumer does manifest.versions[manifest.latest] — must find the key."""
        for tool, tool_dir in _iter_tools():
            for plat, arch, path, manifest in _iter_platform_manifests(tool_dir):
                latest = manifest.get("latest")
                if not latest:
                    continue
                versions = _get_versions(manifest)
                assert latest in versions, (
                    f"{tool}/{plat}/{arch}: latest='{latest}' not in versions "
                    f"{list(versions.keys())}"
                )

    def test_version_entries_have_href(self):
        """Consumer accesses version_info.href — must exist."""
        for tool, tool_dir in _iter_tools():
            for plat, arch, path, manifest in _iter_platform_manifests(tool_dir):
                for ver, info in _get_versions(manifest).items():
                    assert "href" in info, (
                        f"{tool}/{plat}/{arch} v{ver}: missing 'href'"
                    )

    def test_version_entries_have_sha256(self):
        """Consumer accesses version_info.sha256 — must exist."""
        for tool, tool_dir in _iter_tools():
            for plat, arch, path, manifest in _iter_platform_manifests(tool_dir):
                for ver, info in _get_versions(manifest).items():
                    assert "sha256" in info, (
                        f"{tool}/{plat}/{arch} v{ver}: missing 'sha256'"
                    )

    def test_sha256_is_valid_hex(self):
        """SHA256 must be a 64-character hex string."""
        for tool, tool_dir in _iter_tools():
            for plat, arch, path, manifest in _iter_platform_manifests(tool_dir):
                for ver, info in _get_versions(manifest).items():
                    sha = info.get("sha256", "")
                    assert re.fullmatch(r"[0-9a-fA-F]{64}", sha), (
                        f"{tool}/{plat}/{arch} v{ver}: sha256 '{sha}' is not "
                        f"a valid 64-char hex string"
                    )

    def test_href_is_valid_url(self):
        """href must be a URL starting with https://."""
        for tool, tool_dir in _iter_tools():
            for plat, arch, path, manifest in _iter_platform_manifests(tool_dir):
                for ver, info in _get_versions(manifest).items():
                    href = info.get("href", "")
                    assert href.startswith("https://"), (
                        f"{tool}/{plat}/{arch} v{ver}: href '{href}' must "
                        f"start with https://"
                    )


# ===========================================================================
# Multi-part archive validation
# ===========================================================================

class TestMultipartArchives:
    """Multi-part archives must have per-part href and sha256."""

    def test_parts_have_href(self):
        """Consumer does p['href'] on each part."""
        for tool, tool_dir in _iter_tools():
            for plat, arch, path, manifest in _iter_platform_manifests(tool_dir):
                for ver, info in _get_versions(manifest).items():
                    for i, part in enumerate(info.get("parts", [])):
                        assert "href" in part, (
                            f"{tool}/{plat}/{arch} v{ver} part[{i}]: "
                            f"missing 'href'"
                        )

    def test_parts_have_sha256(self):
        """Consumer does p['sha256'] on each part — KeyError if missing."""
        for tool, tool_dir in _iter_tools():
            for plat, arch, path, manifest in _iter_platform_manifests(tool_dir):
                for ver, info in _get_versions(manifest).items():
                    for i, part in enumerate(info.get("parts", [])):
                        assert "sha256" in part, (
                            f"{tool}/{plat}/{arch} v{ver} part[{i}]: "
                            f"missing 'sha256'. Consumer does p['sha256'] -> KeyError. "
                            f"Part: {part}"
                        )

    def test_part_sha256_is_valid_hex(self):
        for tool, tool_dir in _iter_tools():
            for plat, arch, path, manifest in _iter_platform_manifests(tool_dir):
                for ver, info in _get_versions(manifest).items():
                    for i, part in enumerate(info.get("parts", [])):
                        sha = part.get("sha256", "")
                        assert re.fullmatch(r"[0-9a-fA-F]{64}", sha), (
                            f"{tool}/{plat}/{arch} v{ver} part[{i}]: "
                            f"sha256 '{sha}' is not valid hex"
                        )

    def test_part_hrefs_are_valid_urls(self):
        for tool, tool_dir in _iter_tools():
            for plat, arch, path, manifest in _iter_platform_manifests(tool_dir):
                for ver, info in _get_versions(manifest).items():
                    for i, part in enumerate(info.get("parts", [])):
                        href = part.get("href", "")
                        assert href.startswith("https://"), (
                            f"{tool}/{plat}/{arch} v{ver} part[{i}]: "
                            f"href '{href}' must start with https://"
                        )
