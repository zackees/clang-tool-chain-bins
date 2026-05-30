# Building IWYU with Static Linking for macOS

## Problem Statement

IWYU binaries on macOS (both x86_64 and ARM64) are crashing with:
```
dyld: Symbol not found: _LLVMInitializeAArch64AsmParser
  Referenced from: /Users/runner/.clang-tool-chain/iwyu/darwin/arm64/bin/include-what-you-use
  Expected in: <no uuid> unknown
```

**Root Cause:** IWYU was built with dynamic linking to LLVM libraries, but those libraries are not included in the distribution archive.

## Solution: Static Linking

Build IWYU with static LLVM libraries to create a self-contained binary with no external LLVM dependencies.

## Build Requirements

### System Requirements
- macOS 10.15+ (Catalina or later)
- Xcode Command Line Tools
- Homebrew (for installing LLVM)

### Dependencies
```bash
# Install Homebrew LLVM (provides both static and dynamic libraries)
brew install llvm

# Get LLVM path
LLVM_PATH=$(brew --prefix llvm)
echo $LLVM_PATH  # Usually /opt/homebrew/opt/llvm (ARM) or /usr/local/opt/llvm (x86_64)
```

## Build Process

### Step 1: Download IWYU Source

```bash
# IWYU 0.25 corresponds to LLVM 21
IWYU_VERSION="0.25"
curl -L https://github.com/include-what-you-use/include-what-you-use/archive/refs/tags/${IWYU_VERSION}.tar.gz -o iwyu-${IWYU_VERSION}.tar.gz
tar -xzf iwyu-${IWYU_VERSION}.tar.gz
cd include-what-you-use-${IWYU_VERSION}
```

### Step 2: Configure CMake with Static Linking

The key is to use `CMAKE_EXE_LINKER_FLAGS` to force static linking of LLVM components:

```bash
mkdir build && cd build

cmake -G "Unix Makefiles" \
  -DCMAKE_PREFIX_PATH=$(brew --prefix llvm) \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_EXE_LINKER_FLAGS="-static-libgcc -static-libstdc++" \
  -DLLVM_LINK_LLVM_DYLIB=OFF \
  ..
```

**Key CMake Variables:**
- `CMAKE_PREFIX_PATH`: Points to Homebrew LLVM installation
- `CMAKE_BUILD_TYPE=Release`: Optimized build without debug symbols
- `CMAKE_EXE_LINKER_FLAGS`: Forces static linking of standard libraries
- `LLVM_LINK_LLVM_DYLIB=OFF`: Tells CMake to link against individual LLVM component libraries instead of monolithic libLLVM.dylib

### Alternative: Force Static Libraries

If the above doesn't work, explicitly tell the linker to prefer static libraries:

```bash
cmake -G "Unix Makefiles" \
  -DCMAKE_PREFIX_PATH=$(brew --prefix llvm) \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_FIND_LIBRARY_SUFFIXES=".a" \
  -DBUILD_SHARED_LIBS=OFF \
  -DLLVM_LINK_LLVM_DYLIB=OFF \
  ..
```

**Additional Variables:**
- `CMAKE_FIND_LIBRARY_SUFFIXES=".a"`: Only search for static libraries (.a files)
- `BUILD_SHARED_LIBS=OFF`: Disable shared library building

### Step 3: Build IWYU

```bash
make -j$(sysctl -n hw.ncpu)
```

### Step 4: Verify Static Linking

Check that the binary doesn't depend on LLVM dylibs:

```bash
otool -L bin/include-what-you-use
```

**Expected output (good - only system libs):**
```
bin/include-what-you-use:
    /usr/lib/libc++.1.dylib (compatibility version 1.0.0, current version 1700.255.0)
    /usr/lib/libSystem.B.dylib (compatibility version 1.0.0, current version 1336.61.1)
```

**Bad output (has LLVM deps):**
```
bin/include-what-you-use:
    @rpath/libLLVM.dylib (compatibility version 1.0.0)
    @rpath/libclang-cpp.dylib (compatibility version 1.0.0)
    /usr/lib/libc++.1.dylib (...)
    /usr/lib/libSystem.B.dylib (...)
```

If you see `@rpath/libLLVM.dylib` or similar, the static linking failed.

### Step 5: Test the Binary

```bash
# Test version command
./bin/include-what-you-use --version

# Should output something like:
# include-what-you-use 0.25 based on clang version 21.1.6

# Test on a simple file
cat > test.cpp << 'EOF'
#include <iostream>
int main() {
    std::cout << "Hello\n";
    return 0;
}
EOF

./bin/include-what-you-use test.cpp
# Should analyze the file without crashing
```

