from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tools import query
from tools.archive_index import build_aggregate_index


class QueryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.assets_root = self.repo_root / "assets"
        self.index_path = self.repo_root / "tools" / "data" / "tool-index.json"
        if not self.index_path.exists():
            build_aggregate_index(self.assets_root, self.index_path)

    def test_clang_star_query_returns_json_line_with_matches(self) -> None:
        with TemporaryDirectory() as tmp:
            home_dir = Path(tmp)
            results = query.query_records(["clang*"], home_dir=home_dir, index_path=self.index_path)

        self.assertEqual(len(results), 1)
        payload = results[0]
        self.assertEqual(payload["query"], "clang*")
        self.assertTrue(payload["matches"])

        tool_names = {match["tool_name"] for match in payload["matches"]}
        self.assertIn("clang", tool_names)

        for match in payload["matches"]:
            self.assertIn("url", match)
            self.assertIn("local_cache_path", match)
            self.assertIn("installed", match)
            self.assertIn("tool_name", match)
            self.assertTrue(match["url"].startswith("http"))
            self.assertTrue(match["local_cache_path"].endswith(".tar.zst"))
            self.assertFalse(match["installed"])

        jsonl = query.format_query_results(results)
        parsed = [json.loads(line) for line in jsonl.splitlines()]
        self.assertEqual(parsed[0]["query"], "clang*")

    def test_installed_flag_tracks_done_file(self) -> None:
        records = [
            query.ToolRecord(
                tool_name="clang",
                file_name="clang",
                path_in_archive="bin/clang",
                tool_sha256="aaa",
                tool_type="file",
                size=1,
                component="clang",
                version="21.1.5",
                platform="linux",
                arch="x86_64",
                archive_path="clang/linux/x86_64/llvm-21.1.5-linux-x86_64.tar.zst",
                archive_filename="llvm-21.1.5-linux-x86_64.tar.zst",
                archive_sha256="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                archive_url="https://example.invalid/llvm-21.1.5-linux-x86_64.tar.zst",
                parts=[],
            )
        ]

        with TemporaryDirectory() as tmp:
            home_dir = Path(tmp)
            first = query.query_records(["clang"], records=records, home_dir=home_dir)
            self.assertFalse(first[0]["matches"][0]["installed"])

            install_dir = home_dir / "clang" / "linux" / "x86_64"
            install_dir.mkdir(parents=True, exist_ok=True)
            (install_dir / "done.txt").write_text("ok\n", encoding="utf-8")

            second = query.query_records(["clang"], records=records, home_dir=home_dir)
            self.assertTrue(second[0]["matches"][0]["installed"])

    def test_multiple_patterns_return_multiple_json_lines(self) -> None:
        with TemporaryDirectory() as tmp:
            home_dir = Path(tmp)
            results = query.query_records(["clang*", "node"], home_dir=home_dir, index_path=self.index_path)

        self.assertEqual([result["query"] for result in results], ["clang*", "node"])
        jsonl = query.format_query_results(results)
        lines = jsonl.splitlines()
        self.assertEqual(len(lines), 2)
        parsed = [json.loads(line) for line in lines]
        self.assertEqual(parsed[1]["query"], "node")
        self.assertTrue(parsed[1]["matches"])

    def test_exact_lookup_finds_llvm_pdbutil(self) -> None:
        with TemporaryDirectory() as tmp:
            home_dir = Path(tmp)
            results = query.query_records(["llvm-pdbutil"], home_dir=home_dir, index_path=self.index_path)

        self.assertEqual(len(results), 1)
        payload = results[0]
        self.assertEqual(payload["query"], "llvm-pdbutil")
        self.assertTrue(payload["matches"])
        self.assertTrue(any(match["tool_name"] == "llvm-pdbutil" for match in payload["matches"]))

    def test_query_filters_by_platform_arch_and_component(self) -> None:
        with TemporaryDirectory() as tmp:
            home_dir = Path(tmp)
            results = query.query_records(
                ["clang*"],
                home_dir=home_dir,
                index_path=self.index_path,
                platform="linux",
                arch="x86_64",
                component="clang",
            )

        self.assertEqual(len(results), 1)
        payload = results[0]
        self.assertTrue(payload["matches"])
        for match in payload["matches"]:
            self.assertEqual(match["platform"], "linux")
            self.assertEqual(match["arch"], "x86_64")
            self.assertEqual(match["component"], "clang")


if __name__ == "__main__":
    unittest.main()
