"""Clang Toolchain Binary Builder - Rust-powered toolchain packaging."""

__version__ = "0.1.0"

# Try to import native bindings (available when built with maturin)
try:
    from clang_tool_chain_bins._native import (
        expand_archive,
        create_tar_zst,
        sha256_file,
        md5_file,
        sha256_verify,
        generate_checksum_files,
        read_platform_manifest,
        update_platform_manifest,
        lfs_media_url,
    )

    __all__ = [
        "expand_archive",
        "create_tar_zst",
        "sha256_file",
        "md5_file",
        "sha256_verify",
        "generate_checksum_files",
        "read_platform_manifest",
        "update_platform_manifest",
        "lfs_media_url",
    ]
except ImportError:
    # Native extension not built yet (e.g., during development without maturin)
    pass
