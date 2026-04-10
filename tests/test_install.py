from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
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


def _make_multipart_match(part_paths: list[Path], *, archive_name: str = "llvm-21.1.5-linux-x86_64.tar.zst") -> dict[str, object]:
    joined_path = part_paths[0].parent / archive_name
    with joined_path.open("wb") as out_f:
        for part_path in part_paths:
            with part_path.open("rb") as in_f:
                out_f.write(in_f.read())

    return {
        "tool_name": "clang",
        "component": "clang",
        "version": "21.1.5",
        "platform": "linux",
        "arch": "x86_64",
        "archive_sha256": sha256_file(joined_path),
        "archive_url": joined_path.resolve().as_uri(),
        "archive_filename": archive_name,
        "parts": [{"href": part_path.resolve().as_uri()} for part_path in part_paths],
    }


class InstallTests(unittest.TestCase):
    def tearDown(self) -> None:
        install._resolve_zccache_binary.cache_clear()
        install._assert_zccache_download_support.cache_clear()

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

    def test_ensure_cached_concatenates_local_file_parts(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            archive_path = tmp_root / "archives" / "llvm-21.1.5-linux-x86_64.tar.zst"
            _make_test_archive(archive_path, {"bin/clang": b"#!/bin/sh\necho clang\n"})
            archive_bytes = archive_path.read_bytes()
            part_dir = tmp_root / "parts"
            part_dir.mkdir(parents=True, exist_ok=True)
            part_a = part_dir / "archive.part-aa"
            part_b = part_dir / "archive.part-ab"
            midpoint = len(archive_bytes) // 2
            part_a.write_bytes(archive_bytes[:midpoint])
            part_b.write_bytes(archive_bytes[midpoint:])
            match = _make_multipart_match([part_a, part_b])

            cache_path = install._ensure_cached(match, tmp_root / "home")

            self.assertTrue(cache_path.exists())
            self.assertEqual(sha256_file(cache_path), match["archive_sha256"])

    def test_ensure_cached_uses_zccache_for_http_downloads(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            archive_path = tmp_root / "archives" / "llvm-21.1.5-linux-x86_64.tar.zst"
            _make_test_archive(archive_path, {"bin/clang": b"#!/bin/sh\necho clang\n"})
            match = _make_match(archive_path)
            match["archive_url"] = "https://example.invalid/llvm-21.1.5-linux-x86_64.tar.zst"
            home_dir = tmp_root / "home"

            def _fake_fetch(source: str | list[str], destination: Path, expected_sha256: str) -> None:
                self.assertEqual(source, match["archive_url"])
                self.assertEqual(expected_sha256, match["archive_sha256"])
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(archive_path, destination)

            with patch("tools.install._fetch_with_zccache", side_effect=_fake_fetch) as fetch_mock:
                cache_path = install._ensure_cached(match, home_dir)

            self.assertTrue(cache_path.exists())
            self.assertEqual(sha256_file(cache_path), match["archive_sha256"])
            fetch_mock.assert_called_once()

    def test_ensure_cached_uses_zccache_for_http_multipart_downloads(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            archive_path = tmp_root / "archives" / "llvm-21.1.5-linux-x86_64.tar.zst"
            _make_test_archive(archive_path, {"bin/clang": b"#!/bin/sh\necho clang\n"})
            archive_bytes = archive_path.read_bytes()
            midpoint = len(archive_bytes) // 2
            part_urls = [
                "https://example.invalid/llvm-21.1.5-linux-x86_64.tar.zst.part-aa",
                "https://example.invalid/llvm-21.1.5-linux-x86_64.tar.zst.part-ab",
            ]
            match = {
                **_make_match(archive_path),
                "parts": [{"href": part_urls[0]}, {"href": part_urls[1]}],
            }

            def _fake_fetch(source: str | list[str], destination: Path, expected_sha256: str) -> None:
                self.assertEqual(source, part_urls)
                self.assertEqual(expected_sha256, match["archive_sha256"])
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive_bytes[:midpoint] + archive_bytes[midpoint:])

            with patch("tools.install._fetch_with_zccache", side_effect=_fake_fetch) as fetch_mock:
                cache_path = install._ensure_cached(match, tmp_root / "home")

            self.assertTrue(cache_path.exists())
            self.assertEqual(sha256_file(cache_path), match["archive_sha256"])
            fetch_mock.assert_called_once()

    def test_fetch_with_zccache_invokes_cli_for_single_url(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            binary_path = tmp_root / ("zccache.exe" if os.name == "nt" else "zccache")
            binary_path.write_text("", encoding="utf-8")
            daemon_name = "zccache-daemon.exe" if os.name == "nt" else "zccache-daemon"
            (tmp_root / daemon_name).write_text("", encoding="utf-8")
            destination = tmp_root / "cache" / "llvm.tar.zst"
            commands: list[list[str]] = []

            def _fake_run(command: list[str], *, capture_output: bool, text: bool, check: bool) -> subprocess.CompletedProcess[str]:
                self.assertTrue(capture_output)
                self.assertTrue(text)
                self.assertFalse(check)
                commands.append(command)
                if command[1:] == ["download", "--help"]:
                    return subprocess.CompletedProcess(command, 0, "Usage: zccache download\n  --part-url <PART_URLS>\n", "")
                if command[1] == "download":
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(b"archive")
                    return subprocess.CompletedProcess(command, 0, "ok\n", "")
                raise AssertionError(f"unexpected command: {command}")

            with patch.dict(os.environ, {"ZCCACHE_BIN": str(binary_path)}, clear=False):
                with patch("tools.install.subprocess.run", side_effect=_fake_run):
                    install._fetch_with_zccache(
                        "https://example.invalid/llvm.tar.zst",
                        destination,
                        "a" * 64,
                    )

            self.assertEqual(
                commands[-1],
                [
                    str(binary_path),
                    "download",
                    "--url",
                    "https://example.invalid/llvm.tar.zst",
                    "--sha256",
                    "a" * 64,
                    str(destination),
                ],
            )

    def test_fetch_with_zccache_invokes_cli_for_multipart_urls(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            binary_path = tmp_root / ("zccache.exe" if os.name == "nt" else "zccache")
            binary_path.write_text("", encoding="utf-8")
            daemon_name = "zccache-daemon.exe" if os.name == "nt" else "zccache-daemon"
            (tmp_root / daemon_name).write_text("", encoding="utf-8")
            destination = tmp_root / "cache" / "llvm.tar.zst"
            commands: list[list[str]] = []
            source_urls = [
                "https://example.invalid/llvm.tar.zst.part-aa",
                "https://example.invalid/llvm.tar.zst.part-ab",
            ]

            def _fake_run(command: list[str], *, capture_output: bool, text: bool, check: bool) -> subprocess.CompletedProcess[str]:
                self.assertTrue(capture_output)
                self.assertTrue(text)
                self.assertFalse(check)
                commands.append(command)
                if command[1:] == ["download", "--help"]:
                    return subprocess.CompletedProcess(command, 0, "Usage: zccache download\n  --part-url <PART_URLS>\n", "")
                if command[1] == "download":
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(b"archive")
                    return subprocess.CompletedProcess(command, 0, "ok\n", "")
                raise AssertionError(f"unexpected command: {command}")

            with patch.dict(os.environ, {"ZCCACHE_BIN": str(binary_path)}, clear=False):
                with patch("tools.install.subprocess.run", side_effect=_fake_run):
                    install._fetch_with_zccache(source_urls, destination, "b" * 64)

            self.assertEqual(
                commands[-1],
                [
                    str(binary_path),
                    "download",
                    "--part-url",
                    source_urls[0],
                    "--part-url",
                    source_urls[1],
                    "--sha256",
                    "b" * 64,
                    str(destination),
                ],
            )

    def test_fetch_with_zccache_rejects_binary_without_multipart_support(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            binary_path = tmp_root / ("zccache.exe" if os.name == "nt" else "zccache")
            binary_path.write_text("", encoding="utf-8")
            daemon_name = "zccache-daemon.exe" if os.name == "nt" else "zccache-daemon"
            (tmp_root / daemon_name).write_text("", encoding="utf-8")

            def _fake_run(command: list[str], *, capture_output: bool, text: bool, check: bool) -> subprocess.CompletedProcess[str]:
                self.assertTrue(capture_output)
                self.assertTrue(text)
                self.assertFalse(check)
                return subprocess.CompletedProcess(command, 0, "Usage: zccache download\n", "")

            with patch.dict(os.environ, {"ZCCACHE_BIN": str(binary_path)}, clear=False):
                with patch("tools.install.subprocess.run", side_effect=_fake_run):
                    with self.assertRaises(RuntimeError) as exc:
                        install._fetch_with_zccache(
                            [
                                "https://example.invalid/llvm.tar.zst.part-aa",
                                "https://example.invalid/llvm.tar.zst.part-ab",
                            ],
                            tmp_root / "cache" / "llvm.tar.zst",
                            "c" * 64,
                        )

            self.assertIn("does not support multipart downloads", str(exc.exception))

    def test_fetch_with_zccache_fails_when_binary_cannot_be_found(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            with patch.dict(os.environ, {"ZCCACHE_BIN": str(tmp_root / "missing-zccache.exe")}, clear=False):
                with patch("tools.install.shutil.which", return_value=None):
                    with patch("pathlib.Path.is_file", return_value=False):
                        with self.assertRaises(RuntimeError) as exc:
                            install._fetch_with_zccache(
                                "https://example.invalid/llvm.tar.zst",
                                tmp_root / "cache" / "llvm.tar.zst",
                                "d" * 64,
                            )

        self.assertIn("require a zccache binary", str(exc.exception))

    def test_fetch_with_zccache_fails_when_help_probe_errors(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            binary_path = tmp_root / ("zccache.exe" if os.name == "nt" else "zccache")
            binary_path.write_text("", encoding="utf-8")
            daemon_name = "zccache-daemon.exe" if os.name == "nt" else "zccache-daemon"
            (tmp_root / daemon_name).write_text("", encoding="utf-8")

            def _fake_run(command: list[str], *, capture_output: bool, text: bool, check: bool) -> subprocess.CompletedProcess[str]:
                self.assertTrue(capture_output)
                self.assertTrue(text)
                self.assertFalse(check)
                return subprocess.CompletedProcess(command, 9, "", "probe failed")

            with patch.dict(os.environ, {"ZCCACHE_BIN": str(binary_path)}, clear=False):
                with patch("tools.install.subprocess.run", side_effect=_fake_run):
                    with self.assertRaises(RuntimeError) as exc:
                        install._fetch_with_zccache(
                            "https://example.invalid/llvm.tar.zst",
                            tmp_root / "cache" / "llvm.tar.zst",
                            "e" * 64,
                        )

        self.assertIn("Failed to inspect zccache download support", str(exc.exception))

    def test_fetch_with_zccache_fails_when_download_command_errors(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            binary_path = tmp_root / ("zccache.exe" if os.name == "nt" else "zccache")
            binary_path.write_text("", encoding="utf-8")
            daemon_name = "zccache-daemon.exe" if os.name == "nt" else "zccache-daemon"
            (tmp_root / daemon_name).write_text("", encoding="utf-8")

            def _fake_run(command: list[str], *, capture_output: bool, text: bool, check: bool) -> subprocess.CompletedProcess[str]:
                self.assertTrue(capture_output)
                self.assertTrue(text)
                self.assertFalse(check)
                if command[1:] == ["download", "--help"]:
                    return subprocess.CompletedProcess(command, 0, "Usage: zccache download\n  --part-url <PART_URLS>\n", "")
                return subprocess.CompletedProcess(command, 7, "", "download failed")

            with patch.dict(os.environ, {"ZCCACHE_BIN": str(binary_path)}, clear=False):
                with patch("tools.install.subprocess.run", side_effect=_fake_run):
                    with self.assertRaises(RuntimeError) as exc:
                        install._fetch_with_zccache(
                            "https://example.invalid/llvm.tar.zst",
                            tmp_root / "cache" / "llvm.tar.zst",
                            "f" * 64,
                        )

        self.assertIn("zccache download failed", str(exc.exception))

    def test_fetch_with_zccache_fails_when_command_does_not_create_destination(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            binary_path = tmp_root / ("zccache.exe" if os.name == "nt" else "zccache")
            binary_path.write_text("", encoding="utf-8")
            daemon_name = "zccache-daemon.exe" if os.name == "nt" else "zccache-daemon"
            (tmp_root / daemon_name).write_text("", encoding="utf-8")

            def _fake_run(command: list[str], *, capture_output: bool, text: bool, check: bool) -> subprocess.CompletedProcess[str]:
                self.assertTrue(capture_output)
                self.assertTrue(text)
                self.assertFalse(check)
                if command[1:] == ["download", "--help"]:
                    return subprocess.CompletedProcess(command, 0, "Usage: zccache download\n  --part-url <PART_URLS>\n", "")
                return subprocess.CompletedProcess(command, 0, "ok\n", "")

            with patch.dict(os.environ, {"ZCCACHE_BIN": str(binary_path)}, clear=False):
                with patch("tools.install.subprocess.run", side_effect=_fake_run):
                    with self.assertRaises(RuntimeError) as exc:
                        install._fetch_with_zccache(
                            "https://example.invalid/llvm.tar.zst",
                            tmp_root / "cache" / "llvm.tar.zst",
                            "1" * 64,
                        )

        self.assertIn("did not create", str(exc.exception))

    def test_fetch_with_zccache_creates_expected_download_daemon_name(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            binary_path = tmp_root / ("zccache.exe" if os.name == "nt" else "zccache")
            binary_path.write_text("", encoding="utf-8")
            fallback_name = "zccache-daemon.exe" if os.name == "nt" else "zccache-daemon"
            expected_name = "zccache-download-daemon.exe" if os.name == "nt" else "zccache-download-daemon"
            (tmp_root / fallback_name).write_text("daemon", encoding="utf-8")
            destination = tmp_root / "cache" / "llvm.tar.zst"

            def _fake_run(command: list[str], *, capture_output: bool, text: bool, check: bool) -> subprocess.CompletedProcess[str]:
                self.assertTrue((tmp_root / expected_name).exists())
                if command[1:] == ["download", "--help"]:
                    return subprocess.CompletedProcess(command, 0, "Usage: zccache download\n  --part-url <PART_URLS>\n", "")
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"archive")
                return subprocess.CompletedProcess(command, 0, "ok\n", "")

            with patch.dict(os.environ, {"ZCCACHE_BIN": str(binary_path)}, clear=False):
                with patch("tools.install.subprocess.run", side_effect=_fake_run):
                    install._fetch_with_zccache(
                        "https://example.invalid/llvm.tar.zst",
                        destination,
                        "0" * 64,
                    )

            self.assertTrue((tmp_root / expected_name).exists())

    def test_fetch_archive_rejects_missing_part_href(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            match = {
                "archive_filename": "llvm.tar.zst",
                "archive_sha256": "2" * 64,
                "archive_url": "https://example.invalid/llvm.tar.zst",
                "parts": [{"href": "https://example.invalid/llvm.tar.zst.part-aa"}, {}],
            }

            with self.assertRaises(RuntimeError) as exc:
                install._fetch_archive(match, tmp_root / "cache" / "llvm.tar.zst")

        self.assertIn("missing href values", str(exc.exception))

    def test_fetch_archive_uses_zccache_for_mixed_scheme_parts(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            match = {
                "archive_filename": "llvm.tar.zst",
                "archive_sha256": "3" * 64,
                "archive_url": "https://example.invalid/llvm.tar.zst",
                "parts": [
                    {"href": "file:///tmp/llvm.tar.zst.part-aa"},
                    {"href": "https://example.invalid/llvm.tar.zst.part-ab"},
                ],
            }

            with patch("tools.install._fetch_with_zccache") as fetch_mock:
                install._fetch_archive(match, tmp_root / "cache" / "llvm.tar.zst")

        fetch_mock.assert_called_once_with(
            ["file:///tmp/llvm.tar.zst.part-aa", "https://example.invalid/llvm.tar.zst.part-ab"],
            tmp_root / "cache" / "llvm.tar.zst",
            "3" * 64,
        )

    def test_ensure_cached_hash_mismatch_removes_tmp_file(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            archive_path = tmp_root / "archives" / "llvm.tar.zst"
            _make_test_archive(archive_path, {"bin/clang": b"#!/bin/sh\necho clang\n"})
            match = _make_match(archive_path)
            match["archive_sha256"] = "0" * 64
            home_dir = tmp_root / "home"

            def _fake_fetch(source: str | list[str], destination: Path, expected_sha256: str) -> None:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(archive_path, destination)

            with patch("tools.install._fetch_with_zccache", side_effect=_fake_fetch):
                with self.assertRaises(RuntimeError) as exc:
                    install._ensure_cached(match, home_dir)

            cache_path = install.get_cache_path(
                match["component"],
                match.get("platform"),
                match.get("arch"),
                match["archive_sha256"],
                home_dir,
            )
            self.assertFalse(cache_path.exists())
            self.assertFalse(cache_path.with_suffix(".tmp").exists())
            self.assertIn("hash mismatch", str(exc.exception))

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
