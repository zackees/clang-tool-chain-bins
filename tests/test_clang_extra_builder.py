from __future__ import annotations

import json
import runpy
import stat
import sys
import tarfile
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import pyzstd

from clang_tool_chain_bins._impl.archive_index import build_aggregate_index, build_meta_index, build_sidecar_indexes
from tools.create_clang_extra_archives import (
    EXTRA_TOOLS,
    LLVM_DOWNLOAD_SHA256,
    SUPPORTED_TARGETS,
    build_archive,
    download_llvm,
    stage_clang_extra,
)
from tools.integrate_clang_extra_artifacts import merge_archive_records, merge_component_manifest


class ClangExtraBuilderTests(unittest.TestCase):
    def _load_forge_recipe(self) -> dict[str, object]:
        conan = types.ModuleType("conan")
        conan.ConanFile = object
        conan_tools = types.ModuleType("conan.tools")
        conan_cmake = types.ModuleType("conan.tools.cmake")
        conan_cmake.CMake = object
        conan_cmake.CMakeToolchain = object
        conan_files = types.ModuleType("conan.tools.files")
        conan_files.copy = lambda *args, **kwargs: None
        modules = {
            "conan": conan,
            "conan.tools": conan_tools,
            "conan.tools.cmake": conan_cmake,
            "conan.tools.files": conan_files,
        }
        recipe = Path(__file__).parents[1] / "ci" / "clangd" / "conanfile.py"
        with patch.dict(sys.modules, modules):
            return runpy.run_path(str(recipe))

    def _source(self, root: Path, platform: str = "linux") -> Path:
        (root / "bin").mkdir(parents=True)
        (root / "lib" / "clang" / "21" / "include").mkdir(parents=True)
        for name in EXTRA_TOOLS:
            suffix = ".exe" if platform == "win" and name not in {"git-clang-format", "run-clang-tidy"} else ""
            path = root / "bin" / (name + suffix)
            path.write_bytes(name.encode())
            if platform != "win":
                path.chmod(0o755)
        (root / "lib" / "clang" / "21" / "include" / "stddef.h").write_text("#pragma once\n")
        (root / "lib" / "libLLVM.so.21").write_bytes(b"runtime")
        return root

    def test_allowlist_and_runtime_are_staged_with_executable_modes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = self._source(Path(directory) / "llvm")
            staging = Path(directory) / "staging"
            provenance = stage_clang_extra(source, staging, "linux", "x86_64", "21.1.5")
            self.assertEqual(provenance["method"], "extracted")
            self.assertEqual(sorted(p.name for p in (staging / "bin").iterdir()), sorted(EXTRA_TOOLS))
            self.assertTrue((staging / "lib" / "clang" / "21" / "include" / "stddef.h").exists())
            self.assertTrue((staging / "lib" / "libLLVM.so.21").exists())
            if not __import__("sys").platform.startswith("win"):
                for path in (staging / "bin").iterdir():
                    self.assertTrue(path.stat().st_mode & stat.S_IXUSR)

    def test_archive_contains_tools_resources_and_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._source(root / "llvm")
            archive = build_archive(source, root / "assets", "linux", "x86_64", "21.1.5")
            with archive.open("rb") as raw, pyzstd.ZstdFile(raw) as compressed, tarfile.open(fileobj=compressed, mode="r|") as tar:
                members = {member.name: member for member in tar}
            self.assertEqual({f"bin/{name}" for name in EXTRA_TOOLS}, {name for name in members if name.startswith("bin/")})
            self.assertTrue(members["bin/clangd"].mode & stat.S_IXUSR)
            self.assertIn("lib/clang/21/include/stddef.h", members)
            provenance = json.loads(Path(f"{archive}.provenance.json").read_text())
            self.assertEqual(provenance["llvm_version"], "21.1.5")
            manifest = json.loads((root / "assets" / "linux" / "x86_64" / "manifest.json").read_text())
            self.assertEqual(manifest["latest"], "21.1.5")
            self.assertEqual(manifest["versions"]["21.1.5"]["sha256"], (Path(f"{archive}.sha256").read_text().split()[0]))

    def test_missing_binary_fails_loudly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = self._source(Path(directory) / "llvm")
            (source / "bin" / "clangd").unlink()
            with self.assertRaises(FileNotFoundError):
                stage_clang_extra(source, Path(directory) / "staging", "linux", "x86_64", "21.1.5")

    def test_generated_sidecar_and_indexes_expose_clangd(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._source(root / "llvm")
            archive = build_archive(source, root / "assets" / "clang-extra", "linux", "x86_64", "21.1.5")
            build_sidecar_indexes(root / "assets")
            build_aggregate_index(root / "assets", root / "aggregate.json")
            build_meta_index(root / "assets", root / "meta.json")
            sidecar = json.loads(Path(f"{archive}.json").read_text())
            self.assertEqual([tool["tool_name"] for tool in sidecar["tools"]], sorted(EXTRA_TOOLS))
            aggregate = json.loads((root / "aggregate.json").read_text())
            matches = [tool for tool in aggregate["tools"] if tool["tool_name"] == "clangd"]
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0]["path_in_archive"], "bin/clangd")
            self.assertEqual(matches[0]["provenance"]["llvm_version"], "21.1.5")

    def test_supported_targets_are_exactly_issue_allowlist(self) -> None:
        self.assertEqual(SUPPORTED_TARGETS, {
            ("win", "x86_64"), ("win", "arm64"), ("linux", "x86_64"), ("linux", "arm64"),
            ("darwin", "x86_64"), ("darwin", "arm64"),
        })

    def test_windows_arm64_download_is_pinned_and_hash_verified(self) -> None:
        expected = "d570e77cd37791372ddab07fe892a2a25f0824821dc57e546118a3f1ee4b66de"
        self.assertEqual(LLVM_DOWNLOAD_SHA256[("win", "arm64", "21.1.5")], expected)
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            payload = work / "LLVM-21.1.5-woa64.exe"
            payload.write_bytes(b"not-the-pinned-upstream-payload")
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                download_llvm("win", "arm64", "21.1.5", work)

    def test_windows_arm64_provenance_records_upstream_and_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._source(root / "llvm", platform="win")
            provenance = stage_clang_extra(
                source,
                root / "staging",
                "win",
                "arm64",
                "21.1.5",
                "8e2cd28cd4ba46613a46467b0c91b1cabead26cd",
                build_options={
                    "upstream_url": "https://example.invalid/LLVM-21.1.5-woa64.exe",
                    "upstream_sha256": "d570e77cd37791372ddab07fe892a2a25f0824821dc57e546118a3f1ee4b66de",
                    "extraction_method": "7z",
                },
            )
            self.assertEqual(provenance["llvm_project_tag"], "llvmorg-21.1.5")
            self.assertEqual(provenance["llvm_project_commit"], "8e2cd28cd4ba46613a46467b0c91b1cabead26cd")
            self.assertEqual(provenance["target"], {"platform": "win", "arch": "arm64"})
            self.assertEqual(provenance["build_options"]["extraction_method"], "7z")

    def test_forge_recipe_does_not_force_generator_or_install_prefix(self) -> None:
        recipe = (Path(__file__).parents[1] / "ci" / "clangd" / "conanfile.py").read_text(encoding="utf-8")
        self.assertIn("toolchain = CMakeToolchain(self)", recipe)
        self.assertNotIn('generator="Ninja"', recipe)
        self.assertNotIn('toolchain.variables["CMAKE_INSTALL_PREFIX"]', recipe)
        self.assertIn('toolchain.variables["LLVM_TARGETS_TO_BUILD"] = "AArch64"', recipe)
        self.assertIn('self.settings.rm_safe("compiler.cppstd")', recipe)
        self.assertIn('toolchain.variables["CMAKE_CXX_STANDARD"] = 17', recipe)
        self.assertIn('tool_output_dir.glob("*.dll")', recipe)

    def test_forge_recipe_finds_multiconfig_resource_headers(self) -> None:
        recipe = self._load_forge_recipe()
        find_build_output = recipe["find_build_output"]
        find_compiled_tool_outputs = recipe["find_compiled_tool_outputs"]
        find_resource_include = recipe["find_clang_resource_include"]
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory) / "build"
            executables = [build / "Release" / "bin" / f"{tool}.exe" for tool in recipe["COMPILED_TOOLS"]]
            executables[0].parent.mkdir(parents=True)
            for executable in executables:
                executable.touch()
            expected = build / "Release" / "lib" / "clang" / "21" / "include"
            expected.mkdir(parents=True)
            (expected / "stddef.h").write_text("#pragma once\n", encoding="utf-8")
            self.assertEqual(find_build_output(build, "clangd.exe"), executables[0])
            self.assertEqual(find_compiled_tool_outputs(build), executables)
            self.assertEqual(find_resource_include(build, "21"), expected)

            executables[0].unlink()
            (expected / "stddef.h").unlink()
            with self.assertRaisesRegex(RuntimeError, "did not produce clangd.exe"):
                find_build_output(build, "clangd.exe")
            with self.assertRaisesRegex(RuntimeError, "did not produce Clang 21 resource headers"):
                find_resource_include(build, "21")

            executables[0].parent.mkdir(parents=True, exist_ok=True)
            executables[0].touch()
            moved = build / "Other" / "bin" / executables[-1].name
            moved.parent.mkdir(parents=True)
            executables[-1].replace(moved)
            with self.assertRaisesRegex(RuntimeError, "compiled tools in multiple directories"):
                find_compiled_tool_outputs(build)

    def test_integration_merges_windows_arm64_without_losing_existing_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            component = Path(directory) / "assets" / "clang-extra"
            component.mkdir(parents=True)
            (component / "manifest.json").write_text(
                json.dumps(
                    {
                        "platforms": [
                            {
                                "platform": "win",
                                "architectures": [
                                    {"arch": "x86_64", "manifest_path": "win/x86_64/manifest.json"}
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            archive = component / "win" / "arm64" / "clang-extra-21.1.5-win-arm64.tar.zst"
            archive.parent.mkdir(parents=True)
            archive.touch()

            merge_component_manifest(component, [archive])

            root = json.loads((component / "manifest.json").read_text(encoding="utf-8"))
            targets = {
                (platform["platform"], arch["arch"])
                for platform in root["platforms"]
                for arch in platform["architectures"]
            }
            self.assertEqual(targets, {("win", "x86_64"), ("win", "arm64")})

    def test_integration_preserves_unindexed_legacy_aggregate_records(self) -> None:
        existing = {
            "schema_version": 1,
            "archives": [
                {
                    "component": "lldb",
                    "filename": "legacy.tar.zst",
                    "relative_path": "legacy.tar.zst",
                },
                {
                    "component": "clang-extra",
                    "filename": "old.tar.zst",
                    "relative_path": "new.tar.zst",
                },
            ],
        }
        generated = {
            "schema_version": 1,
            "archives": [
                {
                    "component": "clang-extra",
                    "filename": "new.tar.zst",
                    "relative_path": "new.tar.zst",
                },
            ],
        }

        merged = merge_archive_records(existing, generated, "archives", {"new.tar.zst"})

        self.assertEqual(
            [(item["component"], item["filename"]) for item in merged["archives"]],
            [("lldb", "legacy.tar.zst"), ("clang-extra", "new.tar.zst")],
        )

    def test_integration_rejects_missing_generated_archive_records(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing archives"):
            merge_archive_records(
                {"archives": []},
                {"archives": []},
                "archives",
                {"clang-extra/win/arm64/archive.tar.zst"},
            )


if __name__ == "__main__":
    unittest.main()
