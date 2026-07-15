# clangd Forge fallback

`conanfile.py` pins both the LLVM release tag and the dereferenced
`llvm-project` commit. Forge runs this recipe on a native runner with
`LLVM_ENABLE_PROJECTS=clang;clang-tools-extra`, Release configuration, and
the native Conan-selected generator. The package exports the four compiled
tools, both script tools, runtime libraries, and matching Clang resource
headers.
