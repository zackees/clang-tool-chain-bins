# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

This repository hosts pre-built binary distributions of LLVM/Clang toolchains, Include-What-You-Use (IWYU), MinGW sysroot, and Emscripten for multiple platforms. The binaries are ultra-compressed using zstd level 22 compression with hard-link deduplication, achieving ~94% size reduction.

The repository serves as both:
1. **Binary hosting**: GitHub Pages site at https://zackees.github.io/clang-tool-chain-bins/
2. **Build tools**: Python scripts for maintainers to generate and package toolchain archives

## Project Structure

```
clang-tool-chain-bins/
├── assets/                    # Binary distributions (Git LFS)
│   ├── clang/                # LLVM/Clang archives by platform/arch
│   ├── iwyu/                 # Include-What-You-Use archives
│   ├── mingw/                # MinGW sysroot for Windows GNU ABI
│   └── emscripten/           # Emscripten WebAssembly toolchain
├── tools/                     # Maintainer scripts for building archives
│   ├── fetch_and_archive.py              # Main pipeline for LLVM/Clang
│   ├── fetch_and_archive_emscripten.py   # Emscripten via emsdk
│   ├── fetch_and_archive_emscripten_docker.py  # Emscripten via Docker
│   ├── create_iwyu_archives.py           # IWYU packaging
│   ├── extract_mingw_sysroot.py          # MinGW sysroot extraction
│   └── [component scripts]               # Individual pipeline steps
├── index.html                 # GitHub Pages main page
├── index.css                  # Styling
└── pyproject.toml            # Package configuration with entry points
```

## Common Development Commands

### Building Archives (Maintainer Only)

**LLVM/Clang archives:**
```bash
# Install dependencies
uv sync

# Generate archive for a platform
uv run fetch-and-archive --platform win --arch x86_64
uv run fetch-and-archive --platform linux --arch x86_64
uv run fetch-and-archive --platform linux --arch arm64
uv run fetch-and-archive --platform darwin --arch x86_64
uv run fetch-and-archive --platform darwin --arch arm64

# Or use existing binaries (skip download)
uv run fetch-and-archive --platform win --arch x86_64 --source-dir ./extracted
```

**IWYU archives:**
```bash
uv run create-iwyu-archives
```

**MinGW sysroot:**
```bash
uv run extract-mingw-sysroot --arch x86_64 --work-dir work --output-dir assets/mingw/win
```

**Emscripten archives:**
```bash
# Via Docker (Linux only, recommended for Linux builds)
uv run python tools/fetch_and_archive_emscripten_docker.py --platform linux --arch x86_64

# Via native emsdk (all platforms)
uv run python tools/fetch_and_archive_emscripten.py --platform win --arch x86_64
```

### Testing Archives

```bash
# Extract an archive
uv run expand-archive assets/clang/win/x86_64/llvm-21.1.5-win-x86_64.tar.zst ./test-install

# Verify checksums
sha256sum -c assets/clang/win/x86_64/llvm-21.1.5-win-x86_64.tar.zst.sha256
```

### Git LFS Operations

All binary archives use Git LFS. Important commands:

```bash
# Verify LFS is tracking files correctly
git lfs ls-files

# Pull LFS objects
git lfs pull

# Push LFS objects
git lfs push origin main
```

**CRITICAL**: Archive URLs in manifests must use the GitHub LFS media server format:
```
https://media.githubusercontent.com/media/zackees/clang-tool-chain-bins/refs/heads/main/assets/...
```

NOT the regular GitHub blob URLs.

### Local Development Server

```bash
# Serve the GitHub Pages site locally (requires Python)
python -m http.server 8000
# Visit http://localhost:8000
```

## Archive Build Pipeline

The `fetch_and_archive.py` script orchestrates a multi-step pipeline:

1. **Download**: Fetch official LLVM releases from GitHub (or use `--source-dir`)
2. **Extract**: Handle `.exe` (Windows) or `.tar.xz` (Linux/macOS)
3. **Strip**: Remove non-essential files (docs, examples, static libs)
   - Keeps only essential binaries (compilers, linkers, binary utilities)
   - See `ESSENTIAL_BINARIES` set in `fetch_and_archive.py:72-97`
4. **Deduplicate**: Find identical binaries by MD5 hash (~571 MB savings)
5. **Hard-link**: Create hard-linked directory structure
6. **Archive**: TAR with native hard-link support (stores links as metadata)
7. **Compress**: zstd level 22 (ultra-compression, ~17:1 ratio)
8. **Checksum**: Generate SHA256 and MD5 files
9. **Manifest**: Update `manifest.json` with version and checksum
10. **Place**: Move to `assets/{tool}/{platform}/{arch}/`

**Result**: ~52 MB archive from ~900 MB original (Windows x86_64 example)

## Manifest System

Each toolchain has a two-tier manifest structure:

**Root manifest** (`assets/{tool}/manifest.json`):
```json
{
  "platforms": [
    {
      "platform": "win",
      "architectures": [
        {"arch": "x86_64", "manifest_path": "win/x86_64/manifest.json"}
      ]
    }
  ]
}
```

