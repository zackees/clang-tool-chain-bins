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
        self.aggregate_index_path = self.repo_root / "tools" / "data" / "tool-index.json"
        self.meta_index_path = self.repo_root / "tools" / "data" / "index-meta.json"
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

    def test_aggregate_and_meta_indexes_cover_all_sidecars(self) -> None:
        self.assertTrue(self.aggregate_index_path.exists(), "missing aggregate tool index")
        self.assertTrue(self.meta_index_path.exists(), "missing meta index")

        aggregate_data = json.loads(self.aggregate_index_path.read_text(encoding="utf-8"))
        meta_data = json.loads(self.meta_index_path.read_text(encoding="utf-8"))

        self.assertEqual(aggregate_data.get("schema_version"), 1)
        self.assertEqual(meta_data.get("schema_version"), 1)
        self.assertEqual(meta_data.get("aggregate_index_path"), "tool-index.json")

        expected_index_paths = {str(Path(f"{archive}.json").relative_to(self.assets_root)) for archive in self.archives}
        aggregate_entries = {entry["index_path"]: entry for entry in aggregate_data.get("archives", [])}
        aggregate_index_paths = set(aggregate_entries)
        meta_index_paths = {entry["index_path"] for entry in meta_data.get("indexes", [])}

        self.assertEqual(aggregate_data.get("archive_count"), len(expected_index_paths))
        self.assertEqual(meta_data.get("index_count"), len(expected_index_paths))
        self.assertEqual(aggregate_index_paths, expected_index_paths)
        self.assertEqual(meta_index_paths, expected_index_paths)

        for entry in meta_data.get("indexes", []):
            with self.subTest(index_path=entry["index_path"]):
                self.assertIn("archive_path", entry)
                self.assertIn("component", entry)
                self.assertIn("archive_sha256", entry)
                self.assertTrue((self.assets_root / entry["index_path"]).exists(), f"missing indexed sidecar {entry['index_path']}")
                aggregate_entry = aggregate_entries[entry["index_path"]]
                self.assertEqual(entry["archive_path"], aggregate_entry["relative_path"])
                self.assertEqual(entry["component"], aggregate_entry["component"])
                self.assertEqual(entry["archive_sha256"], aggregate_entry["sha256"])


if __name__ == "__main__":
    unittest.main()
