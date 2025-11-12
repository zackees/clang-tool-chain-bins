# Node.js Runtime Archives

This directory contains minimal Node.js runtime archives for Emscripten WebAssembly compilation support.

## Purpose

Node.js is required by Emscripten to run compiled WebAssembly programs. Instead of requiring users to install Node.js manually, `clang-tool-chain` bundles minimal Node.js binaries that are automatically downloaded on first use.

## Structure

```
nodejs/
├── manifest.json                  # Root manifest (all platforms)
├── README.md                      # This file
├── win/x86_64/
│   ├── manifest.json              # Windows x64 manifest
│   └── nodejs-*.tar.zst          # Windows archive (to be generated)
├── linux/x86_64/
│   ├── manifest.json              # Linux x64 manifest
│   └── nodejs-*.tar.zst          # Linux x64 archive (to be generated)
├── linux/arm64/
│   ├── manifest.json              # Linux ARM64 manifest
│   └── nodejs-*.tar.zst          # Linux ARM64 archive (to be generated)
├── darwin/x86_64/
│   ├── manifest.json              # macOS x64 manifest
│   └── nodejs-*.tar.zst          # macOS x64 archive (to be generated)
└── darwin/arm64/
    ├── manifest.json              # macOS ARM64 manifest
    └── nodejs-*.tar.zst          # macOS ARM64 archive (to be generated)
```

## Generating Archives

To generate Node.js runtime archives, use the maintainer script:

```bash
cd downloads-bins/tools
python fetch_and_archive_nodejs.py --platform win --arch x86_64
python fetch_and_archive_nodejs.py --platform linux --arch x86_64
python fetch_and_archive_nodejs.py --platform linux --arch arm64
python fetch_and_archive_nodejs.py --platform darwin --arch x86_64
python fetch_and_archive_nodejs.py --platform darwin --arch arm64
```

This will:
1. Download official Node.js binaries from nodejs.org
2. Verify checksums against official SHASUMS256.txt
3. Extract the archive
4. Strip unnecessary files (include/, share/, npm, corepack, docs)
5. Keep only minimal runtime (bin/node, lib/node_modules)
6. Create compressed .tar.zst archive (zstd level 22)
7. Generate checksums (SHA256, MD5)
8. Create manifest.json with GitHub URLs
9. Verify archive by extracting and testing node --version

## Archive Contents

Each Node.js archive contains:
- `bin/node` (or `bin/node.exe` on Windows) - Node.js runtime executable
- `LICENSE` - Node.js license file
- `lib/node_modules/` (minimal) - Core Node.js modules required for runtime

**Stripped (not included):**
- `include/` - C++ headers (not needed for Emscripten)
- `share/` - Documentation and man pages
- `npm` - Package manager (not needed for Emscripten)
- `corepack` - Package manager wrapper (not needed)
- `README.md`, `CHANGELOG.md` - Documentation files

## Size Information

| Platform | Official Size | Our Size | Reduction |
|----------|--------------|----------|-----------|
| Windows x64 | ~34 MB | ~12-15 MB | 56-65% |
| Linux x64 | ~29 MB | ~10-12 MB | 59-66% |
| Linux ARM64 | ~28 MB | ~10-12 MB | 57-67% |
| macOS x64 | ~49 MB | ~12-15 MB | 69-76% |
| macOS ARM64 | ~48 MB | ~10-12 MB | 75-79% |
| **Total** | **~188 MB** | **~54-66 MB** | **65-71%** |

## Version Information

- **Node.js Version:** v22.11.0 LTS "Jod" (November 2024 release)
- **Support Timeline:** Until April 2027 (LTS)
- **Official Download:** https://nodejs.org/dist/v22.11.0/

## Installation

These archives are automatically downloaded by `clang-tool-chain` when:
1. User executes `clang-tool-chain-emcc` or `clang-tool-chain-empp`
2. Bundled Node.js is not already installed
3. System Node.js is not available (or bundled is preferred)

Installation location: `~/.clang-tool-chain/nodejs/{platform}/{arch}/`

## Three-Tier Priority System

The wrapper uses a three-tier priority system for Node.js:

