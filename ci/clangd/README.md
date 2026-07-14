# clangd Forge fallback

`conanfile.py` pins both the LLVM release tag and the dereferenced
`llvm-project` commit. Forge runs this recipe on a native runner with
`LLVM_ENABLE_PROJECTS=clang;clang-tools-extra`, Release configuration, and
the `clangd` target. The package exports `bin/clangd` and the matching Clang
resource headers so the archive builder can merge them with the five existing
`clang-extra` tools.
