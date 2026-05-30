# Cleanup Old LLVM 19.1.7 Versions

## Issue
Old LLVM 19.1.7 archives are still present in the repository, wasting Git LFS bandwidth and storage. These are not referenced in any manifests (latest is 21.1.5/21.1.6).

## Files to Remove

### Windows x86_64
- `assets/clang/win/x86_64/llvm-19.1.7-win-x86_64.tar.zst` (53MB)
- `assets/clang/win/x86_64/llvm-19.1.7-win-x86_64.tar.zst.md5`
- `assets/clang/win/x86_64/llvm-19.1.7-win-x86_64.tar.zst.sha256`

### macOS x86_64
- `assets/clang/darwin/x86_64/llvm-19.1.7-darwin-x86_64.tar.zst`
- `assets/clang/darwin/x86_64/llvm-19.1.7-darwin-x86_64.tar.zst.md5`
- `assets/clang/darwin/x86_64/llvm-19.1.7-darwin-x86_64.tar.zst.sha256`

### macOS ARM64
- `assets/clang/darwin/arm64/llvm-19.1.7-darwin-arm64.tar.zst`
- `assets/clang/darwin/arm64/llvm-19.1.7-darwin-arm64.tar.zst.md5`
- `assets/clang/darwin/arm64/llvm-19.1.7-darwin-arm64.tar.zst.sha256`

## Current Latest Versions

| Platform | Latest Version | Manifest |
|----------|----------------|----------|
| Windows x86_64 | 21.1.5 | ✅ Confirmed |
| Linux x86_64 | 21.1.5 | ✅ Confirmed |
| Linux ARM64 | 21.1.5 | ✅ Confirmed |
| macOS x86_64 | 21.1.6 | ✅ Confirmed |
| macOS ARM64 | 21.1.6 | ✅ Confirmed |

## Cleanup Commands

```bash
cd downloads-bins

# Remove Windows 19.1.7
git rm assets/clang/win/x86_64/llvm-19.1.7-win-x86_64.tar.zst*

# Remove macOS x86_64 19.1.7
git rm assets/clang/darwin/x86_64/llvm-19.1.7-darwin-x86_64.tar.zst*

# Remove macOS ARM64 19.1.7
git rm assets/clang/darwin/arm64/llvm-19.1.7-darwin-arm64.tar.zst*

# Commit the cleanup
git commit -m "chore: remove old LLVM 19.1.7 archives (not referenced in manifests)"

# Push changes
git push origin main

# Optional: Clean up LFS storage
git lfs prune
```

## Estimated Savings

- **Git LFS bandwidth**: ~150-200MB (3 large archives)
- **Repository size**: Archives will be removed from LFS storage
- **Download impact**: None (manifests already point to 21.x versions)

## Verification

After cleanup, verify no references to 19.1.7 remain:

```bash
# Check manifests
grep -r "19.1.7" assets/clang/*/manifest.json
# Should return nothing

# Check for remaining files
find assets/clang -name "*19.1.7*"
# Should return nothing

# Check LFS tracking
git lfs ls-files | grep "19.1.7"
# Should return nothing
```

## Safety

This is a safe operation because:
1. ✅ Manifests point to 21.x versions only
2. ✅ No users are downloading 19.1.7 (not in manifest)
3. ✅ Old versions can always be rebuilt if needed (build scripts remain)
4. ✅ Git history preserves old files (can be recovered if needed)