1. **Bundled Node.js (Priority 1):** Fast path <1ms, checks `~/.clang-tool-chain/nodejs/`
2. **System Node.js (Priority 2):** Fallback via `shutil.which("node")`
3. **Auto-download (Priority 3):** Triggers automatic download (~10-30 seconds, one-time)

## Manifest Format

The manifest follows the same structure as clang, mingw, and emscripten manifests:

**Root manifest (`manifest.json`):**
```json
{
  "platforms": [
    {
      "platform": "win",
      "architectures": [
        {
          "arch": "x86_64",
          "manifest_path": "win/x86_64/manifest.json"
        }
      ]
    },
    {
      "platform": "linux",
      "architectures": [
        {
          "arch": "x86_64",
          "manifest_path": "linux/x86_64/manifest.json"
        },
        {
          "arch": "arm64",
          "manifest_path": "linux/arm64/manifest.json"
        }
      ]
    },
    {
      "platform": "darwin",
      "architectures": [
        {
          "arch": "x86_64",
          "manifest_path": "darwin/x86_64/manifest.json"
        },
        {
          "arch": "arm64",
          "manifest_path": "darwin/arm64/manifest.json"
        }
      ]
    }
  ]
}
```

**Platform manifest (`{platform}/{arch}/manifest.json`):**
```json
{
  "latest": "22.11.0",
  "22.11.0": {
    "href": "https://raw.githubusercontent.com/zackees/clang-tool-chain-bins/main/assets/nodejs/{platform}/{arch}/nodejs-22.11.0-{platform}-{arch}.tar.zst",
    "sha256": "actual_sha256_checksum_here"
  }
}
```

## Integration with Emscripten

When a user runs Emscripten commands:
1. `wrapper.py:ensure_nodejs_available()` checks for Node.js
2. If bundled Node.js exists, use it (fast path)
3. If system Node.js exists, use it (fallback)
4. Otherwise, trigger `downloader.ensure_nodejs_available()` to download
5. Add node bin directory to PATH environment variable
6. Execute Emscripten with modified PATH

## Testing

Node.js bundling is tested in:
- `tests/test_nodejs_downloader.py` - 22 tests for manifest fetching, download, installation
- `tests/test_emscripten.py` - Integration tests for Emscripten with bundled Node.js
- `tests/test_wrapper.py` - Three-tier priority system tests

Run tests:
```bash
# Fast unit tests only
uv run pytest tests/test_nodejs_downloader.py -m "not slow" -v

# All tests including slow network-dependent tests
uv run pytest tests/test_nodejs_downloader.py -v

# Integration tests
uv run pytest tests/test_emscripten.py -v
```

## Maintenance

### Updating Node.js Version

When a new Node.js LTS version is released:
1. Update `NODEJS_VERSION` in `fetch_and_archive_nodejs.py`
2. Re-run archive generation for all platforms
3. Update manifests with new version and checksums
4. Test with Emscripten compilation
5. Commit and push to clang-tool-chain-bins repository
6. Update submodule reference in main repository

### Platform Support

Current platforms:
- Windows x86_64 (complete)
- Linux x86_64 (complete)
- Linux ARM64 (complete)
- macOS x86_64 (complete)
- macOS ARM64 (complete)

Future considerations:
- Windows ARM64 (when Node.js has stable ARM64 builds)

## References

- **Node.js Official Downloads:** https://nodejs.org/dist/
- **Node.js Release Schedule:** https://github.com/nodejs/release#release-schedule
- **LLVM-MinGW Pattern:** Similar to MinGW sysroot bundling for Windows GNU ABI
- **Emscripten Integration:** Required for WebAssembly compilation and execution

## Status

- [x] Manifest structure created
- [x] Root manifest.json created
- [x] Platform manifests created (5 platforms)
- [x] README.md documentation
- [x] Maintainer script implemented (fetch_and_archive_nodejs.py)
- [x] Downloader infrastructure implemented (downloader.py)
- [x] Wrapper integration implemented (wrapper.py)
- [x] Test suite implemented (test_nodejs_downloader.py)
- [ ] Archives generated for all platforms (Iteration 13)
- [ ] SHA256 checksums added to manifests (Iteration 13)
- [ ] Archives uploaded to GitHub (Iteration 16)
- [ ] End-to-end testing completed (Iteration 15)
