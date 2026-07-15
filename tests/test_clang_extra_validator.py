from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from tools.validate_clang_extra import PE_MACHINE_ARM64, PE_MACHINE_X86_64, read_pe_machine, validate_pe_machine


def _write_pe(path: Path, machine: int) -> None:
    payload = bytearray(256)
    payload[:2] = b"MZ"
    struct.pack_into("<I", payload, 0x3C, 0x80)
    payload[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", payload, 0x84, machine)
    path.write_bytes(payload)


class ClangExtraValidatorTests(unittest.TestCase):
    def test_pe_machine_parser_accepts_native_arm64(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "clangd.exe"
            _write_pe(executable, PE_MACHINE_ARM64)
            self.assertEqual(read_pe_machine(executable), PE_MACHINE_ARM64)
            validate_pe_machine(executable, "arm64")

    def test_pe_machine_validator_rejects_x86_64_emulation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "clangd.exe"
            _write_pe(executable, PE_MACHINE_X86_64)
            with self.assertRaisesRegex(AssertionError, "0xaa64"):
                validate_pe_machine(executable, "arm64")


if __name__ == "__main__":
    unittest.main()
