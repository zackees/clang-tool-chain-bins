# Migrate clang-tool-chain-bins from Python to Rust + Python Bindings

## Overview

Rewrite all toolchain packaging logic from Python to Rust, exposing CLIs as native Rust binaries and maintaining backward-compatible Python API via PyO3 abi3-py310 shims built with maturin (zig cross-compilation).

**Current state**: ~4,500 lines of Python across 15 scripts, 9 registered CLI entry points + 3 unregistered scripts. Single dependency: `zstandard`.

**Target state**: Rust workspace with maturin-built Python wheels. All CLIs are native Rust subcommands. Python package `clang-tool-chain-bins` continues to work via PyO3 shims + `[project.scripts]` entry points that delegate to the Rust binary. The zccache download crate is embedded directly as a Rust dependency, eliminating the current Rust→Python→Rust subprocess seam.

## Architecture

```
clang-tool-chain-bins/
├── Cargo.toml                        # Workspace root
├── pyproject.toml                    # maturin build-backend, abi3-py310
├── Dockerfile.linux-test             # Linux validation container
├── crates/
│   ├── ctcb-cli/                     # Binary: all subcommands via clap
│   ├── ctcb-core/                    # Platform detection, config, error types, formatting
│   ├── ctcb-archive/                 # tar create/extract, zstd compress/decompress, hard-links
│   ├── ctcb-checksum/                # SHA256, MD5 generation and verification
│   ├── ctcb-dedup/                   # MD5-based deduplication, hardlink structure creation
│   ├── ctcb-download/                # HTTP download (embeds zccache-download crate), progress bars
│   ├── ctcb-strip/                   # Binary stripping, essential-file filtering, platform rules
│   ├── ctcb-manifest/                # Two-tier manifest JSON read/write, split-archive metadata
│   └── ctcb-split/                   # Archive splitting for GitHub LFS 100MB limit
├── python/
│   └── clang_tool_chain_bins/        # Python package (shims)
│       ├── __init__.py               # Public API re-exports
│       ├── _native.pyd/.so           # PyO3 native extension (maturin-built)
│       └── cli.py                    # Entry points: find Rust binary, subprocess.run()
├── assets/                           # [unchanged] Git LFS binary distributions
├── tools/                            # [deprecated, removed in final phase]
└── .github/workflows/
    ├── ci.yml                        # Lint, test, build (all platforms)
    └── release.yml                   # Tag-triggered wheel + binary publishing
```

### CLI Mapping (Python → Rust subcommands)

| Current Python entry point | Rust subcommand | Source script |
|---|---|---|
| `fetch-and-archive` | `ctcb fetch` | `fetch_and_archive.py` (1376 lines) |
| `download-binaries` | `ctcb download` | `download_binaries.py` (463 lines) |
| `strip-binaries` | `ctcb strip` | `strip_binaries.py` (436 lines) |
| `deduplicate-binaries` | `ctcb dedup` | `deduplicate_binaries.py` (217 lines) |
| `create-hardlink-archive` | `ctcb hardlink-archive` | `create_hardlink_archive.py` (390 lines) |
| `expand-archive` | `ctcb expand` | `expand_archive.py` (260 lines) |
| `test-compression` | `ctcb bench-compression` | `test_compression.py` (259 lines) |
| `create-iwyu-archives` | `ctcb iwyu` | `create_iwyu_archives.py` (330 lines) |
| `extract-mingw-sysroot` | `ctcb mingw-sysroot` | `extract_mingw_sysroot.py` (349 lines) |
| *(unregistered)* | `ctcb emscripten` | `fetch_and_archive_emscripten.py` (427 lines) |
| *(unregistered)* | `ctcb emscripten-docker` | `fetch_and_archive_emscripten_docker.py` (279 lines) |
| *(unregistered)* | `ctcb nodejs` | `fetch_and_archive_nodejs.py` (730 lines) |

### Python shim entry points (pyproject.toml)

All existing CLI names continue to work. Each one delegates to the Rust binary:

