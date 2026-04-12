# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

This repository hosts pre-built binary distributions of LLVM/Clang toolchains, Include-What-You-Use (IWYU), MinGW sysroot, Emscripten, and Node.js for multiple platforms. The binaries are ultra-compressed using zstd level 22 compression with hard-link deduplication, achieving ~94% size reduction.

The repository serves as both:
1. **Binary hosting**: GitHub Pages site at https://zackees.github.io/clang-tool-chain-bins/
2. **Build tools**: Rust CLI (`ctcb`) with Python bindings for maintainers to generate and package toolchain archives

## Project Structure

```
clang-tool-chain-bins/
├── Cargo.toml                    # Rust workspace root
├── pyproject.toml                # maturin build backend (PyO3 abi3-py310)
├── crates/                       # Rust workspace crates
│   ├── ctcb-cli/                 # Binary: all subcommands via clap + PyO3 bindings
│   ├── ctcb-core/                # Platform detection, config, formatting helpers
│   ├── ctcb-archive/             # tar create/extract, zstd compress/decompress
│   ├── ctcb-checksum/            # SHA256, MD5 generation and verification
│   ├── ctcb-dedup/               # MD5-based deduplication, hardlink structure
│   ├── ctcb-download/            # HTTP download with progress (reqwest + tokio)
│   ├── ctcb-strip/               # Binary stripping, essential-file filtering
│   ├── ctcb-manifest/            # Two-tier manifest JSON read/write
│   └── ctcb-split/               # Archive splitting for GitHub LFS limits
├── python/                       # Python package (shims + PyO3 bindings)
│   ├── clang_tool_chain_bins/
│   │   ├── __init__.py           # Public API re-exports from _native
│   │   └── cli.py                # Entry point shims → ctcb binary
│   └── tests/
│       └── test_public_api.py    # Python integration tests
├── tools/                        # [LEGACY] Original Python scripts (being phased out)
├── assets/                       # Binary distributions (Git LFS)
│   ├── clang/                    # LLVM/Clang archives by platform/arch
│   ├── iwyu/                     # Include-What-You-Use archives
│   ├── mingw/                    # MinGW sysroot for Windows GNU ABI
│   ├── emscripten/               # Emscripten WebAssembly toolchain
│   └── nodejs/                   # Node.js runtime
├── .github/workflows/
│   ├── ci.yml                    # Lint, test, build wheels (all platforms)
│   └── release.yml               # Tag-triggered release with binaries + wheels
├── index.html                    # GitHub Pages main page
└── index.css                     # Styling
```

## Common Development Commands

### Building (Rust)

```bash
# Build entire workspace
cargo build --workspace

# Build release binary
cargo build --release -p ctcb-cli

# Run tests
cargo test --workspace

# Run a specific subcommand
cargo run -- expand --help
cargo run -- fetch --help
```

### Building (Python + Rust via maturin)

```bash
# Install dependencies and build native extension
uv sync
uv run maturin develop

# Run Python tests
uv run pytest python/tests/ -v

# Build a wheel
uv run maturin build --release
```

### CLI Subcommands (`ctcb`)

```bash
# Master pipeline (download → strip → dedup → hardlink → compress → checksum → manifest)
ctcb fetch --platform win --arch x86_64
ctcb fetch --platform linux --arch x86_64 --source-dir ./extracted

# Individual pipeline steps
ctcb download --version 21.1.5 --current-only
ctcb strip <source_dir> <output_dir> --platform win --arch x86_64
ctcb dedup analyze <directory>
ctcb dedup create <source_dir> <dest_dir>
ctcb hardlink-archive <deduped_dir> <output_dir> --zstd-level 22
ctcb expand <archive.tar.zst> <output_dir> [--verify <sha256>]
ctcb split <archive> --part-size-mb 95
ctcb bench-compression <directory>

# Toolchain-specific packaging
ctcb iwyu --iwyu-root <path> --version 0.25
ctcb mingw-sysroot --arch x86_64
ctcb emscripten --platform linux --arch x86_64
ctcb emscripten-docker --platform linux --arch x86_64
ctcb nodejs --platform win --arch x86_64 --version 22.11.0
```

### Python shim entry points (backward-compatible)

All legacy CLI names still work via Python shims that delegate to `ctcb`:

```bash
uv run fetch-and-archive --platform win --arch x86_64
uv run expand-archive <archive> <output_dir>
uv run download-binaries --current-only
```

### Git LFS Operations

All binary archives use Git LFS. Important commands:

```bash
git lfs ls-files
git lfs pull
git lfs push origin main
```

**CRITICAL**: Archive URLs in manifests must use the GitHub LFS media server format:
```
https://media.githubusercontent.com/media/zackees/clang-tool-chain-bins/refs/heads/main/assets/...
```

NOT the regular GitHub blob URLs.

## Archive Build Pipeline

The `ctcb fetch` command orchestrates a multi-step pipeline:

1. **Download**: Fetch official LLVM releases from GitHub (or use `--source-dir`)
2. **Extract**: Handle `.exe` (Windows via 7z) or `.tar.xz` (Linux/macOS)
3. **Strip**: Remove non-essential files (docs, examples, static libs)
   - Keeps only essential binaries (see `ESSENTIAL_BINARIES` in `ctcb-strip`)
4. **Deduplicate**: Find identical binaries by MD5 hash (~571 MB savings)
5. **Hard-link**: Create hard-linked directory structure
6. **Archive**: TAR with native hard-link support (stores links as metadata)
7. **Compress**: zstd level 22 (ultra-compression, ~17:1 ratio)
8. **Checksum**: Generate SHA256 and MD5 files
9. **Split**: Split archives >99 MB for GitHub LFS limits
10. **Manifest**: Update `manifest.json` with version, URL, and checksum

## Manifest System

Two-tier structure:

**Root manifest** (`assets/{tool}/manifest.json`): Lists platforms and architectures.

**Platform manifest** (`assets/{tool}/{platform}/{arch}/manifest.json`):
```json
{
  "latest": "21.1.5",
  "21.1.5": {
    "href": "https://media.githubusercontent.com/media/.../llvm-21.1.5-win-x86_64.tar.zst",
    "sha256": "3c21e45edeee591fe8ead5427d25b62ddb26c409575b41db03d6777c77bba44f"
  }
}
```

## Key Architecture Decisions

### Rust + Python Bindings
Core logic is in Rust for performance. Python bindings via PyO3 (abi3-py310) enable:
- `pip install clang-tool-chain-bins` with pre-built wheels
- Python API: `from clang_tool_chain_bins import sha256_file, expand_archive`
- CLI shims: legacy Python entry points delegate to the Rust `ctcb` binary

### Hard-Link Deduplication
LLVM contains many duplicate binaries (e.g., `clang.exe` == `clang++.exe`). TAR preserves hard links as metadata, storing data only once.

### Zstd Level 22
Maximum compression (one-time cost for maintainers). Decompression remains fast (~1 second).

## Dependencies

Build requirements:
- Rust 1.85+ (edition 2024)
- Python 3.10+ (for abi3 bindings)
- `uv` (Python package manager)
- `maturin` (Rust-Python build tool)
- 7-Zip (Windows only, for extracting `.exe` LLVM installers)
- Docker (optional, for Emscripten Linux builds)

Key Rust crates: `clap`, `serde`, `zstd`, `tar`, `reqwest`, `pyo3`, `sha2`, `walkdir`, `indicatif`
