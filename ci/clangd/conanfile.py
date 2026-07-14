"""Forge/Conan fallback recipe for targets without an LLVM binary release."""

from conan import ConanFile
from conan.tools.cmake import CMake, CMakeToolchain
from conan.tools.files import copy


LLVM_TAG = "llvmorg-21.1.6"
LLVM_COMMIT = "a832a5222e489298337fbb5876f8dcaf072c5cca"


class ClangdConan(ConanFile):
    name = "clangd"
    version = "21.1.6"
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
        toolchain = CMakeToolchain(self)
        toolchain.variables["LLVM_ENABLE_PROJECTS"] = "clang;clang-tools-extra"
        toolchain.variables["LLVM_TARGETS_TO_BUILD"] = "Native"
        toolchain.variables["LLVM_ENABLE_ASSERTIONS"] = False
        toolchain.variables["CMAKE_BUILD_TYPE"] = "Release"
        toolchain.variables["CMAKE_INSTALL_PREFIX"] = self.package_folder
        toolchain.generate()

    def build(self):
        cmake = CMake(self)
        cmake.configure(build_script_folder="llvm-project/llvm")
        cmake.build(target="clangd")

    def package(self):
        copy(self, "clangd", src=self.build_folder / "bin", dst=self.package_folder / "bin")
        copy(
            self,
            "*",
            src=self.build_folder / "lib" / "clang" / "21" / "include",
            dst=self.package_folder / "lib" / "clang" / "21" / "include",
        )

    def package_info(self):
        self.cpp_info.bindirs = ["bin"]
