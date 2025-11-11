# Docker-Based Emscripten Packaging

## Quick Start

```bash
# Extract and package Emscripten from Docker (Linux x86_64)
cd downloads-bins/tools
python3 fetch_and_archive_emscripten_docker.py --platform linux --arch x86_64
```

This will:
1. Pull the official `emscripten/emsdk:latest` Docker image
2. Extract Emscripten files from the container
3. Package as `.tar.zst` archive with zstd level 22 compression
4. Generate checksums (SHA256, MD5)
5. Create manifest.json
6. Place archive in `../assets/emscripten/linux/x86_64/`

## Why Docker?

**Problem:** Emscripten's emsdk requires a functional Python environment with full standard library. This causes issues on:
- **Windows/MSYS2:** Missing `_ctypes` module in MSYS2 Python
- **Cross-platform builds:** Can't easily generate Linux archives from Windows
- **Reproducibility:** Different emsdk versions on different systems

**Solution:** Use official Docker images that have Emscripten pre-installed and tested.

## Requirements

- Docker installed and running
- Python 3.9+ with `pyzstd` module
- ~5 GB free disk space (Docker image + extracted files)

## Installation

```bash
# Install Docker
# - Windows: https://docs.docker.com/desktop/install/windows-install/
# - macOS: https://docs.docker.com/desktop/install/mac-install/
# - Linux: https://docs.docker.com/engine/install/

# Install pyzstd
pip install pyzstd
```

## Usage Examples

### Extract Only (No Compression)

```bash
# Just extract files without creating final archive
python3 fetch_and_archive_emscripten_docker.py \
  --platform linux --arch x86_64 \
  --no-package \
  --work-dir ./work_emscripten
```

This is useful for:
- Inspecting extracted files
- Manual stripping/optimization
- Testing before full packaging

### Specify Docker Image Version

```bash
# Use specific Emscripten version
python3 fetch_and_archive_emscripten_docker.py \
  --platform linux --arch x86_64 \
  --image emscripten/emsdk:3.1.50
```

### Complete Workflow

```bash
# 1. Extract from Docker
python3 fetch_and_archive_emscripten_docker.py \
  --platform linux --arch x86_64 \
  --work-dir ./work

# This creates:
# - work/extracted/upstream/ (raw Docker extraction)
# - work/archive_structure/ (cleaned for packaging)
# - ../assets/emscripten/linux/x86_64/emscripten-{version}-linux-x86_64.tar.zst
# - ../assets/emscripten/linux/x86_64/manifest.json
```

## Docker Image Details

**Official Image:** `emscripten/emsdk:latest`
- **Source:** https://github.com/emscripten-core/emsdk
- **Docker Hub:** https://hub.docker.com/r/emscripten/emsdk
- **Size:** ~2-4 GB (includes LLVM, Binaryen, Node.js)
- **Platform:** Linux (amd64 and arm64)

**Image Contents:**
```
/emsdk/
├── upstream/
│   ├── emscripten/    # Python scripts (emcc, em++, etc.)
│   ├── bin/           # LLVM/Clang with WASM backend
│   ├── lib/           # System libraries (libc++, libc)
│   └── share/         # Additional resources
├── node/              # Bundled Node.js
└── python/            # Bundled Python (Windows only)
```

**What We Extract:**
- `upstream/emscripten/` - Core Emscripten tools
- `upstream/bin/` - LLVM/Clang binaries
- `upstream/lib/` - System libraries for WebAssembly
- `upstream/share/` - CMake configs, docs, examples (stripped later)

**What We Don't Extract:**
- Node.js (users install separately)
- Python (host system provides)
- emsdk management tools (not needed in distribution)

## Archive Size Expectations

| Component | Compressed | Installed |
|-----------|------------|-----------|
| LLVM/Clang (WASM) | ~150-200 MB | ~500-700 MB |
| Binaryen tools | ~10-20 MB | ~50-100 MB |
| System libraries | ~30-50 MB | ~200-300 MB |
| Emscripten scripts | ~5-10 MB | ~50-100 MB |
| **Total (estimated)** | **~200-300 MB** | **~800-1200 MB** |

Compare to direct emsdk install: ~1.2-1.5 GB installed

## Troubleshooting

### Docker Not Found
```
❌ Docker not found or not running
```

**Solution:** Install Docker Desktop and ensure it's running.

### Docker Pull Fails
```
❌ Failed to pull Docker image: unauthorized
```

**Solution:** Check internet connection. Docker Hub has rate limits; retry after a few minutes.

### Permission Denied (Linux)
```
❌ permission denied while trying to connect to Docker daemon socket
```

**Solution:** Add user to docker group:
```bash
sudo usermod -aG docker $USER
newgrp docker
```

### Insufficient Disk Space
```
❌ no space left on device
```

**Solution:** Free up disk space. Docker images and extracted files need ~5 GB.

## Platform Limitations

**Important:** Docker extraction always produces **Linux** binaries, regardless of host OS.

### Cross-Platform Packaging

To package for Windows or macOS:

1. **Windows:** Use Windows Docker image (if available) or package MinGW-compatible binaries
2. **macOS:** Use macOS-specific emsdk installation (can't use Docker on Linux host)

**Recommended Approach:**
- Use Docker for **Linux x86_64** and **Linux arm64** archives
- Use native emsdk for **Windows** and **macOS** archives

## Next Steps After Packaging

1. **Upload to bins repository:**
   ```bash
   cd ../assets/emscripten/linux/x86_64
   git add emscripten-*.tar.zst manifest.json
   git commit -m "Add Emscripten {version} for Linux x86_64"
   git push
   ```

2. **Update root manifest:**
   ```bash
   cd ../assets/emscripten
   # Edit manifest.json to include new platform/version
   git add manifest.json
   git commit -m "Update Emscripten root manifest"
   ```

3. **Test installation:**
   ```bash
   cd ~/clang-tool-chain
   rm -rf ~/.clang-tool-chain/emscripten  # Clean slate
   clang-tool-chain-emcc --version        # Triggers download
   ```

4. **Test compilation:**
   ```bash
   echo 'int main() { return 0; }' > test.c
   clang-tool-chain-emcc test.c -o test.html
   node test.js  # Should run without errors
   ```

## Comparison: Docker vs Direct emsdk

| Aspect | Docker Approach | Direct emsdk |
|--------|----------------|--------------|
| **Setup Time** | 10-15 min | 30-45 min |
| **Host Requirements** | Docker only | Python, Git |
| **Platform Support** | Linux only | All platforms |
| **Reproducibility** | ✅ Excellent | ⚠️ Variable |
| **Cross-platform** | ❌ No | ✅ Yes |
| **Size** | Same | Same |
| **Maintenance** | Easy | Complex |

## See Also

- `fetch_and_archive_emscripten.py` - Original packaging script (requires native emsdk)
- `../assets/emscripten/manifest.json` - Root manifest structure
- `../../CLAUDE.md` - Main project documentation
