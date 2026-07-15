"""Forge/Conan fallback recipe for targets without an LLVM binary release."""

from pathlib import Path

from conan import ConanFile
from conan.tools.cmake import CMake, CMakeToolchain
from conan.tools.files import copy


LLVM_TAG = "llvmorg-21.1.5"
LLVM_COMMIT = "8e2cd28cd4ba46613a46467b0c91b1cabead26cd"
COMPILED_TOOLS = ("clangd", "clang-format", "clang-query", "clang-tidy")


def find_build_output(build_folder, filename):
    matches = sorted(Path(build_folder).rglob(filename))
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise RuntimeError(f"CMake did not produce {filename}")
    raise RuntimeError(f"CMake produced multiple {filename} outputs: {matches}")


def find_compiled_tool_outputs(build_folder):
    outputs = [find_build_output(build_folder, f"{tool}.exe") for tool in COMPILED_TOOLS]
    output_dirs = {output.parent for output in outputs}
    if len(output_dirs) != 1:
        raise RuntimeError(f"CMake produced compiled tools in multiple directories: {output_dirs}")
    return outputs


def find_clang_resource_include(build_folder, major):
    candidates = Path(build_folder).glob(f"**/lib/clang/{major}/include")
    for candidate in sorted(candidates):
        if (candidate / "stddef.h").is_file():
            return candidate
    raise RuntimeError(f"CMake did not produce Clang {major} resource headers")


class ClangdConan(ConanFile):
    name = "clangd"
    version = "21.1.5"
    settings = "os", "arch", "compiler", "build_type"
    requires = ()
    exports_sources = "CMakeLists.txt"

    def configure(self):
        self.settings.rm_safe("compiler.cppstd")

    def layout(self):
        self.folders.source = "source"
        self.folders.build = "build"

    def source(self):
        self.run("git clone --filter=blob:none https://github.com/llvm/llvm-project.git llvm-project")
        self.run(f"git -C llvm-project checkout {LLVM_COMMIT}")

    def generate(self):
        toolchain = CMakeToolchain(self)
        toolchain.variables["LLVM_ENABLE_PROJECTS"] = "clang;clang-tools-extra"
        toolchain.variables["LLVM_TARGETS_TO_BUILD"] = "AArch64"
        toolchain.variables["LLVM_ENABLE_ASSERTIONS"] = False
        toolchain.variables["CMAKE_CXX_STANDARD"] = 17
        toolchain.variables["CMAKE_BUILD_TYPE"] = "Release"
        toolchain.generate()

    def build(self):
        cmake = CMake(self)
        cmake.configure(build_script_folder="llvm-project/llvm")
        for target in COMPILED_TOOLS:
            cmake.build(target=target)

    def package(self):
        source_root = Path(self.source_folder) / "llvm-project"
        package_bin = Path(self.package_folder) / "bin"
        compiled_outputs = find_compiled_tool_outputs(self.build_folder)
        for executable in compiled_outputs:
            copy(
                self,
                executable.name,
                src=str(executable.parent),
                dst=str(package_bin),
                keep_path=False,
            )
            if not (package_bin / executable.name).is_file():
                raise RuntimeError(f"CMake did not package {executable.name}")
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
        tool_output_dir = compiled_outputs[0].parent
        for runtime in sorted(tool_output_dir.glob("*.dll")):
            copy(self, runtime.name, src=str(runtime.parent), dst=str(package_bin), keep_path=False)
        resource_include = find_clang_resource_include(self.build_folder, "21")
        packaged_include = Path(self.package_folder) / "lib" / "clang" / "21" / "include"
        copy(
            self,
            "*",
            src=str(resource_include),
            dst=str(packaged_include),
        )
        if not (packaged_include / "stddef.h").is_file():
            raise RuntimeError("Clang resource headers were not packaged")

    def package_info(self):
        self.cpp_info.bindirs = ["bin"]
