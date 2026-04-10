from __future__ import annotations

import json
import os
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from tools.download_sources import asset_repo_relative_path_from_url, build_download_descriptor


URL_CHECK_TIMEOUT_ENV = "CLANG_TOOL_CHAIN_BINS_URL_CHECK_TIMEOUT"
URL_CHECK_WORKERS_ENV = "CLANG_TOOL_CHAIN_BINS_URL_CHECK_WORKERS"
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_WORKERS = 8
HEAD_FALLBACK_STATUS_CODES = {403, 405, 501}
GITHUB_CONTENT_HOSTS = {
    "media.githubusercontent.com",
    "raw.githubusercontent.com",
}
GIT_LFS_POINTER_PREFIX = b"version https://git-lfs.github.com/spec/v1"
SAMPLE_BYTES = 256
PLACEHOLDER_TOKENS = {
    "TO_BE_GENERATED_DURING_BUILD",
}


def _walk_download_entries(node: Any, *, manifest_path: str, entries: list[dict[str, Any]]) -> None:
    if isinstance(node, dict):
        href = node.get("href")
        parts = node.get("parts")
        if isinstance(href, str) and href.startswith(("http://", "https://")) and (
            "sha256" in node or isinstance(parts, list)
        ):
            entries.append(
                {
                    "manifest_path": manifest_path,
                    "href": href,
                    "part_urls": [
                        part["href"]
                        for part in parts
                        if isinstance(part, dict) and isinstance(part.get("href"), str)
                    ]
                    if isinstance(parts, list)
                    else [],
                }
            )
            for key, value in node.items():
                if key != "parts":
                    _walk_download_entries(value, manifest_path=manifest_path, entries=entries)
            return

        for value in node.values():
            _walk_download_entries(value, manifest_path=manifest_path, entries=entries)
        return

    if isinstance(node, list):
        for item in node:
            _walk_download_entries(item, manifest_path=manifest_path, entries=entries)


