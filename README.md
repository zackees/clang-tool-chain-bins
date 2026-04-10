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

The CLI uses `~/.clang-tool-chain` as its default cache and install root. Set `CLANG_TOOL_CHAIN_DOWNLOAD_PATH` or pass `--home-dir` to use a different location.

## Query CLI

`query` is the discovery command. It returns one result object per input pattern.

- Patterns without glob characters are exact matches.
- Patterns containing `*`, `?`, or `[]` use glob matching.
- Matching is case-insensitive against both normalized `tool_name` and the archived `file_name`.
- Filters narrow matches by `platform`, `arch`, `version`, and `component`.
- Output is JSON Lines by default. Use `--pretty` for a human-readable table.

Exact lookup:

```bash
clang-tool-chain-bins query clang
```

Glob lookup:

```bash
clang-tool-chain-bins query "llvm-*"
```

Multiple patterns in one call:

```bash
clang-tool-chain-bins query clang clang++ --platform linux --arch x86_64
```

Pretty output:

```bash
clang-tool-chain-bins query clang++ --pretty
```

Typical JSONL shape:

```json
{"query":"clang","matches":[{"tool_name":"clang","file_name":"clang","component":"clang","version":"21.1.5","platform":"linux","arch":"x86_64","archive_url":"https://...","archive_sha256":"...","local_cache_path":"...","install_path":"...","installed":false}]}
```

Each match includes the aggregate index metadata plus derived local state such as:

- `query`
- `matches`
- `tool_name` and `file_name`
- `component`, `version`, `platform`, `arch`
- `archive_filename`, `archive_path`, `archive_sha256`, `archive_url`
- `path_in_archive`, `tool_type`, `tool_sha256`, `size`
- `parts`, `download_kind`, `probe_urls`
- `local_cache_path`
- `install_path`
- `installed`

`installed` is a local state check against the selected home directory. It becomes true when the install directory or its `done.txt` marker already exists there; it is not a remote availability check.

Index selection for `query` works like this:

- `--index PATH` uses that local aggregate index file.
- `CLANG_TOOL_CHAIN_BINS_INDEX_URL` overrides the remote index URL when `--index` is not set.
- Otherwise the CLI first tries to discover a repo-relative raw GitHub `tool-index.json`.
- If that remote lookup fails, it falls back to the packaged local index at `tools/data/tool-index.json`.

## Install CLI

`install` resolves an exact tool or filename to one or more archive candidates, then downloads and extracts the selected archive.

- `install` does not support globs.
- Matching is exact against `tool_name` or `file_name`.
- If more than one archive matches, the command exits with an error unless you add filters or pass `--all`.
- Output is always JSON, one object per installed candidate.
- `--dry-run` prints the install plan without downloading or extracting anything.

Install a single archive:

```bash
clang-tool-chain-bins install clang --platform linux --arch x86_64
```

Preview the plan only:

```bash
clang-tool-chain-bins install clang --platform linux --arch x86_64 --dry-run
```

If multiple archives match, narrow the selection with:

- `--platform`
- `--arch`
- `--version`
- `--component`

Use `--all` to install every matching archive.

Typical install result:

```json
{"operation":"install","status":"installed","tool_name":"clang","component":"clang","version":"21.1.5","platform":"linux","arch":"x86_64","install_path":"~/.clang-tool-chain/clang/linux/x86_64","archive_sha256":"...","archive_url":"https://...","dry_run":false}
```

Possible `status` values include:

- `installed`
- `already_installed`
- `dry_run`

The installer is concurrency-safe for each component/platform/arch target:

- archives are cached under `HOME/archives/`
- installs land under `HOME/<component>/<platform>/<arch>/`
- a per-target lock file prevents concurrent writers
- `done.txt` records the installed archive hash and source URL

Download behavior:

- `file://` archive URLs are copied directly
- HTTP(S) archive URLs are downloaded automatically

`install` uses the packaged local aggregate index unless `--index` is provided. Unlike `query`, it does not try to fetch a remote index automatically.

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
