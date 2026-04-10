from __future__ import annotations

import io
import json
import tarfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pyzstd

import clang_tool_chain_bins as bins
from tools.common import sha256_file


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


def _write_index(index_path: Path, archive_path: Path) -> None:
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
                "download_kind": "file",
                "probe_urls": [archive_path.resolve().as_uri()],
            }
        ],
    }
    index_path.write_text(json.dumps(payload), encoding="utf-8")


class ApiTests(unittest.TestCase):
    def test_query_returns_typed_matches(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            archive_path = tmp_root / "archives" / "llvm.tar.zst"
            _make_test_archive(archive_path, {"bin/clang": b"clang\n"})
            index_path = tmp_root / "tool-index.json"
            _write_index(index_path, archive_path)

            results = bins.query("clang", index_path=index_path, home_dir=tmp_root / "home")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].query, "clang")
        self.assertEqual(len(results[0].matches), 1)
        self.assertEqual(results[0].matches[0].tool_name, "clang")
        self.assertEqual(results[0].matches[0].component, "clang")

    def test_resolve_one_and_is_installed_round_trip(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            archive_path = tmp_root / "archives" / "llvm.tar.zst"
            _make_test_archive(archive_path, {"bin/clang": b"clang\n"})
            index_path = tmp_root / "tool-index.json"
            home_dir = tmp_root / "home"
            _write_index(index_path, archive_path)

            match = bins.resolve_one("clang", index_path=index_path, platform="linux", arch="x86_64", component="clang")
            self.assertEqual(match.archive_filename, archive_path.name)
            self.assertFalse(
                bins.is_installed(
                    "clang",
                    index_path=index_path,
                    home_dir=home_dir,
                    platform="linux",
                    arch="x86_64",
                    version="21.1.5",
                    component="clang",
                )
            )

            result = bins.install(
                "clang",
                index_path=index_path,
                home_dir=home_dir,
                platform="linux",
                arch="x86_64",
                version="21.1.5",
                component="clang",
            )[0]

            self.assertEqual(result.status, "installed")
            self.assertTrue(
                bins.is_installed(
                    "clang",
                    index_path=index_path,
                    home_dir=home_dir,
                    platform="linux",
                    arch="x86_64",
                    version="21.1.5",
                    component="clang",
                )
            )


if __name__ == "__main__":
    unittest.main()