def _collect_manifest_download_entries(assets_root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for manifest_path in sorted(assets_root.rglob("manifest*.json")):
        text = manifest_path.read_text(encoding="utf-8")
        data = json.loads(text)
        entry_count_before = len(entries)
        _walk_download_entries(
            data,
            manifest_path=str(manifest_path.relative_to(assets_root)),
            entries=entries,
        )
        for token in PLACEHOLDER_TOKENS:
            if token in text:
                entries.append(
                    {
                        "manifest_path": str(manifest_path.relative_to(assets_root)),
                        "href": token,
                        "part_urls": [],
                        "placeholder": True,
                    }
                )
        if len(entries) == entry_count_before:
            continue
    return entries


def _collect_manifest_probe_urls(assets_root: Path) -> list[dict[str, Any]]:
    urls: dict[str, set[str]] = {}
    for entry in _collect_manifest_download_entries(assets_root):
        if entry.get("placeholder"):
            continue
        probe_urls = entry["part_urls"] or [entry["href"]]
        for url in probe_urls:
            urls.setdefault(url, set()).add(entry["manifest_path"])

    return [
        {
            "url": url,
            "manifest_paths": sorted(manifest_paths),
        }
        for url, manifest_paths in sorted(urls.items())
    ]


def _probe_url_once(url: str, *, method: str, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        method=method,
        headers={"User-Agent": "clang-tool-chain-bins-url-check/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = {
            "method": method,
            "status": getattr(response, "status", response.getcode()),
            "final_url": response.geturl(),
            "content_type": response.headers.get_content_type(),
        }
        if method == "GET":
            sample = response.read(SAMPLE_BYTES)
            result["pointer_response"] = sample.startswith(GIT_LFS_POINTER_PREFIX)
            result["sample_preview"] = sample[:80].decode("utf-8", errors="replace")
        return result


def _probe_url(url: str, *, timeout: int) -> dict[str, Any]:
    hostname = (urlparse(url).hostname or "").lower()
    if hostname in GITHUB_CONTENT_HOSTS:
        try:
            return _probe_url_once(url, method="GET", timeout=timeout)
        except urllib.error.HTTPError as exc:
            return {
                "method": "GET",
                "status": exc.code,
                "final_url": url,
                "error": str(exc),
            }
        except urllib.error.URLError as exc:
            return {
                "method": "GET",
                "status": None,
                "final_url": url,
                "error": str(exc),
            }

    try:
        return _probe_url_once(url, method="HEAD", timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code not in HEAD_FALLBACK_STATUS_CODES:
            return {
                "method": "HEAD",
                "status": exc.code,
                "final_url": url,
                "error": str(exc),
            }
    except urllib.error.URLError as exc:
        return {
            "method": "HEAD",
            "status": None,
            "final_url": url,
            "error": str(exc),
        }

    try:
        return _probe_url_once(url, method="GET", timeout=timeout)
    except urllib.error.HTTPError as exc:
        return {
            "method": "GET",
            "status": exc.code,
            "final_url": url,
            "error": str(exc),
        }
    except urllib.error.URLError as exc:
        return {
            "method": "GET",
            "status": None,
            "final_url": url,
            "error": str(exc),
        }


class DownloadUrlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.assets_root = self.repo_root / "assets"

    def test_manifest_download_urls_match_delivery_rules(self) -> None:
        entries = _collect_manifest_download_entries(self.assets_root)
        self.assertTrue(entries, "expected at least one manifest download entry")

        failures: list[str] = []
        for entry in entries:
            manifest_path = entry["manifest_path"]
            if entry.get("placeholder"):
                failures.append(f"placeholder token in {manifest_path}: {entry['href']}")
                continue

            archive_repo_path = asset_repo_relative_path_from_url(entry["href"])
            if archive_repo_path is None:
                failures.append(f"unparseable archive href in {manifest_path}: {entry['href']}")
                continue

            part_repo_paths = []
            invalid_part_urls: list[str] = []
            for part_url in entry["part_urls"]:
                part_repo_path = asset_repo_relative_path_from_url(part_url)
                if part_repo_path is None:
                    invalid_part_urls.append(part_url)
                    continue
                part_repo_paths.append(part_repo_path)
            if invalid_part_urls:
                failures.append(f"unparseable part hrefs in {manifest_path}: {', '.join(invalid_part_urls)}")
                continue

            descriptor = build_download_descriptor(
                archive_repo_path,
                repo_root=self.repo_root,
                part_repo_relative_paths=part_repo_paths,
            )
            actual_probe_urls = entry["part_urls"] or [entry["href"]]
            expected_probe_urls = list(descriptor.probe_urls)

            if entry["href"] != descriptor.href:
                failures.append(
                    f"href mismatch in {manifest_path}: expected {descriptor.href}, found {entry['href']}"
                )
            if actual_probe_urls != expected_probe_urls:
                failures.append(
                    f"probe URL mismatch in {manifest_path}: expected {expected_probe_urls}, found {actual_probe_urls}"
                )

        self.assertFalse(
            failures,
            "manifest delivery mismatches:\n" + "\n".join(failures[:50]),
        )

    def test_all_manifest_download_urls_resolve_to_final_2xx(self) -> None:
        timeout = int(os.environ.get(URL_CHECK_TIMEOUT_ENV, DEFAULT_TIMEOUT_SECONDS))
        max_workers = int(os.environ.get(URL_CHECK_WORKERS_ENV, DEFAULT_WORKERS))
        url_entries = _collect_manifest_probe_urls(self.assets_root)

        self.assertTrue(url_entries, "expected at least one manifest download URL")

        failures: list[str] = []
        redirects: list[str] = []

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(_probe_url, entry["url"], timeout=timeout): entry for entry in url_entries
            }
            for future in as_completed(future_map):
                entry = future_map[future]
                result = future.result()
                status = result.get("status")
                final_url = result.get("final_url")
                pointer_response = bool(result.get("pointer_response"))

                if pointer_response or not isinstance(status, int) or status // 100 != 2:
                    failures.append(
                        " | ".join(
                            [
                                f"status={status}",
                                f"method={result.get('method')}",
                                f"url={entry['url']}",
                                f"final_url={final_url}",
                                f"pointer_response={pointer_response}",
                                f"content_type={result.get('content_type')}",
                                f"sample={result.get('sample_preview')}",
                                f"manifests={','.join(entry['manifest_paths'])}",
                                f"error={result.get('error')}",
                            ]
                        )
                    )
                    continue

                if final_url and final_url != entry["url"]:
                    redirects.append(f"{entry['url']} -> {final_url}")

        self.assertFalse(
            failures,
            "download URL probe failures:\n" + "\n".join(failures[:50]),
        )

        if redirects:
            self.assertTrue(all("->" in redirect for redirect in redirects))


if __name__ == "__main__":
    unittest.main()
