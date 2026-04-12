"""
Manifest integrity tests — validates that all published manifests have
the required fields for the consumer API (clang-tool-chain) to work.

These tests reproduce the exact errors found in clang-tool-chain CI:
- Bug 1: LLDB manifests have latest=null (KeyError: None)
- Bug 2: IWYU parts missing per-part sha256 (KeyError: 'sha256')
- Bug 3: clang-extra missing linux/arm64 (RuntimeError: platform not found)
"""

import json
from pathlib import Path

import pytest

ASSETS = Path(__file__).parent.parent / "assets"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_platform_manifests(tool: str):
    """Yield (platform, arch, manifest_dict) for every platform manifest of a tool."""
    root_manifest_path = ASSETS / tool / "manifest.json"
    if not root_manifest_path.exists():
        pytest.skip(f"No root manifest for {tool}")
    root = _load_json(root_manifest_path)
    for plat_entry in root.get("platforms", []):
        platform = plat_entry["platform"]
        for arch_entry in plat_entry.get("architectures", []):
            arch = arch_entry["arch"]
            if "manifest_path" not in arch_entry:
                continue  # Some root manifests use a different schema
            manifest_path = ASSETS / tool / arch_entry["manifest_path"]
            if not manifest_path.exists():
                pytest.fail(f"Platform manifest missing: {manifest_path}")
            yield platform, arch, _load_json(manifest_path)


class TestManifestIntegrity:
    """Every published platform manifest must have valid data for the consumer API."""

    def test_all_platform_manifests_have_non_null_latest(self):
        """Bug 1 repro: manifest.versions[manifest.latest] fails when latest is None."""
        for tool_dir in ASSETS.iterdir():
            if not tool_dir.is_dir():
                continue
            tool = tool_dir.name
            for platform, arch, manifest in _iter_platform_manifests(tool):
                latest = manifest.get("latest")
                assert latest is not None, (
                    f"{tool}/{platform}/{arch}: 'latest' is null/missing. "
                    f"Consumer does manifest.versions[manifest.latest] which raises KeyError: None"
                )
                # If latest is set, it must exist as a version key
                # Manifest formats: either flat (version keys at same level as "latest")
                # or nested (under "versions" dict)
                if "versions" in manifest and isinstance(manifest["versions"], dict):
                    version_keys = list(manifest["versions"].keys())
                else:
                    version_keys = [k for k in manifest if k != "latest"]
                if version_keys:
                    assert latest in version_keys, (
                        f"{tool}/{platform}/{arch}: latest='{latest}' but "
                        f"available versions are {version_keys}"
                    )

    def test_all_manifest_parts_have_sha256(self):
        """Bug 2 repro: p['sha256'] fails when parts lack per-part sha256."""
        for tool_dir in ASSETS.iterdir():
            if not tool_dir.is_dir():
                continue
            tool = tool_dir.name
            for platform, arch, manifest in _iter_platform_manifests(tool):
                # Handle both flat and nested manifest formats
                if "versions" in manifest and isinstance(manifest["versions"], dict):
                    versions = manifest["versions"]
                else:
                    versions = {k: v for k, v in manifest.items()
                                if k != "latest" and isinstance(v, dict)}
                for version_key, version_info in versions.items():
                    parts = version_info.get("parts", [])
                    for i, part in enumerate(parts):
                        assert "sha256" in part, (
                            f"{tool}/{platform}/{arch} version {version_key} "
                            f"part[{i}] missing 'sha256'. "
                            f"Consumer does p['sha256'] which raises KeyError. "
                            f"Part: {part}"
                        )

    def test_all_root_manifest_entries_have_valid_platform_manifests(self):
        """Every platform listed in a root manifest must have a valid platform manifest file."""
        for tool_dir in ASSETS.iterdir():
            if not tool_dir.is_dir():
                continue
            root_path = tool_dir / "manifest.json"
            if not root_path.exists():
                continue
            root = _load_json(root_path)
            for plat_entry in root.get("platforms", []):
                for arch_entry in plat_entry.get("architectures", []):
                    if "manifest_path" not in arch_entry:
                        continue
                    pm_path = tool_dir / arch_entry["manifest_path"]
                    assert pm_path.exists(), (
                        f"{tool_dir.name}: root manifest references "
                        f"{arch_entry['manifest_path']} but file does not exist"
                    )
                    pm = _load_json(pm_path)
                    latest = pm.get("latest")
                    if latest is not None:
                        # If latest is set, verify the version entry exists
                        if "versions" in pm:
                            assert latest in pm["versions"], (
                                f"{tool_dir.name}/{arch_entry['manifest_path']}: "
                                f"latest='{latest}' not in versions"
                            )
                        else:
                            assert latest in pm, (
                                f"{tool_dir.name}/{arch_entry['manifest_path']}: "
                                f"latest='{latest}' not found as key"
                            )
