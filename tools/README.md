# LLVM/Clang Toolchain Build Tools

This directory contains maintainer scripts for creating and managing LLVM/Clang toolchain binary archives.

## Main Scripts

### `fetch_and_archive.py`
Complete pipeline for downloading, stripping, deduplicating, and compressing LLVM toolchains.

**Usage:**
```bash
# Download and package LLVM for Windows x86_64
python fetch_and_archive.py --platform win --arch x86_64

# Use existing extracted binaries (skip download)
python fetch_and_archive.py --platform win --arch x86_64 --source-dir ./extracted

# Package for Linux ARM64
python fetch_and_archive.py --platform linux --arch arm64
```

**What it does:**
1. Downloads LLVM from GitHub (or uses `--source-dir`)
2. Extracts archive
3. Strips unnecessary files (docs, examples, static libs)
4. Deduplicates identical binaries (~571 MB savings)
5. Creates hard-linked structure
6. Compresses with zstd level 22 (94.3% reduction)
7. Generates checksums (SHA256, MD5)
8. Names archive: `llvm-{version}-{platform}-{arch}.tar.zst`
9. Places in `../assets/clang/{platform}/{arch}/`

**Requirements:**
```bash
pip install zstandard
```

### `extract_mingw_sysroot.py`
Extracts MinGW-w64 sysroot for Windows GNU ABI support.

**Usage:**
```bash
python extract_mingw_sysroot.py --arch x86_64 --work-dir work --output-dir ../assets/mingw/win
```

## Individual Component Scripts

### `download_binaries.py`
Download LLVM releases from GitHub.

### `strip_binaries.py`
Remove unnecessary files to optimize archive size.

### `deduplicate_binaries.py`
Identify duplicate binaries by MD5 hash.

### `create_hardlink_archive.py`
Create hard-linked TAR archives to preserve deduplication.

### `expand_archive.py`
Extract `.tar.zst` archives.

### `test_compression.py`
Compare compression methods and levels.

### `create_iwyu_archives.py`
Create include-what-you-use toolchain archives.

## Output Structure

Archives are placed in:
```
../assets/
├── clang/
│   ├── win/x86_64/
│   │   ├── llvm-{version}-win-x86_64.tar.zst
│   │   ├── llvm-{version}-win-x86_64.tar.zst.sha256
│   │   └── manifest.json
│   ├── linux/x86_64/
│   └── darwin/arm64/
└── mingw/
    └── win/x86_64/
        ├── mingw-sysroot-{version}-win-x86_64.tar.zst
        └── manifest.json
```

## Development Workflow

1. **Update LLVM version** in `fetch_and_archive.py` (line 41: `LLVM_VERSION`)

2. **Generate archives for all platforms:**
   ```bash
   # Windows x86_64
   python fetch_and_archive.py --platform win --arch x86_64

   # Linux x86_64
   python fetch_and_archive.py --platform linux --arch x86_64

   # Linux ARM64
   python fetch_and_archive.py --platform linux --arch arm64

   # macOS x86_64
   python fetch_and_archive.py --platform darwin --arch x86_64

   # macOS ARM64
   python fetch_and_archive.py --platform darwin --arch arm64
   ```

3. **Extract MinGW sysroot (Windows GNU ABI):**
   ```bash
   python extract_mingw_sysroot.py --arch x86_64 --work-dir work --output-dir ../assets/mingw/win
   ```

4. **Update manifests** in `../assets/clang/{platform}/{arch}/manifest.json` with new version info

5. **Commit to this repository:**
   ```bash
   git add ../assets/
   git commit -m "Add LLVM version X.Y.Z"
   git push origin main
   ```

6. **Update main repository** to reference new submodule commit:
   ```bash
   cd ../..  # Back to main repo
   git add downloads-bins
   git commit -m "Update binaries to LLVM X.Y.Z"
   git push origin main
   ```

## Archive Size Optimization

The pipeline achieves ~94% size reduction through:

1. **Stripping** (~27 MB saved)
   - Remove Fortran runtime libraries
   - Remove documentation and examples
   - Remove static libraries

2. **Deduplication** (~571 MB saved)
   - Identify identical binaries
   - Create hard-linked TAR archive

3. **ZSTD Level 22** (902 MB → 52 MB)
   - Ultra-compressed archives
   - ~17:1 compression ratio

**Result:** 51.53 MB archive (from 902 MB original) for Windows x86_64

## Notes

- End users do NOT need these scripts - binaries are downloaded automatically
- These scripts are only for maintainers who update binary distributions
- Archives use `.tar.zst` format for maximum compression with fast decompression
- Hard links in TAR preserve deduplication benefits
