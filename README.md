# clang-tool-chain-bins

`clang-tool-chain-bins` is the package and CLI for querying and installing the archived LLVM, Clang, MSVC-adjacent, and related tool bundles managed in this repository.

It is designed for machine consumption first:

- per-archive JSON indexes for every `*.tar.zst`
- aggregate tool index for exact and glob lookup
- JSON Lines query output for scripting and AI agents
- install resolution with cache tracking and concurrency-safe locking

## Install

```bash
pip install clang-tool-chain-bins
```

## Query

Exact lookup:

```bash
clang-tool-chain-bins query llvm-pdbutil
```

Glob lookup:

```bash
clang-tool-chain-bins query "llvm-*"
```

Filter by platform, architecture, version, or component family:

```bash
clang-tool-chain-bins query "clang*" --platform linux --arch x86_64 --component clang
```

The CLI emits JSON Lines, one JSON object per input query. Each result object includes:

- `query`
- `matches`
- archive URL
- local cache path
- install path
- installed state
- component, version, platform, arch
- source archive provenance

## Install Tool Archives

Install the archive that contains a tool:

```bash
clang-tool-chain-bins install llvm-pdbutil --platform win --arch x86_64
```

If multiple archives match, narrow the selection with:

- `--platform`
- `--arch`
- `--version`
- `--component`

Use `--all` to install every matching archive.

## Index Generation

Generate or refresh sidecar indexes and the aggregate tool index:

```bash
clang-tool-chain-bins-index
```

## Publishing

Build and validate release artifacts locally:

```bash
clang-tool-chain-bins-publish --skip-upload
```

Publish to TestPyPI:

```bash
clang-tool-chain-bins-publish --testpypi
```

Publish to PyPI:

```bash
clang-tool-chain-bins-publish
```

By default the publish script reads the token from `PYPI_TOKEN`.

## Repository Scope

This repository still contains maintainer-oriented archive build scripts under `tools/`. The published package hardens the artifact boundary so wheel and sdist contents are limited to intentional code, metadata, tests, and generated tool index data rather than the raw archive payloads.
