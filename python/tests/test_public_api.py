"""Tests for the public Python API backed by Rust native bindings."""

import os
import tempfile
import pytest

# Skip all tests if native extension is not built
native = pytest.importorskip("clang_tool_chain_bins._native")


def test_sha256_file():
    """SHA256 of a known file."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
        f.write(b"hello world")
        path = f.name
    try:
        result = native.sha256_file(path)
        assert isinstance(result, str)
        assert len(result) == 64  # SHA256 hex length
        # Known SHA256 of "hello world"
        assert result == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
    finally:
        os.unlink(path)


def test_md5_file():
    """MD5 of a known file."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
        f.write(b"hello world")
        path = f.name
    try:
        result = native.md5_file(path)
        assert isinstance(result, str)
        assert len(result) == 32  # MD5 hex length
        assert result == "5eb63bbbe01eeed093cb22bb8f5acdc3"
    finally:
        os.unlink(path)


def test_sha256_verify_matching():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
        f.write(b"hello world")
        path = f.name
    try:
        assert native.sha256_verify(path, "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9")
    finally:
        os.unlink(path)


def test_sha256_verify_non_matching():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
        f.write(b"hello world")
        path = f.name
    try:
        assert not native.sha256_verify(path, "0000000000000000000000000000000000000000000000000000000000000000")
    finally:
        os.unlink(path)


def test_lfs_media_url():
    url = native.lfs_media_url("clang/win/x86_64/test.tar.zst", "main")
    assert "media.githubusercontent.com" in url
    assert "clang/win/x86_64/test.tar.zst" in url


def test_lfs_media_url_default_branch():
    url = native.lfs_media_url("test.tar.zst")
    assert "main" in url


def test_expand_archive_file_not_found():
    with pytest.raises(FileNotFoundError):
        native.expand_archive("/nonexistent/path.tar.zst", "/tmp/out")


def test_tar_zst_roundtrip():
    """Create and expand a tar.zst archive."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create source files
        src = os.path.join(tmpdir, "source")
        os.makedirs(src)
        with open(os.path.join(src, "test.txt"), "w") as f:
            f.write("hello from rust")
        with open(os.path.join(src, "data.bin"), "wb") as f:
            f.write(b"\x00" * 1024)

        # Create archive
        archive = os.path.join(tmpdir, "test.tar.zst")
        native.create_tar_zst(src, archive, 3)
        assert os.path.exists(archive)

        # Expand archive
        out = os.path.join(tmpdir, "output")
        native.expand_archive(archive, out)

        # Verify contents
        # Note: the archive preserves directory structure,
        # so files will be under out/ somewhere
        found = False
        for root, dirs, files in os.walk(out):
            if "test.txt" in files:
                with open(os.path.join(root, "test.txt")) as f:
                    assert f.read() == "hello from rust"
                found = True
        assert found, "test.txt not found in extracted archive"
