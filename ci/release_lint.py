"""Pre-release sanity checks.

Run as: python ci/release_lint.py

Fails fast (non-zero exit) if anything that would blow up the auto-release
pipeline is detected:

- pyproject.toml `[project] version` matches `[workspace.package] version` in
  Cargo.toml.
- Every per-crate `Cargo.toml` declares `version.workspace = true` or matches
  the workspace version directly.
- Every inter-crate path dependency in the workspace references the matching
  version (so `cargo publish -p X` resolves against crates.io correctly once
  the previous crate in the chain is uploaded).
- The publish order list inside `.github/workflows/auto-release.yml` is a
  topological order over the actual dependency graph.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
CARGO_TOML = REPO_ROOT / "Cargo.toml"
CRATES_DIR = REPO_ROOT / "crates"
AUTO_RELEASE = REPO_ROOT / ".github" / "workflows" / "auto-release.yml"


def _load_toml(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)


def check_versions() -> list[str]:
    errors: list[str] = []
    pyproject = _load_toml(PYPROJECT)
    cargo = _load_toml(CARGO_TOML)

    py_version = pyproject["project"]["version"]
    ws_version = cargo["workspace"]["package"]["version"]

    if py_version != ws_version:
        errors.append(
            f"pyproject.toml version ({py_version}) != Cargo.toml workspace version ({ws_version})"
        )

    for crate_dir in sorted(CRATES_DIR.iterdir()):
        if not (crate_dir / "Cargo.toml").is_file():
            continue
        crate = _load_toml(crate_dir / "Cargo.toml")
        pkg = crate.get("package", {})
        version_field = pkg.get("version")
        if isinstance(version_field, dict) and version_field.get("workspace") is True:
            continue  # inherits workspace.package.version
        if version_field != ws_version:
            errors.append(
                f"{crate_dir.name}/Cargo.toml package.version ({version_field!r}) "
                f"!= workspace version ({ws_version})"
            )
    return errors


def _iter_inter_crate_path_deps() -> list[tuple[str, str, str | None, str]]:
    """Yield (consumer_crate, dep_name, dep_version, dep_path) for every path
    dependency that points at another in-workspace crate.
    """
    results: list[tuple[str, str, str | None, str]] = []
    for crate_dir in sorted(CRATES_DIR.iterdir()):
        cargo_toml = crate_dir / "Cargo.toml"
        if not cargo_toml.is_file():
            continue
        data = _load_toml(cargo_toml)
        for section in ("dependencies", "dev-dependencies", "build-dependencies"):
            deps = data.get(section, {})
            for dep_name, spec in deps.items():
                if not isinstance(spec, dict):
                    continue
                dep_path = spec.get("path")
                if not isinstance(dep_path, str):
                    continue
                # Resolve dep target; only flag in-workspace path deps.
                resolved = (cargo_toml.parent / dep_path).resolve()
                if not resolved.is_relative_to(CRATES_DIR):
                    continue
                results.append((crate_dir.name, dep_name, spec.get("version"), dep_path))
    return results


def check_inter_crate_deps() -> list[str]:
    errors: list[str] = []
    cargo = _load_toml(CARGO_TOML)
    ws_version = cargo["workspace"]["package"]["version"]

    for consumer, dep_name, dep_version, dep_path in _iter_inter_crate_path_deps():
        if dep_version is None:
            errors.append(
                f"{consumer} -> {dep_name}: path dep is missing `version = \"{ws_version}\"`. "
                f"`cargo publish` rejects path-only deps."
            )
            continue
        # Accept either bare "0.4.2" or "^0.4.2" / "=0.4.2" etc. that contains the version string.
        if ws_version not in dep_version:
            errors.append(
                f"{consumer} -> {dep_name}: version requirement {dep_version!r} "
                f"does not include workspace version {ws_version!r}"
            )
    return errors


def check_publish_order() -> list[str]:
    """Confirm the publish-crates loop list is a topological sort over deps."""
    errors: list[str] = []
    if not AUTO_RELEASE.is_file():
        errors.append(f"missing workflow: {AUTO_RELEASE}")
        return errors

    text = AUTO_RELEASE.read_text(encoding="utf-8")
    match = re.search(r"for crate in ((?:ctcb-\S+ ?)+);", text)
    if not match:
        errors.append("could not find `for crate in ctcb-...` loop in auto-release.yml")
        return errors

    order = match.group(1).split()

    # Build dep graph: crate -> set of crates it depends on (in-workspace path deps).
    graph: dict[str, set[str]] = {c: set() for c in order}
    for consumer, dep_name, _v, _p in _iter_inter_crate_path_deps():
        if consumer in graph and dep_name in graph:
            graph[consumer].add(dep_name)

    # Confirm every crate in `crates/` is in the publish order.
    crate_dirs = {d.name for d in CRATES_DIR.iterdir() if (d / "Cargo.toml").is_file()}
    missing = crate_dirs - set(order)
    if missing:
        errors.append(
            f"publish order in auto-release.yml is missing crate(s): {sorted(missing)}"
        )

    extra = set(order) - crate_dirs
    if extra:
        errors.append(
            f"publish order references crate(s) not in crates/: {sorted(extra)}"
        )

    # Topological check: every dep of a crate must appear earlier in the list.
    position = {c: i for i, c in enumerate(order)}
    for consumer, deps in graph.items():
        for dep in deps:
            if position.get(dep, -1) >= position.get(consumer, -1):
                errors.append(
                    f"publish order violates dependency: {consumer} depends on "
                    f"{dep}, but {dep} appears at or after {consumer}"
                )
    return errors


def main() -> int:
    all_errors: list[str] = []
    all_errors.extend(check_versions())
    all_errors.extend(check_inter_crate_deps())
    all_errors.extend(check_publish_order())

    if all_errors:
        for err in all_errors:
            _fail(err)
        print(f"\n{len(all_errors)} release-lint failure(s).", file=sys.stderr)
        return 1

    print("release-lint OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