### Step 6: Strip Debug Symbols (Optional)

Reduce binary size by stripping debug symbols:

```bash
strip -S bin/include-what-you-use

# Check size reduction
ls -lh bin/include-what-you-use
```

## Troubleshooting

### Problem: CMake finds dynamic libraries instead of static

**Solution:** Explicitly tell CMake to use static libraries first:

```bash
cmake -DCMAKE_FIND_LIBRARY_SUFFIXES=".a;.dylib" ...
```

This prioritizes `.a` files over `.dylib` files.

### Problem: Undefined symbols during linking

**Error:**
```
Undefined symbols for architecture arm64:
  "_LLVMInitializeAArch64AsmParser", referenced from:
      ...
```

**Solution:** Add the missing LLVM component library explicitly:

```bash
cmake -DCMAKE_EXE_LINKER_FLAGS="-lLLVMAArch64AsmParser" ...
```

Or ensure `LLVM_LINK_LLVM_DYLIB=OFF` is set so CMake links individual component libraries.

### Problem: Binary is too large (>200 MB)

This is expected with static linking. The binary includes all LLVM components.

**Solutions:**
1. Use `strip -S` to remove debug symbols (saves ~50-70%)
2. Enable LTO (Link-Time Optimization): `-DCMAKE_INTERPROCEDURAL_OPTIMIZATION=ON`
3. Accept the larger size - still better than broken dynamic linking

### Problem: Homebrew LLVM only has dynamic libraries

Check what Homebrew installed:

```bash
ls -la $(brew --prefix llvm)/lib/lib*.a
ls -la $(brew --prefix llvm)/lib/lib*.dylib
```

If no `.a` files exist, LLVM was built without static libraries. You'll need to:

1. Build LLVM from source with static libraries enabled, OR
2. Use the dynamic libraries and bundle them (not recommended, complex)

## Implementation in build_iwyu_macos.py

Key changes to make in `downloads-bins/tools/build_iwyu_macos.py`:

```python
def build_iwyu(source_dir: Path, llvm_path: Path, arch: str) -> Path:
    """Build IWYU with CMake and STATIC linking."""
    # ... existing code ...

    # CMake configuration with STATIC linking
    cmake_cmd = [
        "cmake",
        "-G", "Unix Makefiles",
        f"-DCMAKE_PREFIX_PATH={homebrew_llvm_path}",
        "-DCMAKE_BUILD_TYPE=Release",

        # NEW: Force static linking
        "-DCMAKE_FIND_LIBRARY_SUFFIXES=.a;.dylib",  # Prefer .a files
        "-DLLVM_LINK_LLVM_DYLIB=OFF",               # Link component libs, not monolithic
        "-DBUILD_SHARED_LIBS=OFF",                  # Don't build shared libs

        # Optional: Link-time optimization for smaller binary
        # "-DCMAKE_INTERPROCEDURAL_OPTIMIZATION=ON",

        ".."
    ]

    # ... rest of build ...

    # NEW: Strip debug symbols after build
    binary_path = build_dir / "bin" / "include-what-you-use"
    if binary_path.exists():
        print("\nStripping debug symbols...")
        subprocess.run(["strip", "-S", str(binary_path)], check=True)
        print(f"✓ Stripped {binary_path}")
```

## Testing the Fix

After building:

1. **Check dependencies:**
   ```bash
   otool -L bin/include-what-you-use | grep -i llvm
   # Should return nothing (no LLVM dependencies)
   ```

2. **Test locally:**
   ```bash
   ./bin/include-what-you-use --version
   ./bin/include-what-you-use test.cpp
   ```

3. **Test in isolated environment:**
   ```bash
   # Copy to /tmp (no LLVM dylibs in PATH)
   cp bin/include-what-you-use /tmp/
   cd /tmp
   ./include-what-you-use --version
   # Should work without any DYLD_LIBRARY_PATH settings
   ```

4. **Run CI tests:**
   After uploading the new archive, GitHub Actions will run the IWYU tests automatically.

## Expected Results

- **Binary size:** ~80-150 MB (vs ~5 MB dynamic, but that one doesn't work)
- **After zstd compression:** ~15-25 MB archive
- **Dependencies:** Only system libraries (libc++, libSystem)
- **Runtime:** No LLVM installation required
- **Portability:** Works on any macOS 10.15+ system

## References

- IWYU Build Docs: https://github.com/include-what-you-use/include-what-you-use#how-to-build-standalone
- CMake FindLLVM: https://llvm.org/docs/CMake.html
- macOS dyld man page: `man dyld`
- Homebrew LLVM: `brew info llvm`
