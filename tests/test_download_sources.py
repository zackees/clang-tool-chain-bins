from __future__ import annotations

import unittest
from pathlib import PurePosixPath

from tools.download_sources import (
    DownloadKind,
    asset_repo_relative_path_from_url,
    build_asset_download_descriptor,
    classify_download_kind,
)


class DownloadSourceTests(unittest.TestCase):
    def test_classify_known_assets(self) -> None:
        cases = [
            ("assets/clang/darwin/arm64/llvm-21.1.6-darwin-arm64.tar.zst", DownloadKind.LFS),
            ("assets/clang/win/x86_64/llvm-21.1.5-win-x86_64.tar.zst", DownloadKind.RAW),
            ("assets/cosmocc/cosmocc-universal-4.0.2.tar.zst", DownloadKind.LFS),
        ]
        for asset_path, expected_kind in cases:
            with self.subTest(asset_path=asset_path):
                self.assertEqual(classify_download_kind(asset_path), expected_kind)

    def test_gitattributes_rules_cover_existing_and_future_part_files(self) -> None:
        self.assertEqual(
            classify_download_kind("assets/emscripten/win/x86_64/emscripten-future-win-x86_64.tar.zst.part-aa"),
            DownloadKind.RAW,
        )
        self.assertEqual(
            classify_download_kind("assets/cosmocc/future-cosmocc.tar.zst.part-aa"),
            DownloadKind.LFS,
        )

    def test_multipart_descriptor_uses_part_urls_as_probe_urls(self) -> None:
        descriptor = build_asset_download_descriptor(
            "assets/iwyu/linux/x86_64/iwyu-0.25-linux-x86_64-fixed.tar.zst",
            part_asset_paths=[
                "assets/iwyu/linux/x86_64/iwyu-0.25-linux-x86_64-fixed.tar.zst.part-aa",
                "assets/iwyu/linux/x86_64/iwyu-0.25-linux-x86_64-fixed.tar.zst.part-ab",
                "assets/iwyu/linux/x86_64/iwyu-0.25-linux-x86_64-fixed.tar.zst.part-ac",
            ],
        )
        self.assertEqual(descriptor.kind, DownloadKind.MULTIPART)
        self.assertEqual(
            descriptor.href,
            "https://raw.githubusercontent.com/zackees/clang-tool-chain-bins/main/assets/iwyu/linux/x86_64/iwyu-0.25-linux-x86_64-fixed.tar.zst",
        )
        self.assertEqual(
            descriptor.probe_urls,
            (
                "https://raw.githubusercontent.com/zackees/clang-tool-chain-bins/main/assets/iwyu/linux/x86_64/iwyu-0.25-linux-x86_64-fixed.tar.zst.part-aa",
                "https://raw.githubusercontent.com/zackees/clang-tool-chain-bins/main/assets/iwyu/linux/x86_64/iwyu-0.25-linux-x86_64-fixed.tar.zst.part-ab",
                "https://raw.githubusercontent.com/zackees/clang-tool-chain-bins/main/assets/iwyu/linux/x86_64/iwyu-0.25-linux-x86_64-fixed.tar.zst.part-ac",
            ),
        )

    def test_asset_repo_relative_path_from_url_supports_raw_and_media(self) -> None:
        self.assertEqual(
            asset_repo_relative_path_from_url(
                "https://raw.githubusercontent.com/zackees/clang-tool-chain-bins/main/assets/nodejs/win/x86_64/nodejs-22.11.0-win-x86_64.tar.zst"
            ),
            PurePosixPath("assets/nodejs/win/x86_64/nodejs-22.11.0-win-x86_64.tar.zst"),
        )
        self.assertEqual(
            asset_repo_relative_path_from_url(
                "https://media.githubusercontent.com/media/zackees/clang-tool-chain-bins/main/assets/clang/darwin/arm64/llvm-21.1.6-darwin-arm64.tar.zst"
            ),
            PurePosixPath("assets/clang/darwin/arm64/llvm-21.1.6-darwin-arm64.tar.zst"),
        )


if __name__ == "__main__":
    unittest.main()
