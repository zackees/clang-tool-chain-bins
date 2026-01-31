#!/usr/bin/env python3
"""Join script for llvm-21.1.5-linux-x86_64.tar.zst"""
import sys
from pathlib import Path

parts = ['llvm-21.1.5-linux-x86_64.tar.zst.part1', 'llvm-21.1.5-linux-x86_64.tar.zst.part2']
output = "llvm-21.1.5-linux-x86_64.tar.zst"

print(f"Joining {len(parts)} parts into {output}...")

try:
    with open(output, 'wb') as out:
        for part in parts:
            print(f"  Adding {part}...")
            with open(part, 'rb') as inp:
                out.write(inp.read())

    size_mb = Path(output).stat().st_size / (1024 * 1024)
    print(f"\nDone! Created {output} ({size_mb:.2f} MB)")
    print("\nTo extract:")
    print(f"  tar --zstd -xf {output}")

except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
