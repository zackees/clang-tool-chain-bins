## Goal

Move the tool inventory and query/install surface into `downloads-bins/` so it can be developed, tested, hardened, and published independently of the main `clang-tool-chain` repo.

Target package / CLI direction:

- publish from this repo, not the parent repo
- harden first, then publish
- publish under the intended package name `clang-tool-chain-bins`
- keep unit tests local to this repo so archive/index behavior is validated where the assets and maintainer scripts already live

## Checklist

### 1. Harden the package layout

- [x] Review the current `pyproject.toml` and package boundary.
- [x] Harden packaging so only intentional code and metadata ship in wheel/sdist.
- [x] Exclude large work directories and transient build state from published artifacts.
- [x] Replace broad packaging of `tools/` with a clearer, publishable module boundary if needed.
- [x] Ensure the package can be built reproducibly in isolation.
- [x] Add or improve `README.md` so the package has a usable PyPI landing page.
- [x] Verify console entry points are stable and intentional.

### 2. Tool inventory metadata

- [x] Add a per-archive JSON index for every `*.tar.zst`.
- [x] Include archive filename/path, family, version, platform, arch.
- [x] Include archive file listing.
- [x] Include tool/executable entries.
- [x] Include archive SHA-256 and binary/tool SHA-256 values.
- [x] Keep schema machine-oriented and deterministic.
- [x] Leave room for a future hash-tree / Merkle-tree extension.

### 3. Validation tests

- [x] Add unit tests that validate archive/index integrity under `assets/`.
- [x] Validate every `*.tar.zst` has a matching `*.json` index.
- [x] Validate every archive has a matching `.sha256`.
- [x] Validate archive SHA-256 matches the side file.
- [x] Validate JSON indexes are schema-valid.
- [x] Validate JSON metadata matches archive identity.

### 4. Aggregate index

- [x] Add an aggregate index generator that scans all archives and sidecar JSON files.
- [x] Support exact lookup like `llvm-pdbutil`.
- [x] Support glob/prefix lookup like `llvm-*`.
- [x] Support filtering by platform / arch / version / family.
- [x] Preserve provenance back to the source archive.
- [x] Keep generated output deterministic for CI and regression testing.

### 5. CLI: `clang-tool-chain-bins query`

- [x] Add a query command in this repo.
- [x] Support `clang-tool-chain-bins query "clang*"`.
- [x] Support exact tool lookup like `llvm-pdbutil` from aggregate tool metadata.
- [x] Support glob tool lookup like `llvm-*` from aggregate tool metadata.
- [x] Emit JSON Lines output.
- [x] Return one JSON object per input query.
- [x] Include a `matches` array in each JSON object.
- [x] Report archive/component identity.
- [x] Report URL.
- [x] Report local file cache path.
- [x] Report installed state.
- [x] Report platform / arch / version when known.
- [x] Use the aggregate index instead of manifest walking at query time.

### 5a. Unit tests for query

- [x] Add repo-local unit tests for `clang-tool-chain-bins query`.
- [x] Validate that querying `clang*` returns JSONL output.
- [x] Validate that the output reports URL.
- [x] Validate that the output reports local cache path.
- [x] Validate that the output reports installed state.
- [x] Validate multiple input patterns return multiple JSON lines, one line per query.

### 6. CLI: `clang-tool-chain-bins install`

- [x] Add install resolution using the same aggregate index.
- [x] Support fully-qualified install selection when needed.
- [x] Reuse existing concurrent download/extract flows where possible.
- [x] Keep install behavior concurrency-safe.

### 7. Publish to PyPI

- [ ] Use the existing PyPI token for release once the package is hardened.
- [x] Add a local publish script in this repo.
- [x] Create isolated build environment in the publish flow.
- [x] Install `build` and `twine` in the publish flow.
- [x] Build wheel + sdist.
- [x] Run `twine check`.
- [ ] Optionally publish to TestPyPI.
- [ ] Publish to PyPI.
- [x] Align `pyproject.toml`, docs, and CLI naming with `clang-tool-chain-bins`.

## Notes

- This work should live in `downloads-bins/`, not in the parent repo.
- Prioritize correctness and machine validation over presentation.
- The end state should let a machine or AI answer: "does this repo contain tool X, for which platform/arch/version, and how do I install it?"
