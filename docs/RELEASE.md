# Release process

This repo uses **tag-driven releases**. Pushing a `vX.Y.Z` tag triggers
`.github/workflows/release.yml`, which builds, attests, and publishes
to PyPI, crates.io, and GitHub Releases in one shot.

## Cutting a release

1. Bump the version in `Cargo.toml` (`[workspace.package].version`) and
   `pyproject.toml`. Both must match — the preflight job will fail otherwise.
2. Update every `crates/*/Cargo.toml` with the new internal-dep version
   (the 16 `version = "X.Y.Z"` literals on `ctcb-*` deps). The preflight
   job validates these too. CI rewrites them to exact pins (`=X.Y.Z`)
   before publishing to crates.io; your committed source keeps the loose
   form so local builds resolve to the path dep.
3. Commit, push, then run:

   ```bash
   ./publish
   ```

   This validates metadata, checks PyPI for the version, creates the
   `vX.Y.Z` tag locally, and pushes it. The push triggers CI.
4. Watch CI at `https://github.com/<owner>/<repo>/actions`. The whole
   pipeline takes ~25 minutes (5-target matrix dominates).

## Pipeline shape

```
preflight ──┬─► build (5 targets × bin+wheel, attested)
            └─► sdist (attested)
                   │
                   ▼
            publish-pypi (env: pypi, OIDC, PEP 740 attestations)
                   │
                   ▼
            publish-crates (9 crates, dependency order, idempotent skip)
                   │
                   ▼
            publish-release (GitHub Release with all artifacts + SHA256SUMS)
```

Re-running the same tag is idempotent: preflight detects already-published
versions on both registries and short-circuits the affected jobs.

## Verifying attestations as a downstream user

Every binary, wheel, and sdist gets a SLSA v1 build provenance attestation
via [actions/attest-build-provenance][attest-action]. To verify locally:

```bash
gh attestation verify <file> --repo zackees/clang-tool-chain-bins
```

PyPI also shows PEP 740 attestations on the project's release page.

[attest-action]: https://github.com/actions/attest-build-provenance

## One-time setup (maintainer)

These cannot be put in repo code; they require admin access on
GitHub / PyPI / crates.io.

### 1. crates.io scoped token

1. Go to https://crates.io/settings/tokens
2. Click **New Token**
3. Name: `clang-tool-chain-bins-release`
4. Endpoints: `publish-update` only (not `publish-new`, since all 9 crates
   already exist on crates.io)
5. Crate scopes: enter each of the 9 crates explicitly:
   - `ctcb-core`, `ctcb-checksum`, `ctcb-archive`, `ctcb-dedup`,
     `ctcb-download`, `ctcb-strip`, `ctcb-manifest`, `ctcb-split`, `ctcb-cli`
6. Expiry: 90 days (rotate on renewal)
7. Copy the token. Add it as a repository secret:

   ```bash
   gh secret set CARGO_REGISTRY_TOKEN --body '<paste>'
   ```

### 2. PyPI Trusted Publisher

1. Go to https://pypi.org/manage/project/clang-tool-chain-bins/settings/publishing/
2. Click **Add a new pending publisher** (or **Add publisher** if the
   project already has one)
3. Fill in:
   - **PyPI Project Name**: `clang-tool-chain-bins`
   - **Owner**: `zackees`
   - **Repository name**: `clang-tool-chain-bins`
   - **Workflow name**: `release.yml`
   - **Environment name**: `pypi`
4. Save.

### 3. GitHub `pypi` environment

1. Go to https://github.com/zackees/clang-tool-chain-bins/settings/environments
2. Click **New environment**, name it `pypi`
3. Under **Deployment branches and tags**, select **Selected branches
   and tags** and add the rule `v*` — only tag pushes matching `v*` can
   deploy to this environment
4. Do **not** add Required reviewers — releases are fully automatic on
   tag push. The safety net is the immutable-tag ruleset (step 4 below)
   plus the branch ruleset that requires PRs into `main`
5. Save

### 4. Branch + tag protection ruleset

Apply a Repository Ruleset that locks `main` and makes `v*` tags
immutable.

```bash
# Save the ruleset spec
cat > /tmp/ctcb-ruleset.json <<'JSON'
{
  "name": "ctcb-release-protection",
  "target": "branch",
  "enforcement": "active",
  "conditions": {
    "ref_name": {
      "include": ["~DEFAULT_BRANCH"],
      "exclude": []
    }
  },
  "rules": [
    {"type": "deletion"},
    {"type": "non_fast_forward"},
    {"type": "pull_request", "parameters": {
      "required_approving_review_count": 0,
      "dismiss_stale_reviews_on_push": true,
      "require_code_owner_review": false,
      "require_last_push_approval": false,
      "required_review_thread_resolution": false
    }},
    {"type": "required_status_checks", "parameters": {
      "strict_required_status_checks_policy": false,
      "required_status_checks": [
        {"context": "Lint"},
        {"context": "Test (ubuntu-latest)"},
        {"context": "Test (windows-latest)"},
        {"context": "Test (macos-latest)"}
      ]
    }}
  ]
}
JSON

# Apply branch protection
gh api --method POST \
  -H "Accept: application/vnd.github+json" \
  /repos/zackees/clang-tool-chain-bins/rulesets \
  --input /tmp/ctcb-ruleset.json

# Tag immutability ruleset (separate; tag rulesets use target=tag)
cat > /tmp/ctcb-tag-ruleset.json <<'JSON'
{
  "name": "ctcb-immutable-release-tags",
  "target": "tag",
  "enforcement": "active",
  "conditions": {
    "ref_name": {
      "include": ["refs/tags/v*"],
      "exclude": []
    }
  },
  "rules": [
    {"type": "deletion"},
    {"type": "update"},
    {"type": "non_fast_forward"}
  ]
}
JSON

gh api --method POST \
  -H "Accept: application/vnd.github+json" \
  /repos/zackees/clang-tool-chain-bins/rulesets \
  --input /tmp/ctcb-tag-ruleset.json
```

After applying:
- Pushes to `main` must come via PR with passing CI
- `vX.Y.Z` tags can only be created (never moved or deleted) once published
- Force-push to `main` is rejected

To verify:

```bash
gh api /repos/zackees/clang-tool-chain-bins/rulesets
```

## Recovering from a partial release

If CI fails halfway (e.g., crates.io publish dies on crate 6 of 9):

1. Fix the underlying issue (network blip → just rerun; bad code → push
   a new commit, but you'll need a new tag since `vX.Y.Z` is now
   immutable — bump to `vX.Y.Z+1`).
2. **Re-run the workflow** from the failed job: GitHub Actions UI →
   workflow run → **Re-run failed jobs**.
3. Preflight will detect already-published versions and skip jobs;
   `publish-crates` will skip already-uploaded crates per-crate.

If you need to advance past the immutable tag, just bump versions and
ship `vX.Y.Z+1`. Never try to overwrite a published tag.
