# Building macOS Emscripten Archives

## Problem
The macOS Emscripten archives (both x86_64 and ARM64) have incorrect directory structure with `upstream/` prefix.
This was caused by a bug in `tools/fetch_and_archive_emscripten.py` that has now been fixed.

## Limitation
**Emscripten archives for macOS MUST be built on an actual macOS system.**

You cannot build macOS binaries from Windows or Linux. The emsdk installation process downloads
platform-specific binaries, so building darwin/arm64 requires macOS ARM64 (M1/M2/M3 Mac).

## Prerequisites (macOS only)

```bash
# Install Python 3.11+
brew install python3

# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
cd downloads-bins
uv sync
```

## Building macOS ARM64 Archive

```bash
cd downloads-bins

# Clean any previous work
rm -rf work_emscripten

# Build the archive (takes 10-30 minutes)
uv run python tools/fetch_and_archive_emscripten.py --platform darwin --arch arm64

# Verify the archive structure
python3 -c "
import tarfile
import pyzstd
from pathlib import Path
import io

archive_path = Path('assets/emscripten/darwin/arm64/emscripten-*-darwin-arm64.tar.zst')
archives = list(Path('assets/emscripten/darwin/arm64/').glob('emscripten-*-darwin-arm64.tar.zst'))
if archives:
    archive_path = archives[0]
    print(f'Checking: {archive_path}')
    with open(archive_path, 'rb') as f:
        compressed_data = f.read()
        decompressed = pyzstd.decompress(compressed_data)

    with tarfile.open(fileobj=io.BytesIO(decompressed), mode='r') as tar:
        members = tar.getmembers()
        print(f'Total files: {len(members)}')
        print('First 20 entries:')
        for member in members[:20]:
            print(f'  {member.name}')

        # Check for upstream/ prefix (should NOT exist)
        has_upstream = any('upstream/' in m.name for m in members[:50])
        if has_upstream:
            print('❌ ERROR: Archive still contains upstream/ prefix!')
        else:
            print('✅ SUCCESS: Archive has correct structure (no upstream/ prefix)')
else:
    print('No archive found')
"
```

## Building macOS x86_64 Archive (Intel Macs)

```bash
cd downloads-bins

# Clean any previous work
rm -rf work_emscripten

# Build the archive (takes 10-30 minutes)
uv run python tools/fetch_and_archive_emscripten.py --platform darwin --arch x86_64
```

## Alternative: GitHub Actions Workflow

If you don't have access to a macOS machine, you can create a GitHub Actions workflow:

```yaml
name: Build macOS Emscripten Archive

on:
  workflow_dispatch:
    inputs:
      arch:
        description: 'Architecture (x86_64 or arm64)'
        required: true
        default: 'arm64'
        type: choice
        options:
          - arm64
          - x86_64

jobs:
  build:
    runs-on: ${{ inputs.arch == 'arm64' && 'macos-14' || 'macos-13' }}  # macos-14 = ARM64, macos-13 = x86_64

    steps:
      - name: Checkout clang-tool-chain-bins
        uses: actions/checkout@v4
        with:
          repository: zackees/clang-tool-chain-bins
          lfs: true

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install uv
        run: curl -LsSf https://astral.sh/uv/install.sh | sh

      - name: Install dependencies
        run: |
          cd downloads-bins
          uv sync

      - name: Build Emscripten archive
        run: |
          cd downloads-bins
          uv run python tools/fetch_and_archive_emscripten.py --platform darwin --arch ${{ inputs.arch }}

      - name: Verify archive structure
        run: |
          cd downloads-bins
          python3 -c "
          import tarfile, pyzstd, io
          from pathlib import Path
          archives = list(Path('assets/emscripten/darwin/${{ inputs.arch }}/').glob('emscripten-*-darwin-${{ inputs.arch }}.tar.zst'))
          if archives:
              with open(archives[0], 'rb') as f:
                  decompressed = pyzstd.decompress(f.read())
              with tarfile.open(fileobj=io.BytesIO(decompressed), mode='r') as tar:
                  members = tar.getmembers()
                  has_upstream = any('upstream/' in m.name for m in members[:50])
                  if has_upstream:
                      print('ERROR: Archive has upstream/ prefix')
                      exit(1)
                  print('SUCCESS: Archive structure is correct')
          "

      - name: Upload artifact
        uses: actions/upload-artifact@v4
        with:
          name: emscripten-darwin-${{ inputs.arch }}
          path: downloads-bins/assets/emscripten/darwin/${{ inputs.arch }}/emscripten-*.tar.zst*
```

## After Building

1. Verify the archive structure (see verification script above)
2. Commit and push the new archive to Git LFS:
   ```bash
   git add assets/emscripten/darwin/arm64/
   git commit -m "fix(emscripten): rebuild macOS ARM64 archive with correct directory structure"
   git lfs push origin main
   git push origin main
   ```

3. Test the installation:
   ```bash
   cd ../clang-tool-chain
   clang-tool-chain purge --yes
   uv run clang-tool-chain-emcc --version
   ```

## What Was Fixed

The bug was in `tools/fetch_and_archive_emscripten.py` lines 420-429:

**Before (BROKEN)**:
```python
# Create upstream/ directory in staging to match expected structure
staging_upstream = staging_dir / "upstream"
staging_upstream.mkdir(parents=True, exist_ok=True)

# Copy essential directories into upstream/
for src_name in ["emscripten", "bin", "lib"]:
    src = upstream_dir / src_name
    if src.exists():
        dst = staging_upstream / src_name  # ← Files go INTO upstream/!
        shutil.copytree(src, dst, symlinks=True)
```

**After (FIXED)**:
```python
# Copy essential directories directly to staging root (NOT into upstream/)
# This ensures archive has correct structure: bin/, emscripten/, lib/
for src_name in ["emscripten", "bin", "lib"]:
    src = upstream_dir / src_name
    if src.exists():
        dst = staging_dir / src_name  # ← Copy directly to staging root
        shutil.copytree(src, dst, symlinks=True)
```

This change ensures the archive structure matches Windows/Linux (no `upstream/` prefix).