```toml
[project.scripts]
fetch-and-archive = "clang_tool_chain_bins.cli:fetch_and_archive"
download-binaries = "clang_tool_chain_bins.cli:download_binaries"
# ... etc for all 9+3 entry points
```

Each shim function does: `sys.exit(subprocess.run([rust_binary, subcommand, *sys.argv[1:]]).returncode)`

---

## Agent Orchestration Protocol

This issue is designed for an **orchestrating agent** that dispatches phases sequentially to a **task sub-agent**.

### Rules for the orchestrating agent

1. **Dispatch one phase at a time** to a sub-agent. Do not run phases in parallel.
2. After the sub-agent completes, **locally validate** before moving to the next phase:
   - **Windows**: Run `uv run` commands and `cargo` commands natively.
   - **Linux**: Run inside Docker (`docker run` with the repo mounted or `Dockerfile.linux-test`).
3. **No remote CI** until Phase 11. All testing is local until then.
4. Keep the orchestrating agent's context clean — the sub-agent holds implementation details.
5. If a sub-agent fails validation, have it fix the issue before proceeding.

### Rules for task sub-agents

1. **Always use `uv run`** to execute Python. Never invoke `.venv/python` directly.
2. Inside Python scripts, use `sys.executable` for subprocess Python calls. Never use `sys.path.insert(0, ...)`.
3. Run `cargo build` and `cargo test` for Rust validation.
4. Run `uv run pytest` (not `python -m pytest`) for Python validation.
5. Each phase must end with all tests passing locally before reporting completion.

---

## Phase 1: Project Scaffolding

**Goal**: Establish the Rust workspace, maturin build system, and local validation infrastructure. No logic yet — just skeleton.

### Sub-tasks

1.1. Create `Cargo.toml` workspace root with all crate members (empty `lib.rs`/`main.rs` stubs):
   - `crates/ctcb-cli` (binary crate, `clap` dependency)
   - `crates/ctcb-core` (lib)
   - `crates/ctcb-archive` (lib)
   - `crates/ctcb-checksum` (lib)
   - `crates/ctcb-dedup` (lib)
   - `crates/ctcb-download` (lib)
   - `crates/ctcb-strip` (lib)
   - `crates/ctcb-manifest` (lib)
   - `crates/ctcb-split` (lib)

1.2. Set up workspace-level dependencies in `[workspace.dependencies]`:
   - `clap` (derive), `serde` + `serde_json`, `sha2`, `md-5`, `zstd`, `tar`, `reqwest` (rustls), `pyo3` (abi3-py310, feature-gated), `anyhow`, `indicatif` (progress bars), `walkdir`, `tokio` (for async downloads)

1.3. Restructure `pyproject.toml`:
   - Change build-backend to `maturin`
   - Set `[tool.maturin]` with `--zig` cross-compilation config
   - Configure PyO3 abi3-py310
   - Keep package name `clang-tool-chain-bins`
   - Keep all existing `[project.scripts]` entry points pointing to new `python/clang_tool_chain_bins/cli.py` shims
   - Add `requires-python = ">=3.10"` (abi3-py310)

1.4. Create `python/clang_tool_chain_bins/__init__.py` and `python/clang_tool_chain_bins/cli.py` with stub shim functions for all 12 CLIs.

1.5. Create `Dockerfile.linux-test`:
   - Based on `rust:latest`
   - Install `maturin`, `uv`, `zig`
   - Mount repo, run `cargo build --workspace` and `cargo test --workspace`
   - Run `uv run pytest`

1.6. Add `.cargo/config.toml` for zig linker configuration if needed.

1.7. Verify: `cargo build --workspace` succeeds (empty stubs). `cargo test --workspace` passes (no tests yet). `uv sync` and `uv run python -c "import clang_tool_chain_bins"` works.

### Validation
- **Windows**: `cargo build --workspace` ✓, `uv sync && uv run python -c "import clang_tool_chain_bins"` ✓
- **Linux Docker**: `docker build -f Dockerfile.linux-test .` ✓

