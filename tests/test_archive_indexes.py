from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.common import sha256_file


def _parse_sha256_sidefile(path: Path) -> str | None:
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        return None
    return content.split()[0]


def _parse_git_lfs_pointer(path: Path) -> dict[str, str] | None:
    if path.stat().st_size > 1024:
        return None
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines or lines[0] != "version https://git-lfs.github.com/spec/v1":
        return None
    payload: dict[str, str] = {}
    for line in lines[1:]:
        if line.startswith("oid sha256:"):
            payload["sha256"] = line.split("oid sha256:", 1)[1]
        elif line.startswith("size "):
            payload["size"] = line.split("size ", 1)[1]
    return payload if "sha256" in payload else None


class ArchiveIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.assets_root = self.repo_root / "assets"
        self.archives = sorted(self.assets_root.rglob("*.tar.zst"))

    def test_every_archive_has_sidecar_json_and_sha256(self) -> None:
        for archive in self.archives:
            with self.subTest(archive=str(archive)):
                self.assertTrue(Path(f"{archive}.json").exists(), f"missing sidecar index for {archive}")
                self.assertTrue(Path(f"{archive}.sha256").exists(), f"missing sha256 side file for {archive}")

    def test_archive_sha256_matches_side_file(self) -> None:
        for archive in self.archives:
            with self.subTest(archive=str(archive)):
                expected = _parse_sha256_sidefile(Path(f"{archive}.sha256"))
                self.assertIsNotNone(expected, f"invalid sha256 side file for {archive}")
                pointer = _parse_git_lfs_pointer(archive)
                actual = pointer["sha256"] if pointer else sha256_file(archive)
                self.assertEqual(actual, expected)

    def test_index_schema_and_identity(self) -> None:
        for archive in self.archives:
            with self.subTest(archive=str(archive)):
                sidecar = Path(f"{archive}.json")
                data = json.loads(sidecar.read_text(encoding="utf-8"))

                self.assertEqual(data.get("schema_version"), 1)
                self.assertIn("archive", data)
                self.assertIn("files", data)
                self.assertIn("tools", data)

                archive_meta = data["archive"]
                self.assertEqual(archive_meta["filename"], archive.name)
                self.assertEqual(archive_meta["relative_path"], str(archive.relative_to(self.assets_root)))
                self.assertEqual(archive_meta["sha256"], _parse_sha256_sidefile(Path(f"{archive}.sha256")))
                self.assertEqual(data["file_count"], len(data["files"]))
                self.assertEqual(data["tool_count"], len(data["tools"]))

                file_paths = {entry["path"] for entry in data["files"]}
                for tool in data["tools"]:
                    self.assertIn("tool_name", tool)
                    self.assertIn("file_name", tool)
                    self.assertIn(tool["path"], file_paths)


if __name__ == "__main__":
    unittest.main()
