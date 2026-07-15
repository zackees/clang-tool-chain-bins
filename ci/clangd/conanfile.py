"""Forge/Conan fallback recipe for targets without an LLVM binary release."""

from pathlib import Path

from conan import ConanFile
from conan.tools.cmake import CMake, CMakeToolchain
from conan.tools.files import copy


LLVM_TAG = "llvmorg-21.1.5"
LLVM_COMMIT = "8e2cd28cd4ba46613a46467b0c91b1cabead26cd"
COMPILED_TOOLS = ("clangd", "clang-format", "clang-query", "clang-tidy")


class ClangdConan(ConanFile):
    name = "clangd"
    version = "21.1.5"
    settings = "os", "arch", "compiler", "build_type"
    requires = ()
    exports_sources = "CMakeLists.txt"

    def layout(self):
        self.folders.source = "source"
        self.folders.build = "build"

    def source(self):
        self.run("git clone --filter=blob:none https://github.com/llvm/llvm-project.git llvm-project")
        self.run(f"git -C llvm-project checkout {LLVM_COMMIT}")

    def generate(self):
        toolchain = CMakeToolchain(self, generator="Ninja")
        toolchain.variables["LLVM_ENABLE_PROJECTS"] = "clang;clang-tools-extra"
        toolchain.variables["LLVM_TARGETS_TO_BUILD"] = "AArch64"
        toolchain.variables["LLVM_ENABLE_ASSERTIONS"] = False
        toolchain.variables["CMAKE_BUILD_TYPE"] = "Release"
        toolchain.variables["CMAKE_INSTALL_PREFIX"] = self.package_folder
        toolchain.generate()

    def build(self):
        cmake = CMake(self)
        cmake.configure(build_script_folder="llvm-project/llvm")
        for target in COMPILED_TOOLS:
            cmake.build(target=target)

    def package(self):
        source_root = Path(self.source_folder) / "llvm-project"
        build_bin = Path(self.build_folder) / "bin"
        package_bin = Path(self.package_folder) / "bin"
        for tool in COMPILED_TOOLS:
            copy(self, f"{tool}.exe", src=str(build_bin), dst=str(package_bin))
        copy(
            self,
            "git-clang-format",
            src=str(source_root / "clang" / "tools" / "clang-format"),
            dst=str(package_bin),
        )
        copy(
            self,
            "run-clang-tidy.py",
            src=str(source_root / "clang-tools-extra" / "clang-tidy" / "tool"),
            dst=str(package_bin),
        )
        (package_bin / "run-clang-tidy.py").rename(package_bin / "run-clang-tidy")
        copy(self, "*.dll", src=str(build_bin), dst=str(package_bin))
        copy(
            self,
            "*",
            src=str(Path(self.build_folder) / "lib" / "clang" / "21" / "include"),
            dst=str(Path(self.package_folder) / "lib" / "clang" / "21" / "include"),
        )

    def package_info(self):
        self.cpp_info.bindirs = ["bin"]
