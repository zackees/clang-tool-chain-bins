# Windows Archive Git LFS Migration

## Overview
Convert Windows LLVM archives from Git LFS to regular files (like Linux) to avoid LFS bandwidth costs.

## Current State
- Windows archives are under 99MB (53MB and 71MB)
- Currently tracked in Git LFS via .gitattributes
- No splitting needed since all archives < 99MB

## Migration Steps

### 1. Update .gitattributes (DONE)
Added exemption for Windows archives:
```
assets/clang/win/**/*.tar.zst !filter !diff !merge text=auto
```

### 2. Migrate existing LFS files to regular files

**Option A: Keep in submodule history (recommended)**
```bash
cd downloads-bins

# Untrack from LFS (keeps files locally)
git lfs untrack "assets/clang/win/**/*.tar.zst"

# Remove from Git cache (but keep file on disk)
git rm --cached assets/clang/win/x86_64/*.tar.zst

# Re-add as regular files (now using new .gitattributes rules)
git add assets/clang/win/x86_64/*.tar.zst

# Commit the migration
git commit -m "chore: migrate Windows archives from Git LFS to regular files (<99MB)"

# Push to remote
git push origin main
```

**Option B: Clean LFS history completely (requires force push)**
```bash
cd downloads-bins

# Use git filter-repo or BFG to remove from LFS history
# WARNING: This rewrites history and requires force push
# Only do this if you want to completely remove LFS bandwidth usage
```

### 3. Verify migration
```bash
cd downloads-bins

# Check that files are NOT in LFS
git lfs ls-files | grep "win/x86_64"
# Should return nothing

# Verify files are regular Git objects
git ls-files -s assets/clang/win/x86_64/*.tar.zst
# Should show regular file mode (100644), not LFS pointer

# Check file size in Git
git cat-file -s HEAD:assets/clang/win/x86_64/llvm-21.1.5-win-x86_64.tar.zst
# Should show actual size (~71MB), not pointer size (~100 bytes)
```

### 4. Update parent repository
```bash
cd ..  # Back to clang-tool-chain root

# Update submodule reference
git add downloads-bins
git commit -m "chore: update downloads-bins (Windows LFS migration)"
git push
```

## Notes

- **Windows archives stay under 99MB**: No splitting needed (unlike Linux which needs .part1/.part2)
- **macOS archives**: Consider migrating these too if under 99MB
- **Emscripten archives**: These are larger and may need to stay in LFS or be split
- **IWYU archives**: Check sizes and migrate if < 99MB

## File Sizes Reference

| Archive | Size | Split Needed? | LFS Status |
|---------|------|---------------|------------|
| llvm-19.1.7-win-x86_64.tar.zst | 53MB | No | Migrating to regular |
| llvm-21.1.5-win-x86_64.tar.zst | 71MB | No | Migrating to regular |
| llvm-21.1.5-linux-x86_64 | 112MB | Yes (98+14MB) | Already regular files |
| llvm-21.1.6-darwin-x86_64.tar.zst | ? | TBD | Still LFS |

## Warnings

⚠️ **Option B (clean history) requires:**
- Coordination with all contributors
- Force push to main branch
- Re-cloning for all developers
- Careful backup before proceeding

⚠️ **Bandwidth savings:**
- Option A: Saves future LFS bandwidth, but past LFS data remains
- Option B: Completely eliminates LFS bandwidth, but requires force push
