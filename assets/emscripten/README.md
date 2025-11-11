# Emscripten Archives for clang-tool-chain

## Status: IN PROGRESS (Iteration 3)

This directory will contain Emscripten SDK archives packaged for clang-tool-chain distribution.

## Structure

```
emscripten/
├── manifest.json           # Root manifest (all platforms)
├── win/
│   ├── x86_64/
│   │   ├── manifest.json   # Platform-specific manifest
│   │   └── emscripten-{version}-win-x86_64.tar.zst
│   └── arm64/              # (Future support)
├── linux/
│   ├── x86_64/
│   │   ├── manifest.json
│   │   └── emscripten-{version}-linux-x86_64.tar.zst
│   └── arm64/
│       ├── manifest.json
│       └── emscripten-{version}-linux-arm64.tar.zst
└── darwin/
    ├── x86_64/
    │   ├── manifest.json
    │   └── emscripten-{version}-darwin-x86_64.tar.zst
    └── arm64/
        ├── manifest.json
        └── emscripten-{version}-darwin-arm64.tar.zst
```

## Archive Creation Process

Archives are created using `tools/fetch_and_archive_emscripten.py`:

```bash
cd tools
python fetch_and_archive_emscripten.py --platform win --arch x86_64
python fetch_and_archive_emscripten.py --platform linux --arch x86_64
python fetch_and_archive_emscripten.py --platform linux --arch arm64
python fetch_and_archive_emscripten.py --platform darwin --arch x86_64
python fetch_and_archive_emscripten.py --platform darwin --arch arm64
```

## Requirements

- **pyzstd**: For zstd compression (`pip install pyzstd`)
- **git**: For cloning emsdk
- **Node.js**: Required by Emscripten (not bundled)
- **Disk space**: ~2GB per platform for build, ~100-200 MB per archive

## Archive Contents

Each archive contains:
- `emscripten/` - Emscripten Python scripts (emcc.py, em++.py, etc.)
- `bin/` - LLVM/Clang binaries with WebAssembly backend
- `lib/` - System libraries (libc, libc++, etc.)

Stripped from upstream:
- Documentation (*.md, docs/)
- Tests (tests/, test/)
- Examples (examples/)
- Git metadata (.git*)

## Next Steps (Iteration 4)

1. **Generate archives for all platforms** (may take 30-60 minutes per platform)
2. **Upload archives to this repository** (clang-tool-chain-bins)
3. **Update manifests with real URLs and checksums**
4. **Test automatic download and installation**
5. **Test WebAssembly compilation**: hello_world.cpp → .wasm

## Current Blockers

- **No archives generated yet**: Emscripten installation via emsdk takes significant time (~30 minutes per platform) and disk space (~1.2 GB installed)
- **Testing requires real archives**: Cannot test downloader/wrapper without archives in GitHub
- **Node.js dependency**: Users must install Node.js separately (not bundled in archives)

## Testing Without Full Build

For rapid development iteration, consider:
1. Create minimal test archive with stub files
2. Test manifest parsing and download logic
3. Generate full archives in CI/CD or dedicated build environment

## Related Files

- `src/clang_tool_chain/downloader.py` - Download infrastructure (lines 1333-1533)
- `src/clang_tool_chain/wrapper.py` - Wrapper infrastructure (lines 798-922)
- `pyproject.toml` - Entry points (lines 101-102)
- `tools/fetch_and_archive_emscripten.py` - Archive creation script