**Platform manifest** (`assets/{tool}/{platform}/{arch}/manifest.json`):
```json
{
  "latest": "21.1.5",
  "versions": {
    "21.1.5": {
      "version": "21.1.5",
      "href": "https://media.githubusercontent.com/media/.../llvm-21.1.5-win-x86_64.tar.zst",
      "sha256": "3c21e45edeee591fe8ead5427d25b62ddb26c409575b41db03d6777c77bba44f"
    }
  }
}
```

## Supported Platforms

| Tool | Windows x64 | Windows ARM64 | Linux x64 | Linux ARM64 | macOS x64 | macOS ARM64 |
|------|-------------|---------------|-----------|-------------|-----------|-------------|
| **LLVM/Clang** | ✅ | ⚠️ Untested | ✅ | ✅ | ✅ | ✅ |
| **LLVM MinGW** | ✅ | - | - | - | - | - |
| **IWYU** | ✅ | - | ✅ | ✅ | ✅ | ✅ |
| **MinGW Sysroot** | ✅ | - | - | - | - | - |
| **Emscripten** | ✅ | - | ✅ | ✅ | ✅ | ✅ |

## Key Architecture Decisions

### Hard-Link Deduplication
LLVM contains many duplicate binaries (e.g., `clang.exe` == `clang++.exe`). Instead of storing duplicates, we:
1. Create hard links in the directory structure
2. TAR automatically preserves these as link entries (metadata only)
3. Decompression restores hard links on NTFS/ext4/APFS

**Savings**: ~571 MB for Windows x64

### Git LFS for Binaries
Archives are tracked with Git LFS to avoid bloating the repository. The `.gitattributes` file configures LFS for `.tar.zst` files.

### Zstd Level 22
We use maximum compression (level 22) because:
- Compression is one-time cost for maintainers
- Decompression remains fast (~1 second regardless of level)
- Achieves ~94% size reduction vs ~88% at level 10

### Emscripten Docker Approach
Emscripten packaging has two methods:
1. **Docker** (`fetch_and_archive_emscripten_docker.py`): For Linux archives, uses official Docker images
2. **Native emsdk** (`fetch_and_archive_emscripten.py`): For all platforms, requires Python with full stdlib

Use Docker for reproducible Linux builds. Use native emsdk for Windows/macOS.

## Updating LLVM Version

To add a new LLVM version:

1. Update version in `tools/fetch_and_archive.py:41`:
   ```python
   LLVM_VERSION = "21.1.6"  # Update this
   ```

2. Generate archives for all platforms:
   ```bash
   uv run fetch-and-archive --platform win --arch x86_64
   uv run fetch-and-archive --platform linux --arch x86_64
   uv run fetch-and-archive --platform linux --arch arm64
   uv run fetch-and-archive --platform darwin --arch x86_64
   uv run fetch-and-archive --platform darwin --arch arm64
   ```

3. Each script automatically updates its platform manifest (`manifest.json`)

4. Commit and push (including LFS objects):
   ```bash
   git add assets/
   git commit -m "feat: Add LLVM 21.1.6 for all platforms"
   git lfs push origin main  # Push LFS objects first
   git push origin main
   ```

5. Update `index.html` if needed to reflect new versions

## GitHub Pages Deployment

The site automatically deploys from the `main` branch. Any changes to `index.html`, `index.css`, or `assets/` trigger a rebuild.

**Important**: Ensure `.nojekyll` file exists to prevent Jekyll processing.

## Entry Points (pyproject.toml)

The package defines CLI entry points:

- `fetch-and-archive`: Main LLVM/Clang pipeline
- `download-binaries`: Download step only
- `strip-binaries`: Strip unnecessary files
- `deduplicate-binaries`: Find duplicates
- `create-hardlink-archive`: Create TAR with hard links
- `expand-archive`: Extract `.tar.zst` archives
- `test-compression`: Compare compression methods
- `create-iwyu-archives`: Build IWYU archives
- `extract-mingw-sysroot`: Extract MinGW sysroot

## Important Notes

- **End users**: Do NOT need these tools. The main `clang-tool-chain` project downloads binaries automatically.
- **Maintainers**: Use these scripts only when updating binary distributions.
- **Compression time**: zstd-22 takes ~3-4 minutes per archive (vs ~1 second for zstd-3).
- **Windows 7z requirement**: Extracting Windows `.exe` installers requires 7-Zip. Use `--source-dir` to skip download.
- **MSYS2 Python**: May have `_ctypes` issues. Use Docker for Emscripten on Windows when possible.

## Dependencies

Runtime (for building archives):
- Python 3.8+
- `zstandard` (Python module)
- `pyzstd` (for Emscripten Docker script)
- 7-Zip (Windows only, for extracting `.exe` installers)
- Docker (optional, for Emscripten Linux builds)

System tools (usually pre-installed):
- `tar` with xz support (Linux/macOS)
- `git` with LFS support
- `sha256sum` (or `shasum` on macOS)