---

## Phase 2: Core Library Crates

**Goal**: Implement shared infrastructure that all subsequent phases depend on.

### Sub-tasks

2.1. **`ctcb-core`**: Implement:
   - `Platform` enum: `Win`, `Linux`, `Darwin`
   - `Arch` enum: `X86_64`, `Arm64`
   - `Target` struct combining platform + arch
   - `detect_current_target()` — runtime platform/arch detection
   - Formatting helpers: `format_size(bytes) -> String`, `format_duration(Duration) -> String`
   - `print_section(title)` — formatted section headers (matching Python's 70-char separators)
   - Common error types via `thiserror`

2.2. **`ctcb-checksum`**: Implement:
   - `sha256_file(path) -> String` — hex digest
   - `md5_file(path) -> String` — hex digest
   - `sha256_verify(path, expected_hex) -> Result<bool>`
   - `generate_checksum_files(archive_path)` — writes `.sha256` and `.md5` sidecar files
   - Unit tests: hash a known byte sequence, verify round-trip

2.3. **`ctcb-archive`**: Implement:
   - `create_tar(source_dir, output_path, preserve_hardlinks: bool)` — TAR with native hard-link metadata
   - `extract_tar(tar_path, output_dir)` — extract TAR preserving hard links
   - `compress_zstd(input_path, output_path, level: i32)` — zstd compression (level 1-22)
   - `decompress_zstd(input_path, output_path)` — zstd decompression
   - `compress_tar_zst(source_dir, output_path, level: i32)` — combined tar+zstd
   - `extract_tar_zst(archive_path, output_dir)` — combined decompress+extract
   - Unit tests: round-trip a temp directory through tar+zstd, verify file contents and permissions

### Validation
- **Windows**: `cargo test -p ctcb-core -p ctcb-checksum -p ctcb-archive`
- **Linux Docker**: Same tests, plus verify executable bit preservation on tar round-trip

---

## Phase 3: Port `expand-archive` (Proof of Concept)

**Goal**: First fully working Rust CLI subcommand. Validates the end-to-end pattern: Rust binary ← clap subcommand ← Python shim.

### Sub-tasks

3.1. Implement `ctcb expand` subcommand in `ctcb-cli/src/main.rs`:
   ```
   ctcb expand <archive> <output_dir> [--verify <sha256>] [--keep-hardlinks]
   ```
   - Uses `ctcb-archive::extract_tar_zst()`
   - Uses `ctcb-checksum::sha256_verify()` if `--verify` provided
   - Progress reporting via `indicatif`

3.2. Wire up Python shim: `expand-archive` entry point calls `ctcb expand`.

3.3. Integration test: use an existing `.tar.zst` from `assets/` — expand with both Python (old) and Rust (new), diff the output directories.

### Validation
- **Windows**: `cargo run -- expand assets/clang/win/x86_64/llvm-*.tar.zst ./test-out` produces correct output
- **Linux Docker**: Same test with a Linux archive
- **Shim**: `uv run expand-archive assets/clang/win/x86_64/llvm-*.tar.zst ./test-out` works identically

---

## Phase 4: Port `deduplicate-binaries`

**Goal**: MD5-based deduplication engine.

### Sub-tasks

4.1. **`ctcb-dedup`**: Implement:
   - `analyze_directory(dir) -> DeduplicationReport` — group files by MD5 hash, report savings
   - `create_deduped_structure(source_dir, dest_dir) -> DeduplicationManifest` — copy unique files, emit `dedup_manifest.json`
   - `expand_deduped_structure(deduped_dir, manifest, output_dir)` — restore from manifest
   - `DeduplicationManifest` struct: serde-serializable, compatible with current `dedup_manifest.json` format

4.2. Implement `ctcb dedup` subcommand with sub-subcommands:
   ```
   ctcb dedup analyze <directory>
   ctcb dedup create <source_dir> <dest_dir>
   ctcb dedup expand <deduped_dir> <output_dir>
   ```

4.3. Wire up Python shim.

4.4. Test: run Python `deduplicate-binaries analyze` and `ctcb dedup analyze` on the same extracted LLVM directory, compare reports.

### Validation
- **Windows**: Round-trip dedup→expand on extracted LLVM binaries, verify file-level identity
- **Linux Docker**: Same test

---

## Phase 5: Port `strip-binaries`

**Goal**: Platform-aware binary stripping and essential-file filtering.

### Sub-tasks

5.1. **`ctcb-strip`**: Implement:
   - `ESSENTIAL_BINARIES` set (matching Python's list at `fetch_and_archive.py:72-97`)
   - `strip_extras(source_dir, output_dir, target: Target, keep_headers: bool)` — remove non-essential files
   - `strip_debug_symbols(dir, target: Target)` — run `strip` on Linux binaries (shell out to system `strip`)
   - `should_exclude_lib_file(path) -> bool` — filter Fortran runtime, hwasan, etc.
   - Platform-specific rules: which directories/extensions to remove per platform

5.2. Implement `ctcb strip` subcommand:
   ```
   ctcb strip <source_dir> <output_dir> --platform <win|linux|darwin> --arch <x86_64|arm64> [--keep-headers] [--no-strip] [-v]
   ```

5.3. Wire up Python shim.

### Validation
- **Windows**: Strip an extracted LLVM Win directory, compare output file list with Python version
- **Linux Docker**: Strip a Linux LLVM directory, verify debug symbols removed

---

## Phase 6: Port `create-hardlink-archive`

**Goal**: Hard-link deduplication + TAR + zstd in one step.

### Sub-tasks

6.1. Extend `ctcb-dedup` or `ctcb-archive` with:
   - `create_hardlink_structure(deduped_dir, manifest) -> PathBuf` — uses `std::fs::hard_link()`
   - `verify_hardlinks(dir) -> HardlinkReport` — group by inode, report savings

6.2. Implement `ctcb hardlink-archive` subcommand:
   ```
   ctcb hardlink-archive <deduped_dir> <output_dir> [--name <archive_name>] [--zstd-level <1-22>]
   ```
   - Reads `dedup_manifest.json` from `deduped_dir`
   - Creates hard-linked directory → TAR → zstd

6.3. Wire up Python shim.

### Validation
- **Windows**: Create hardlink archive from deduped LLVM, expand it, verify hard links exist (same inode)
- **Linux Docker**: Same test

---

## Phase 7: Port `download-binaries`

**Goal**: HTTP download with progress, multipart support, checksum verification. Embeds zccache-download crate.

### Sub-tasks

7.1. **`ctcb-download`**: Implement:
   - Add `zccache-download` crate(s) as Cargo dependencies (from crates.io or git)
   - `download_file(url, output_path, show_progress: bool) -> Result<()>` — single-file HTTP download with progress bar
   - `download_multipart(part_urls, output_path, expected_sha256) -> Result<()>` — multipart download via zccache download engine
   - `verify_download(path, expected_sha256) -> Result<bool>`
   - Platform-specific URL resolution: map `(platform, arch, version)` → download URL

7.2. Implement `ctcb download` subcommand:
   ```
   ctcb download --version <ver> [--output <dir>] [--platform <p>...] [--current-only] [--no-verify]
   ```

7.3. Wire up Python shim.

7.4. If zccache-download is not published as a standalone crate, implement download logic directly using `reqwest` with streaming, retry, and progress. The key capability needed: parallel chunk downloads for large files, SHA256 verification.

### Validation
- **Windows**: Download a known small LLVM release, verify checksum matches
- **Linux Docker**: Same test

---

## Phase 8: Port `test-compression` + `ctcb-split`

**Goal**: Compression benchmarking tool and archive splitting for LFS.

### Sub-tasks

8.1. **`ctcb-split`**: Implement:
   - `split_archive(archive_path, max_part_size_mb: u64, output_dir) -> Vec<PartInfo>`
   - `PartInfo` struct: path, sha256, size
   - Update manifest with part metadata

8.2. Implement `ctcb bench-compression` subcommand:
   ```
   ctcb bench-compression <directory> [output_prefix]
   ```
   - Test gzip (1,6,9), bzip2 (1,6,9), xz (0,6,9), zstd (1-22)
   - Report: method, level, compressed size, ratio, time
   - Use Rust crates: `flate2`, `bzip2`, `xz2`, `zstd`

8.3. Implement `ctcb split` subcommand:
   ```
   ctcb split <archive> [--part-size-mb <95>] [--output-dir <dir>]
   ```

8.4. Wire up Python shims for both.

### Validation
- **Windows**: Run bench-compression on a small test directory, verify output table
- **Linux Docker**: Same test, plus split a >100MB file and verify reassembly

---

## Phase 9: Port Manifest System

**Goal**: Two-tier manifest JSON management.

### Sub-tasks

9.1. **`ctcb-manifest`**: Implement:
   - `RootManifest` struct: platforms → architectures → manifest_path
   - `PlatformManifest` struct: latest version, version map (href, sha256, optional parts)
   - `read_root_manifest(path) -> RootManifest`
   - `read_platform_manifest(path) -> PlatformManifest`
   - `update_platform_manifest(path, version, href, sha256, parts)` — add/update version entry
   - `update_root_manifest(path, platform, arch, manifest_path)` — ensure platform/arch entry exists
   - URL generation: always use `https://media.githubusercontent.com/media/zackees/clang-tool-chain-bins/refs/heads/main/assets/...` format (GitHub LFS media URLs, NOT blob URLs)
   - Serde round-trip tests: read existing manifests from `assets/`, write back, verify no diff

### Validation
- **Windows**: Read all existing manifests in `assets/`, round-trip through Rust structs, verify JSON equality
- **Linux Docker**: Same test

---

## Phase 10: Port `fetch-and-archive` (Master Pipeline)

**Goal**: The main orchestration pipeline. This is the largest single migration — it wires together all previous crates.

### Sub-tasks

10.1. Implement `ctcb fetch` subcommand:
   ```
   ctcb fetch --platform <win|linux|darwin> --arch <x86_64|arm64> [--version <ver>] [--source-dir <dir>] [--work-dir <dir>] [--output-dir <dir>] [--zstd-level <1-22>]
   ```

10.2. Pipeline stages (matching Python's 10-step flow):
   1. Download LLVM release (`ctcb-download`) — or use `--source-dir`
   2. Extract (7z on Windows via `Command::new("7z")`, tar on Unix) (`ctcb-archive`)
   3. Strip non-essential files (`ctcb-strip`)
   4. Strip debug symbols on Linux (`ctcb-strip`)
   5. Deduplicate by MD5 (`ctcb-dedup`)
   6. Create hard-link structure (`ctcb-dedup`)
   7. Create TAR with hard-link metadata (`ctcb-archive`)
   8. Compress with zstd (`ctcb-archive`)
   9. Generate checksums (`ctcb-checksum`)
   10. Update manifest (`ctcb-manifest`)
   11. Split if >99MB (`ctcb-split`)
   12. Place in `assets/{tool}/{platform}/{arch}/`

10.3. Wire up Python shim.

10.4. Configuration constants: `LLVM_VERSION`, `ESSENTIAL_BINARIES`, `LLVM_DOWNLOAD_URLS` — store in `ctcb-core` or `ctcb-cli` config module.

### Validation
- **Windows**: Run full pipeline with `--source-dir` pointing to pre-extracted LLVM. Compare output archive SHA256 with Python-generated archive (functional equivalence, not byte-identical).
- **Linux Docker**: Same test with Linux LLVM binaries.

---

## Phase 11: Port Remaining Toolchain Scripts

**Goal**: Port IWYU, MinGW sysroot, Emscripten, and Node.js packaging.

### Sub-tasks

11.1. Implement `ctcb iwyu` subcommand:
   ```
   ctcb iwyu [--iwyu-root <path>] [--version <ver>] [--zstd-level <1-22>] [--platform <p>] [--arch <a>]
   ```
   - Port `create_iwyu_archives.py` logic
   - Uses `ctcb-archive`, `ctcb-checksum`, `ctcb-manifest`

11.2. Implement `ctcb mingw-sysroot` subcommand:
   ```
   ctcb mingw-sysroot --arch <x86_64|arm64> [--work-dir <dir>] [--output-dir <dir>] [--skip-download] [--llvm-mingw-version <ver>]
   ```
   - Port `extract_mingw_sysroot.py` logic
   - Downloads LLVM-MinGW release, extracts sysroot

11.3. Implement `ctcb emscripten` subcommand:
   ```
   ctcb emscripten --platform <p> --arch <a> [--work-dir <dir>] [--output-dir <dir>]
   ```
   - Port `fetch_and_archive_emscripten.py` logic
   - Shells out to `git clone` for emsdk, then `emsdk install/activate`

11.4. Implement `ctcb emscripten-docker` subcommand:
   ```
   ctcb emscripten-docker --platform <p> --arch <a> [--work-dir <dir>] [--output-dir <dir>]
   ```
   - Port `fetch_and_archive_emscripten_docker.py` logic
   - Shells out to `docker create`, `docker cp`

11.5. Implement `ctcb nodejs` subcommand:
   ```
   ctcb nodejs --platform <p> --arch <a> [--work-dir <dir>] [--output-dir <dir>] [--version <ver>]
   ```
   - Port `fetch_and_archive_nodejs.py` logic
   - Download from nodejs.org, verify checksum, strip, compress

11.6. Wire up all Python shims.

### Validation
- **Windows**: Run each subcommand in dry-run or with small inputs
- **Linux Docker**: Run `ctcb emscripten-docker` end-to-end (requires Docker-in-Docker or mounted socket)

---

## Phase 12: PyO3 Bindings + Python API Layer

**Goal**: Expose Rust functions to Python via PyO3 abi3-py310 for library-level access (not just CLI shims).

### Sub-tasks

12.1. Add `#[cfg(feature = "python")]` gated PyO3 modules to key crates:
   - `ctcb-archive`: `expand_archive()`, `compress_tar_zst()`
   - `ctcb-checksum`: `sha256_file()`, `md5_file()`
   - `ctcb-manifest`: `read_platform_manifest()`, `update_platform_manifest()`
   - `ctcb-download`: `download_file()`, `download_multipart()`

12.2. Create root PyO3 module in `ctcb-cli/src/python.rs`:
   ```rust
   #[pymodule]
   fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
       m.add_function(wrap_pyfunction!(expand_archive, m)?)?;
       m.add_function(wrap_pyfunction!(sha256_file, m)?)?;
       // ... etc
   }
   ```

12.3. Update `python/clang_tool_chain_bins/__init__.py` to expose library API:
   ```python
   from clang_tool_chain_bins._native import expand_archive, sha256_file, ...
   ```

12.4. Ensure `maturin develop` builds the native extension and `uv run python -c "from clang_tool_chain_bins._native import expand_archive"` works.

12.5. Write Python integration tests in `python/tests/test_public_api.py`:
   - Test each exposed function
   - Verify backward compatibility with any existing Python API consumers

### Validation
- **Windows**: `maturin develop` + `uv run pytest python/tests/`
- **Linux Docker**: `maturin develop --zig` + `uv run pytest python/tests/`

---

## Phase 13: CI/CD Pipeline

**Goal**: GitHub Actions workflows for building, testing, and releasing cross-platform wheels.

### Sub-tasks

13.1. Create `.github/workflows/ci.yml`:
   - Matrix: linux-x86_64, linux-arm64, macos-x86_64, macos-arm64, windows-x86_64, windows-arm64
   - Steps: `cargo fmt --check`, `cargo clippy`, `cargo test --workspace`
   - Maturin build with `--zig` for cross-compilation
   - `uv run pytest` for Python tests
   - Cache: `Swatinem/rust-cache@v2`

13.2. Create `.github/workflows/release.yml`:
   - Trigger on `v*` tags
   - Build wheels for all 6 targets via `maturin build --release --zig`
   - Build standalone binaries for all 6 targets
   - Upload wheels to PyPI (or test PyPI initially)
   - Create GitHub Release with standalone binaries + SHA256 checksums

13.3. Ensure `pip install clang-tool-chain-bins` installs the wheel with native extension and all CLI shims work.

### Validation
- **Windows**: `maturin build` locally, install wheel, test CLIs
- **Linux Docker**: `maturin build --zig --target x86_64-unknown-linux-gnu`, install wheel, test CLIs
- **Remote**: Push to a test branch, verify CI green on all platforms ← **first remote test**

---

## Phase 14: Cleanup + Final Validation

**Goal**: Remove deprecated Python code, update docs, full remote CI validation.

### Sub-tasks

14.1. Remove `tools/` directory (all Python scripts).

14.2. Update `CLAUDE.md`:
   - New project structure
   - New CLI commands (`ctcb <subcommand>`)
   - New build instructions (`cargo build`, `maturin develop`)
   - Updated dependency list (Rust crates instead of Python packages)

14.3. Update `index.html` if any URLs or version references changed.

14.4. Update `.gitattributes` if needed.

14.5. Full integration test:
   - Run `ctcb fetch --platform win --arch x86_64 --source-dir <pre-extracted>` on Windows
   - Run `ctcb fetch --platform linux --arch x86_64 --source-dir <pre-extracted>` in Linux Docker
   - Verify output archives are functionally equivalent to the existing Python-generated ones
   - Verify manifests are valid JSON with correct LFS media URLs
   - Verify `pip install .` and all 12 CLI entry points work

14.6. Remote CI: push to branch, open PR, verify all CI checks pass on all 6 targets.

### Validation
- **Full matrix**: All 6 platform/arch combos build and test green
- **Backward compatibility**: `pip install clang-tool-chain-bins` → all CLI entry points work → parent `clang-tool-chain` project can use the Python API

---

## Key Technical Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Build system | maturin + zig | Cross-compile all targets, PyO3 abi3 wheels |
| Python ABI | abi3-py310 | Forward-compatible: one wheel works for Python 3.10+ |
| CLI framework | clap (derive) | Industry standard, excellent subcommand support |
| Async runtime | tokio | Required by reqwest for HTTP downloads |
| Error handling | anyhow + thiserror | anyhow in CLI, thiserror in libraries |
| Serialization | serde + serde_json | Manifest compatibility |
| Progress bars | indicatif | Rich terminal progress (matches Python's output) |
| Download engine | zccache-download crate (embedded) | Eliminates Rust→Python→Rust seam |
| HTTP client | reqwest (rustls) | No OpenSSL dependency, cross-platform TLS |
| Compression | zstd crate | Native bindings, levels 1-22 |
| Hashing | sha2 + md-5 crates | Pure Rust, no system dependency |
| File traversal | walkdir | Recursive directory iteration |
| 7-Zip (Windows) | Shell out to `7z` | Existing external dependency, not worth reimplementing |

## Execution Rules

- **Python execution**: Always `uv run`. Never `.venv/python`. Inside scripts: `sys.executable`.
- **Testing**: Local only until Phase 13. Windows native + Linux Docker.
- **Commits**: Conventional commits: `feat(phase-N): description`, `fix(phase-N): description`.
- **Branches**: One feature branch per phase. Merge to main after validation.
- **No scope creep**: Each phase implements exactly what's listed. No "while I'm here" improvements.
- **Manifest URLs**: Always `https://media.githubusercontent.com/media/...` (GitHub LFS media), never blob URLs.
