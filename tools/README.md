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

### `fetch_and_archive_nodejs.py`
Create minimal Node.js binary archives for bundled Emscripten runtime support.

**Usage:**
```bash
# Package Node.js for Windows x86_64
python fetch_and_archive_nodejs.py --platform win --arch x86_64

# Package for Linux ARM64
python fetch_and_archive_nodejs.py --platform linux --arch arm64

# Package for all platforms
python fetch_and_archive_nodejs.py --platform win --arch x86_64
python fetch_and_archive_nodejs.py --platform linux --arch x86_64
python fetch_and_archive_nodejs.py --platform linux --arch arm64
python fetch_and_archive_nodejs.py --platform darwin --arch x86_64
python fetch_and_archive_nodejs.py --platform darwin --arch arm64
```

**What it does:**
1. Downloads official Node.js binaries from nodejs.org
2. Extracts the archive (ZIP, TAR.XZ, TAR.GZ)
3. Verifies checksum against official SHASUMS256.txt
4. Strips unnecessary files:
   - ❌ `include/` (headers, ~5-10 MB)
   - ❌ `share/` (docs, man pages, ~2-5 MB)
   - ❌ README, CHANGELOG (docs)
5. Keeps minimal runtime:
   - ✅ `bin/node[.exe]` (binary)
   - ✅ `lib/node_modules` (core libraries including npm, corepack)
   - ✅ `LICENSE`
6. Creates hard-linked TAR archive (preserves deduplication)
7. Compresses with zstd level 22 (~27% reduction vs official)
8. Generates checksums (SHA256, MD5)
9. Creates manifest.json with version, href, sha256
10. Places in `../assets/nodejs/{platform}/{arch}/`

**Size Reduction:**
| Platform | Official Size | Our Size | Reduction |
|----------|--------------|----------|-----------|
| Windows x64 | 34 MB | ~23-24 MB | ~27-29% |
| Linux x64 | 29 MB | ~23-24 MB | ~17-21% |
| Linux ARM64 | 28 MB | ~23-24 MB | ~14-18% |
| macOS x64 | 49 MB | ~24-25 MB | ~49-51% |
| macOS ARM64 | 48 MB | ~23-24 MB | ~50-52% |
| **Total** | **188 MB** | **~117 MB** | **~38%** |

**Node.js Version:**
- Current: Node.js 22.11.0 LTS "Jod"
- Support: Until 2027-04-30
- Update policy: LTS releases only (stability priority)

**Requirements:**
```bash
pip install zstandard requests
```

**Output:**
```
../assets/nodejs/
├── manifest.json
├── README.md
├── win/x86_64/
│   ├── manifest.json
│   ├── nodejs-22.11.0-win-x86_64.tar.zst (~23-24 MB)
│   ├── nodejs-22.11.0-win-x86_64.tar.zst.sha256
│   └── nodejs-22.11.0-win-x86_64.tar.zst.md5
├── linux/x86_64/
│   ├── manifest.json
│   ├── nodejs-22.11.0-linux-x86_64.tar.zst (~23-24 MB)
│   └── ...
└── darwin/arm64/
    └── ...
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

### `integrate_lldb_linux_archives.py`
**NEW in Iteration 15** - Automate integration of Linux LLDB archives built by GitHub Actions.

**Usage:**
```bash
# Auto-download from latest workflow run and integrate
python tools/integrate_lldb_linux_archives.py

# Download from specific run ID
python tools/integrate_lldb_linux_archives.py --run-id 12345678

# Use pre-downloaded artifacts
python tools/integrate_lldb_linux_archives.py --skip-download --artifacts-dir ./my-artifacts

# Dry-run (test without making changes)
python tools/integrate_lldb_linux_archives.py --dry-run

# Integrate only one architecture
python tools/integrate_lldb_linux_archives.py --arch x86_64
```

**What it does:**
1. Checks GitHub CLI is installed and authenticated
2. Finds latest workflow run (or uses specified run ID)
3. Downloads artifacts from GitHub Actions
4. Verifies SHA256 checksums
5. Tests archive extraction
6. Moves archives to distribution directories (`assets/lldb/linux/x86_64/` and `assets/lldb/linux/arm64/`)
7. Updates manifest files with metadata (sha256, size, python_bundled)
8. Validates manifest structure

**Requirements:**
```bash
pip install zstandard
gh auth login  # GitHub CLI authentication
```

**See also:** `.agent_task/ARCHIVE_INTEGRATION_CHECKLIST.md` for comprehensive manual integration instructions.

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
├── mingw/
│   └── win/x86_64/
│       ├── mingw-sysroot-{version}-win-x86_64.tar.zst
│       └── manifest.json
├── nodejs/
│   ├── manifest.json
│   ├── win/x86_64/
│   │   ├── nodejs-{version}-win-x86_64.tar.zst
│   │   └── manifest.json
│   ├── linux/x86_64/
│   ├── linux/arm64/
│   ├── darwin/x86_64/
│   └── darwin/arm64/
└── emscripten/
    └── linux/x86_64/
        └── ...
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

4. **Generate Node.js archives (for Emscripten runtime):**
   ```bash
   python fetch_and_archive_nodejs.py --platform win --arch x86_64
   python fetch_and_archive_nodejs.py --platform linux --arch x86_64
   python fetch_and_archive_nodejs.py --platform linux --arch arm64
   python fetch_and_archive_nodejs.py --platform darwin --arch x86_64
   python fetch_and_archive_nodejs.py --platform darwin --arch arm64
   ```

5. **Update manifests** in `../assets/clang/{platform}/{arch}/manifest.json` with new version info

6. **Commit to this repository:**
   ```bash
   git add ../assets/
   git commit -m "Add LLVM version X.Y.Z"
   git push origin main
   ```

7. **Update main repository** to reference new submodule commit:
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
