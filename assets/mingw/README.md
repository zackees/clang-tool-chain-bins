# MinGW Sysroot Archives

This directory contains MinGW-w64 sysroot archives for Windows GNU ABI support.

## Structure

```
mingw/
├── manifest.json              # Root manifest (all platforms)
└── win/
    └── x86_64/
        ├── manifest.json      # Platform-specific manifest
        └── mingw-sysroot-*.tar.zst  # Sysroot archive (to be generated)
```

## Generating Archives

To generate MinGW sysroot archives, use the extraction tool:

```bash
python src/clang_tool_chain/downloads/extract_mingw_sysroot.py --arch x86_64
```

This will:
1. Download LLVM-MinGW release from GitHub
2. Extract only the sysroot (x86_64-w64-mingw32/)
3. Create compressed .tar.zst archive
4. Generate checksums (SHA256, MD5)
5. Update manifest.json with actual SHA256

## Archive Contents

Each MinGW sysroot archive contains:
- `x86_64-w64-mingw32/include/` - C/C++ standard library headers
- `x86_64-w64-mingw32/lib/` - Import libraries (.a files)
- `generic-w64-mingw32/` - Generic MinGW headers (optional)

## Version Information

- **LLVM-MinGW Release:** 20241124 (November 24, 2024)
- **LLVM Version:** 19.1.7
- **Target:** x86_64-w64-mingw32 (Windows GNU ABI)

## Installation

These archives are automatically downloaded by `clang-tool-chain` when:
1. User is on Windows platform
2. Code is compiled without explicit `--target` override
3. Windows defaults to GNU ABI (v2.0.0+)

Installation location: `~/.clang-tool-chain/mingw/win/x86_64/`

## Manifest Format

The manifest follows the same structure as clang and iwyu manifests:

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
    }
  ]
}
```

**Platform manifest (`win/x86_64/manifest.json`):**
```json
{
  "latest": "20241124",
  "versions": {
    "20241124": {
      "version": "20241124",
      "href": "https://github.com/zackees/clang-tool-chain/releases/download/mingw-sysroot-v1/mingw-sysroot-20241124-win-x86_64.tar.zst",
      "sha256": "actual_sha256_checksum_here"
    }
  }
}
```

## TODO

- [ ] Generate actual sysroot archive using extract_mingw_sysroot.py
- [ ] Upload archive to GitHub releases
- [ ] Update manifest.json with actual SHA256 checksum
- [ ] Test download and extraction with clang-tool-chain
- [ ] Add ARM64 support (aarch64-w64-mingw32)
