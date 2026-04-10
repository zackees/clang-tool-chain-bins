from __future__ import annotations

import io
import json
import tarfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import fasteners
import pyzstd
from tools import install
from tools.common import get_install_dir, get_lock_path, sha256_file


def _make_test_archive(archive_path: Path, members: dict[str, bytes]) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with archive_path.open("wb") as raw_f, pyzstd.ZstdFile(raw_f, "wb") as zstd_f, tarfile.open(
        fileobj=zstd_f, mode="w"
    ) as tar:
        for name, content in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(content))


def _make_match(archive_path: Path) -> dict[str, object]:
    return {
        "tool_name": "clang",
        "component": "clang",
        "version": "21.1.5",
        "platform": "linux",
        "arch": "x86_64",
        "archive_sha256": sha256_file(archive_path),
        "archive_url": archive_path.resolve().as_uri(),
        "archive_filename": archive_path.name,
        "parts": [],
    }


class InstallTests(unittest.TestCase):
    def test_install_match_extracts_archive_and_marks_done(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            archive_path = tmp_root / "archives" / "llvm-21.1.5-linux-x86_64.tar.zst"
            _make_test_archive(
                archive_path,
                {
                    "bin/clang": b"#!/bin/sh\necho clang\n",
                    "bin/clang++": b"#!/bin/sh\necho clang++\n",
                },
            )
            match = _make_match(archive_path)
            home_dir = tmp_root / "home"

            result = install.install_match(match, home_dir=home_dir)
            install_dir = Path(result["install_path"])

            self.assertEqual(result["status"], "installed")
            self.assertTrue((install_dir / "bin" / "clang").exists())
            self.assertTrue((install_dir / "done.txt").exists())
            self.assertIn(match["archive_sha256"], (install_dir / "done.txt").read_text(encoding="utf-8"))

    def test_ensure_match_returns_already_installed_without_reinstall(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            archive_path = tmp_root / "archives" / "llvm-21.1.5-linux-x86_64.tar.zst"
            _make_test_archive(archive_path, {"bin/clang": b"#!/bin/sh\necho clang\n"})
            match = _make_match(archive_path)
            home_dir = tmp_root / "home"
            install_dir = get_install_dir("clang", "linux", "x86_64", home_dir)
            install_dir.mkdir(parents=True, exist_ok=True)
            (install_dir / "done.txt").write_text(f"archive_sha256={match['archive_sha256']}\n", encoding="utf-8")

            with patch("tools.install._ensure_cached") as ensure_cached_mock:
                result = install.ensure_match(match, home_dir=home_dir)

            self.assertEqual(result["status"], "already_installed")
            ensure_cached_mock.assert_not_called()

    def test_tryinstall_returns_locked_when_lock_is_held(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            archive_path = tmp_root / "archives" / "llvm-21.1.5-linux-x86_64.tar.zst"
            _make_test_archive(archive_path, {"bin/clang": b"#!/bin/sh\necho clang\n"})
            match = _make_match(archive_path)
            home_dir = tmp_root / "home"
            lock_path = get_lock_path("clang", "linux", "x86_64", home_dir)
            lock = fasteners.InterProcessLock(str(lock_path))
            self.assertTrue(lock.acquire(blocking=False))
            try:
                result = install.tryinstall_match(match, home_dir=home_dir)
            finally:
                lock.release()
                lock._do_close()

            self.assertEqual(result["operation"], "tryinstall")
            self.assertEqual(result["status"], "locked")

    def test_tryinstall_returns_already_installed(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            archive_path = tmp_root / "archives" / "llvm-21.1.5-linux-x86_64.tar.zst"
            _make_test_archive(archive_path, {"bin/clang": b"#!/bin/sh\necho clang\n"})
            match = _make_match(archive_path)
            home_dir = tmp_root / "home"
            install_dir = get_install_dir("clang", "linux", "x86_64", home_dir)
            install_dir.mkdir(parents=True, exist_ok=True)
            (install_dir / "done.txt").write_text(f"archive_sha256={match['archive_sha256']}\n", encoding="utf-8")

            result = install.tryinstall_match(match, home_dir=home_dir)

            self.assertEqual(result["status"], "already_installed")

    def test_install_main_requires_filters_when_multiple_candidates_exist(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            index_path = tmp_root / "tool-index.json"
            payload = {
                "schema_version": 1,
                "archive_count": 2,
                "tool_count": 2,
                "archives": [],
                "tools": [
                    {
                        "tool_name": "clang",
                        "file_name": "clang",
                        "path_in_archive": "bin/clang",
                        "tool_sha256": "a" * 64,
                        "tool_type": "file",
                        "size": 1,
                        "component": "clang",
                        "version": "21.1.5",
                        "platform": "linux",
                        "arch": "x86_64",
                        "archive_path": "clang/linux/x86_64/llvm-21.1.5-linux-x86_64.tar.zst",
                        "archive_filename": "llvm-21.1.5-linux-x86_64.tar.zst",
                        "archive_sha256": "b" * 64,
                        "archive_url": "file:///tmp/one.tar.zst",
                        "parts": [],
                    },
                    {
                        "tool_name": "clang",
                        "file_name": "clang",
                        "path_in_archive": "bin/clang",
                        "tool_sha256": "c" * 64,
                        "tool_type": "file",
                        "size": 1,
                        "component": "clang",
                        "version": "21.1.5",
                        "platform": "linux",
                        "arch": "arm64",
                        "archive_path": "clang/linux/arm64/llvm-21.1.5-linux-arm64.tar.zst",
                        "archive_filename": "llvm-21.1.5-linux-arm64.tar.zst",
                        "archive_sha256": "d" * 64,
                        "archive_url": "file:///tmp/two.tar.zst",
                        "parts": [],
                    },
                ],
            }
            index_path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaises(SystemExit) as exc:
                install.main(["clang", "--index", str(index_path)])

            self.assertIn("Multiple install candidates found", str(exc.exception))

    def test_filter_matches_supports_fully_qualified_selection(self) -> None:
        payload = {
            "tools": [
                {
                    "tool_name": "clang",
                    "file_name": "clang",
                    "path_in_archive": "bin/clang",
                    "tool_sha256": "a" * 64,
                    "tool_type": "file",
                    "size": 1,
                    "component": "clang",
                    "version": "21.1.5",
                    "platform": "linux",
                    "arch": "x86_64",
                    "archive_path": "clang/linux/x86_64/llvm-21.1.5-linux-x86_64.tar.zst",
                    "archive_filename": "llvm-21.1.5-linux-x86_64.tar.zst",
                    "archive_sha256": "b" * 64,
                    "archive_url": "file:///tmp/one.tar.zst",
                    "parts": [],
                },
                {
                    "tool_name": "clang",
                    "file_name": "clang",
                    "path_in_archive": "bin/clang",
                    "tool_sha256": "c" * 64,
                    "tool_type": "file",
                    "size": 1,
                    "component": "clang",
                    "version": "21.1.5",
                    "platform": "linux",
                    "arch": "arm64",
                    "archive_path": "clang/linux/arm64/llvm-21.1.5-linux-arm64.tar.zst",
                    "archive_filename": "llvm-21.1.5-linux-arm64.tar.zst",
                    "archive_sha256": "d" * 64,
                    "archive_url": "file:///tmp/two.tar.zst",
                    "parts": [],
                },
            ]
        }

        matches = install._filter_matches(
            payload,
            "clang",
            platform="linux",
            arch="arm64",
            version="21.1.5",
            component="clang",
        )

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["arch"], "arm64")

    def test_install_rejects_path_traversal_entries(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            archive_path = tmp_root / "archives" / "bad.tar.zst"
            _make_test_archive(
                archive_path,
                {
                    "../escape": b"bad\n",
                },
            )
            match = _make_match(archive_path)

            with self.assertRaises(RuntimeError) as exc:
                install.install_match(match, home_dir=tmp_root / "home")

            self.assertIn("escapes install root", str(exc.exception))

    def test_install_dry_run_does_not_create_install_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            archive_path = tmp_root / "archives" / "llvm-21.1.5-linux-x86_64.tar.zst"
            _make_test_archive(archive_path, {"bin/clang": b"#!/bin/sh\necho clang\n"})
            match = _make_match(archive_path)
            home_dir = tmp_root / "home"

            result = install.install_match(match, home_dir=home_dir, dry_run=True)
            install_dir = Path(result["install_path"])

            self.assertEqual(result["status"], "dry_run")
            self.assertFalse(install_dir.exists())

    def test_install_main_dry_run_outputs_plan(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            archive_path = tmp_root / "archives" / "llvm-21.1.5-linux-x86_64.tar.zst"
            _make_test_archive(archive_path, {"bin/clang": b"#!/bin/sh\necho clang\n"})
            payload = {
                "schema_version": 1,
                "archive_count": 1,
                "tool_count": 1,
                "archives": [],
                "tools": [
                    {
                        "tool_name": "clang",
                        "file_name": "clang",
                        "path_in_archive": "bin/clang",
                        "tool_sha256": "a" * 64,
                        "tool_type": "file",
                        "size": 1,
                        "component": "clang",
                        "version": "21.1.5",
                        "platform": "linux",
                        "arch": "x86_64",
                        "archive_path": "clang/linux/x86_64/llvm-21.1.5-linux-x86_64.tar.zst",
                        "archive_filename": archive_path.name,
                        "archive_sha256": sha256_file(archive_path),
                        "archive_url": archive_path.resolve().as_uri(),
                        "parts": [],
                    }
                ],
            }
            index_path = tmp_root / "tool-index.json"
            index_path.write_text(json.dumps(payload), encoding="utf-8")

            output = io.StringIO()
            with patch("sys.stdout", output):
                exit_code = install.main(
                    ["clang", "--dry-run", "--index", str(index_path), "--home-dir", str(tmp_root / "home")]
                )

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue().splitlines()[0])
            self.assertEqual(payload["status"], "dry_run")
            self.assertEqual(payload["operation"], "install")


if __name__ == "__main__":
    unittest.main()
