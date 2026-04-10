from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tools import query
from tools.archive_index import build_aggregate_index


class _FakeResponse(io.StringIO):
    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


class QueryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.assets_root = self.repo_root / "assets"
        self.index_path = self.repo_root / "tools" / "data" / "tool-index.json"
        query._reset_remote_index_cache()
        if not self.index_path.exists():
            build_aggregate_index(self.assets_root, self.index_path)

    def test_clang_star_query_returns_json_lines_with_one_match_per_line(self) -> None:
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
            self.assertIn("source_urls", match)
            self.assertIn("local_cache_path", match)
            self.assertIn("install_path", match)
            self.assertIn("installed", match)
            self.assertIn("tool_name", match)
            self.assertTrue(match["url"].startswith("http"))
            self.assertEqual(match["source_urls"], [match["archive_url"]])
            self.assertTrue(match["local_cache_path"].endswith(".tar.zst"))
            self.assertFalse(match["installed"])

        jsonl = query.format_query_results(results)
        parsed = [json.loads(line) for line in jsonl.splitlines()]
        self.assertTrue(parsed)
        self.assertTrue(all(line["query"] == "clang*" for line in parsed))
        self.assertTrue(all("tool_name" in line for line in parsed))
        self.assertEqual(len(parsed), len(payload["matches"]))

    def test_exact_clang_query_only_returns_clang_tools(self) -> None:
        with TemporaryDirectory() as tmp:
            home_dir = Path(tmp)
            results = query.query_records(["clang"], home_dir=home_dir, index_path=self.index_path)

        payload = results[0]
        self.assertTrue(payload["matches"])
        self.assertTrue(all(match["tool_name"] == "clang" for match in payload["matches"]))

    def test_exact_clangxx_query_returns_clangxx_tools(self) -> None:
        with TemporaryDirectory() as tmp:
            home_dir = Path(tmp)
            results = query.query_records(["clang++"], home_dir=home_dir, index_path=self.index_path)

        payload = results[0]
        self.assertTrue(payload["matches"])
        self.assertTrue(all(match["tool_name"] == "clang++" for match in payload["matches"]))

    def test_exact_query_does_not_match_component_name_only(self) -> None:
        records = [
            query.ToolRecord(
                tool_name="llvm-ar",
                file_name="llvm-ar",
                path_in_archive="bin/llvm-ar",
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
            results = query.query_records(["clang"], records=records, home_dir=home_dir)

        self.assertEqual(results[0]["matches"], [])

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

    def test_multiple_patterns_return_one_json_line_per_match(self) -> None:
        with TemporaryDirectory() as tmp:
            home_dir = Path(tmp)
            results = query.query_records(["clang*", "node"], home_dir=home_dir, index_path=self.index_path)

        self.assertEqual([result["query"] for result in results], ["clang*", "node"])
        jsonl = query.format_query_results(results)
        lines = jsonl.splitlines()
        parsed = [json.loads(line) for line in lines]
        self.assertTrue(any(line["query"] == "clang*" for line in parsed))
        self.assertTrue(any(line["query"] == "node" for line in parsed))
        self.assertTrue(all("tool_name" in line for line in parsed))
        expected_line_count = sum(len(result["matches"]) for result in results)
        self.assertEqual(len(lines), expected_line_count)

    def test_pretty_output_contains_compact_summary_columns(self) -> None:
        results = [
            {
                "query": "clang++",
                "matches": [
                    {
                        "tool_name": "clang++",
                        "installed": False,
                        "version": "21.1.5",
                        "size": 16896,
                        "platform": "win",
                        "arch": "x86_64",
                        "archive_filename": "llvm-21.1.5-win-x86_64.tar.zst",
                        "archive_url": "https://example.invalid/llvm-21.1.5-win-x86_64.tar.zst",
                        "path_in_archive": "bin/clang++.exe",
                        "install_path": r"C:\tmp\clang\win\x86_64",
                    }
                ],
            }
        ]

        pretty = query.format_pretty_results(results)
        self.assertIn("Query: clang++", pretty)
        self.assertIn("Tool", pretty)
        self.assertIn("Installed", pretty)
        self.assertIn("Version", pretty)
        self.assertIn("Size", pretty)
        self.assertIn("no", pretty)
        self.assertIn("16.5 KiB", pretty)
        self.assertIn("Source URL: https://example.invalid/llvm-21.1.5-win-x86_64.tar.zst", pretty)
        self.assertIn(r"Install Path: C:\tmp\clang\win\x86_64", pretty)

    def test_pretty_output_groups_installed_matches_by_install_path(self) -> None:
        results = [
            {
                "query": "clang++",
                "matches": [
                    {
                        "tool_name": "clang++",
                        "installed": True,
                        "version": "21.1.5",
                        "size": 16896,
                        "platform": "win",
                        "arch": "x86_64",
                        "archive_filename": "llvm-21.1.5-win-x86_64.tar.zst",
                        "archive_url": "https://example.invalid/llvm-21.1.5-win-x86_64.tar.zst",
                        "path_in_archive": "bin/clang++.exe",
                        "install_path": r"C:\tmp\clang\win\x86_64",
                    },
                    {
                        "tool_name": "clang",
                        "installed": True,
                        "version": "21.1.5",
                        "size": 1024,
                        "platform": "win",
                        "arch": "x86_64",
                        "archive_filename": "llvm-21.1.5-win-x86_64.tar.zst",
                        "archive_url": "https://example.invalid/llvm-21.1.5-win-x86_64.tar.zst",
                        "path_in_archive": "bin/clang.exe",
                        "install_path": r"C:\tmp\clang\win\x86_64",
                    },
                ],
            }
        ]

        pretty = query.format_pretty_results(results)
        self.assertIn("Installed Matches", pretty)
        self.assertIn(r"C:\tmp\clang\win\x86_64", pretty)
        self.assertIn("|-- clang (21.1.5, 1.0 KiB)", pretty)
        self.assertIn("|-- clang++ (21.1.5, 16.5 KiB)", pretty)
        self.assertIn("Match Details", pretty)

    def test_query_records_uses_part_urls_as_source_urls(self) -> None:
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
                parts=[
                    {"href": "https://example.invalid/llvm-21.1.5-linux-x86_64.tar.zst.part-aa"},
                    {"href": "https://example.invalid/llvm-21.1.5-linux-x86_64.tar.zst.part-ab"},
                ],
            )
        ]

        with TemporaryDirectory() as tmp:
            home_dir = Path(tmp)
            results = query.query_records(["clang"], records=records, home_dir=home_dir)

        self.assertEqual(
            results[0]["matches"][0]["source_urls"],
            [
                "https://example.invalid/llvm-21.1.5-linux-x86_64.tar.zst.part-aa",
                "https://example.invalid/llvm-21.1.5-linux-x86_64.tar.zst.part-ab",
            ],
        )

    def test_pretty_output_handles_no_matches(self) -> None:
        pretty = query.format_pretty_results([{"query": "missing-tool", "matches": []}])
        self.assertIn("Query: missing-tool", pretty)
        self.assertIn("No matches.", pretty)

    def test_query_main_pretty_flag_outputs_text(self) -> None:
        local_payload = {
            "tools": [
                {
                    "tool_name": "clang++",
                    "file_name": "clang++.exe",
                    "path_in_archive": "bin/clang++.exe",
                    "tool_sha256": "aaa",
                    "tool_type": "file",
                    "size": 16896,
                    "component": "clang",
                    "version": "21.1.5",
                    "platform": "win",
                    "arch": "x86_64",
                    "archive_path": "clang/win/x86_64/llvm-21.1.5-win-x86_64.tar.zst",
                    "archive_filename": "llvm-21.1.5-win-x86_64.tar.zst",
                    "archive_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                    "archive_url": "https://example.invalid/llvm-21.1.5-win-x86_64.tar.zst",
                    "parts": [],
                }
            ]
        }

        with TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            index_path = tmp_root / "tool-index.json"
            index_path.write_text(json.dumps(local_payload), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = query.main(["clang++", "--pretty", "--index", str(index_path), "--home-dir", str(tmp_root)])

        self.assertEqual(exit_code, 0)
        pretty = output.getvalue()
        self.assertIn("Query: clang++", pretty)
        self.assertIn("Installed", pretty)
        self.assertIn("Source URL:", pretty)
        self.assertNotIn('"query"', pretty)

    def test_no_matches_returns_empty_matches_array(self) -> None:
        with TemporaryDirectory() as tmp:
            home_dir = Path(tmp)
            results = query.query_records(["tool-that-does-not-exist"], home_dir=home_dir, index_path=self.index_path)

        self.assertEqual(results, [{"query": "tool-that-does-not-exist", "matches": []}])

    def test_format_query_results_emits_no_match_marker(self) -> None:
        jsonl = query.format_query_results([{"query": "tool-that-does-not-exist", "matches": []}])
        parsed = [json.loads(line) for line in jsonl.splitlines()]
        self.assertEqual(parsed, [{"matched": False, "query": "tool-that-does-not-exist"}])

    def test_exact_lookup_finds_llvm_pdbutil(self) -> None:
        with TemporaryDirectory() as tmp:
            home_dir = Path(tmp)
            results = query.query_records(["llvm-pdbutil"], home_dir=home_dir, index_path=self.index_path)

        self.assertEqual(len(results), 1)
        payload = results[0]
        self.assertEqual(payload["query"], "llvm-pdbutil")
        self.assertTrue(payload["matches"])
        self.assertTrue(any(match["tool_name"] == "llvm-pdbutil" for match in payload["matches"]))

    def test_glob_lookup_only_matches_tool_name_or_filename(self) -> None:
        with TemporaryDirectory() as tmp:
            home_dir = Path(tmp)
            results = query.query_records(["llvm-*"], home_dir=home_dir, index_path=self.index_path)

        payload = results[0]
        self.assertTrue(payload["matches"])
        for match in payload["matches"]:
            self.assertTrue(
                query.fnmatchcase(match["tool_name"].lower(), "llvm-*")
                or query.fnmatchcase(match["file_name"].lower(), "llvm-*")
            )

    def test_exe_filename_query_matches_normalized_tool(self) -> None:
        records = [
            query.ToolRecord(
                tool_name="clang++",
                file_name="clang++.exe",
                path_in_archive="bin/clang++.exe",
                tool_sha256="aaa",
                tool_type="file",
                size=1,
                component="clang",
                version="21.1.5",
                platform="win",
                arch="x86_64",
                archive_path="clang/win/x86_64/llvm-21.1.5-win-x86_64.tar.zst",
                archive_filename="llvm-21.1.5-win-x86_64.tar.zst",
                archive_sha256="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                archive_url="https://example.invalid/llvm-21.1.5-win-x86_64.tar.zst",
                parts=[],
            )
        ]

        with TemporaryDirectory() as tmp:
            home_dir = Path(tmp)
            results = query.query_records(["clang++.exe"], records=records, home_dir=home_dir)

        self.assertEqual(len(results[0]["matches"]), 1)
        self.assertEqual(results[0]["matches"][0]["tool_name"], "clang++")

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

    def test_local_index_accepts_json5(self) -> None:
        json5_payload = """{
          // trailing comma + comments should parse
          tools: [
            {
              tool_name: 'clang',
              file_name: 'clang',
              path_in_archive: 'bin/clang',
              tool_sha256: 'aaa',
              tool_type: 'file',
              size: 1,
              component: 'clang',
              version: '21.1.5',
              platform: 'linux',
              arch: 'x86_64',
              archive_path: 'clang/linux/x86_64/llvm-21.1.5-linux-x86_64.tar.zst',
              archive_filename: 'llvm-21.1.5-linux-x86_64.tar.zst',
              archive_sha256: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
              archive_url: 'https://example.invalid/llvm-21.1.5-linux-x86_64.tar.zst',
              parts: [],
            },
          ],
        }"""

        with TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            index_path = tmp_root / "tool-index.json"
            index_path.write_text(json5_payload, encoding="utf-8")
            home_dir = tmp_root / "home"
            results = query.query_records(["clang"], home_dir=home_dir, index_path=index_path)

        self.assertEqual(len(results[0]["matches"]), 1)
        self.assertEqual(results[0]["matches"][0]["tool_name"], "clang")

    def test_remote_index_is_fetched_once_per_launch(self) -> None:
        remote_payload = {
            "tools": [
                {
                    "tool_name": "clang",
                    "file_name": "clang",
                    "path_in_archive": "bin/clang",
                    "tool_sha256": "aaa",
                    "tool_type": "file",
                    "size": 1,
                    "component": "clang",
                    "version": "21.1.5",
                    "platform": "linux",
                    "arch": "x86_64",
                    "archive_path": "clang/linux/x86_64/llvm-21.1.5-linux-x86_64.tar.zst",
                    "archive_filename": "llvm-21.1.5-linux-x86_64.tar.zst",
                    "archive_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                    "archive_url": "https://example.invalid/llvm-21.1.5-linux-x86_64.tar.zst",
                    "parts": [],
                }
            ]
        }

        with TemporaryDirectory() as tmp:
            home_dir = Path(tmp)
            with (
                patch("tools.query._discover_remote_index_url", return_value="https://example.invalid/tool-index.json"),
                patch(
                    "tools.query.urllib.request.urlopen",
                    side_effect=lambda url: _FakeResponse(json.dumps(remote_payload)),
                ) as urlopen_mock,
            ):
                first = query.query_records(["clang"], home_dir=home_dir)
                second = query.query_records(["clang"], home_dir=home_dir)

        self.assertEqual(len(first[0]["matches"]), 1)
        self.assertEqual(len(second[0]["matches"]), 1)
        self.assertEqual(urlopen_mock.call_count, 1)

    def test_remote_index_accepts_json5(self) -> None:
        remote_payload = """{
          tools: [
            {
              tool_name: 'clang',
              file_name: 'clang',
              path_in_archive: 'bin/clang',
              tool_sha256: 'aaa',
              tool_type: 'file',
              size: 1,
              component: 'clang',
              version: '21.1.5',
              platform: 'linux',
              arch: 'x86_64',
              archive_path: 'clang/linux/x86_64/llvm-21.1.5-linux-x86_64.tar.zst',
              archive_filename: 'llvm-21.1.5-linux-x86_64.tar.zst',
              archive_sha256: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
              archive_url: 'https://example.invalid/llvm-21.1.5-linux-x86_64.tar.zst',
              parts: [],
            },
          ],
        }"""

        with TemporaryDirectory() as tmp:
            home_dir = Path(tmp)
            with (
                patch("tools.query._discover_remote_index_url", return_value="https://example.invalid/tool-index.json"),
                patch("tools.query.urllib.request.urlopen", return_value=_FakeResponse(remote_payload)),
            ):
                results = query.query_records(["clang"], home_dir=home_dir)

        self.assertEqual(len(results[0]["matches"]), 1)
        self.assertEqual(results[0]["matches"][0]["tool_name"], "clang")

    def test_repo_checkout_can_discover_remote_index_url(self) -> None:
        url = query._discover_remote_index_url()
        self.assertIsNotNone(url)
        self.assertIn("raw.githubusercontent.com/zackees/clang-tool-chain-bins/", url)
        self.assertTrue(url.endswith("/tools/data/tool-index.json"))

    def test_remote_fetch_falls_back_to_local_index(self) -> None:
        local_payload = {
            "tools": [
                {
                    "tool_name": "clang++",
                    "file_name": "clang++",
                    "path_in_archive": "bin/clang++",
                    "tool_sha256": "aaa",
                    "tool_type": "file",
                    "size": 1,
                    "component": "clang",
                    "version": "21.1.5",
                    "platform": "linux",
                    "arch": "x86_64",
                    "archive_path": "clang/linux/x86_64/llvm-21.1.5-linux-x86_64.tar.zst",
                    "archive_filename": "llvm-21.1.5-linux-x86_64.tar.zst",
                    "archive_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                    "archive_url": "https://example.invalid/llvm-21.1.5-linux-x86_64.tar.zst",
                    "parts": [],
                }
            ]
        }

        with TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            local_index_path = tmp_root / "tool-index.json"
            local_index_path.write_text(json.dumps(local_payload), encoding="utf-8")
            home_dir = tmp_root / "home"
            with (
                patch("tools.query._discover_remote_index_url", return_value="https://example.invalid/tool-index.json"),
                patch("tools.query.urllib.request.urlopen", side_effect=RuntimeError("boom")),
                patch("tools.query.aggregate_index_path", return_value=local_index_path),
            ):
                results = query.query_records(["clang++"], home_dir=home_dir)

        self.assertEqual(len(results[0]["matches"]), 1)
        self.assertEqual(results[0]["matches"][0]["tool_name"], "clang++")


if __name__ == "__main__":
    unittest.main()
