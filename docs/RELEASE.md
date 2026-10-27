# Release Process

This document describes how to cut an auger release. The process is automated by `scripts/release.sh`.

## Prerequisites

- Clean working tree on `main`, up to date with `origin/main`
- `CHANGELOG.md` has a non-empty `## Unreleased` section (or pass `--allow-empty-changelog`)
- All CI checks green on the commit being released
- GitHub CLI (`gh`) authenticated with `repo` scope

## Cutting a Release

```bash
bash scripts/release.sh --version X.Y.Z
```

The script performs these steps in order:

1. **Preflight** — verify clean tree, version format, changelog content, tag doesn't exist
2. **Bump** — update `AUGER_VERSION` in `auger.py`
3. **Changelog** — promote `## Unreleased` to `## vX.Y.Z`, re-open empty `## Unreleased`
4. **OpenAPI** — regenerate `docs/openapi.yaml` via `scripts/gen_openapi.py`
5. **Gates** — run `tests/gate.sh` with `GATE_FULL=1` (full battery, not diff-scoped)
6. **Build** — `python3 -m build` → `dist/auger-X.Y.Z.tar.gz` + `.whl`
7. **SHA256** — write `dist/SHA256SUMS`, verify by reading artifacts back
8. **Commit + Tag** — release commit, then annotated tag `vX.Y.Z`
9. **Push + Release** — fast-forward `main`, push tag, create GitHub release with artifacts

## Dry Run

```bash
bash scripts/release.sh --version X.Y.Z --dry-run
```

Plans every step without mutating anything. Useful for verifying the changelog and version bump before committing.

## Semver

auger follows [Semantic Versioning 2.0.0](https://semver.org/):

- **MAJOR** — incompatible API changes (verb renames, table schema breaks)
- **MINOR** — backward-compatible functionality (new verbs, new tables)
- **PATCH** — backward-compatible bug fixes

The version is stored in `auger.py` as `AUGER_VERSION = "X.Y.Z"` and read by `pyproject.toml` via setuptools' `attr` directive.

## Artifact Set

Every release produces:

- `dist/auger-X.Y.Z.tar.gz` — source distribution
- `dist/auger-X.Y.Z-py3-none-any.whl` — wheel
- `dist/SHA256SUMS` — checksums for both artifacts
- GitHub Release object with all three attached

## Rollback

If a release is broken:

1. Delete the tag: `git tag -d vX.Y.Z && git push origin :refs/tags/vX.Y.Z`
2. Delete the GitHub release: `gh release delete vX.Y.Z`
3. Revert the release commit: `git revert <release-commit-sha>`
4. Push: `git push origin main`

The previous release remains intact. Do not amend or force-push.

## Verifying the v0.2.0 upgrade path

The previous supported release, v0.2.0, predates this repo's packaging metadata:
`git ls-tree v0.2.0` has no `pyproject.toml`, and no auger distribution was ever
published to an index. A plain `pip install auger==0.2.0` therefore fails with
`No matching distribution found for auger==0.2.0` (QA run
auger-qa-2026-10-25-06-39-44, QA-AUGER-11-R2) — that is the real historical
limitation, and the tag is immutable, so it is documented here rather than fixed
by a retroactive publication.

The supported way to exercise the v0.2.0 → current upgrade is the offline
artifact cell:

```bash
bash scripts/upgrade-verify.sh          # add --keep to keep the artifact + scratch dir
```

### How the QA battery must grade the upgrade cell

Any automated QA battery (fresh-box harness, dogfood install-leg, CI) must drive
this offline cell and must NOT probe a remote index for the pin:

- Run `bash scripts/upgrade-verify.sh` (or the pinned seam in
  `tests/test_upgrade_path.py`). The upgrade cell is graded by that script's exit
  code and its `UPGRADE VERIFY: PASS` line.
- An index probe for `auger==0.2.0` (`pip install auger==0.2.0` /
  `pip download auger==0.2.0`) is EXPECTED to fail with
  `No matching distribution found for auger==0.2.0` (from versions: none): the
  tag predates the repo's packaging metadata and no auger distribution was ever
  published to any index. That miss is an expected **SKIP**, not a product
  failure — record the cell as `SKIP` with the no-distribution reason, never as
  `FAIL`. A green upgrade cell on this project always comes from the offline
  artifact path above, never from an index.

The QA battery must invoke that script (or the pinned
`tests/test_upgrade_path.py` seam), not an index install. `pip download
auger==0.2.0` and `pip install auger==0.2.0` are expected to return non-zero
because no v0.2.0 distribution was published; an index miss is an honest
`SKIP` with that reason, not a product failure. The offline script is the
PASS/FAIL upgrade verdict and its `UPGRADE VERIFY: PASS` line is the evidence
that the exact historical pin installed from the locally built artifact.

For a harness that still probes an index, preserve the distinction explicitly:

```bash
if pip download --no-deps auger==0.2.0 >/tmp/auger-index-probe.log 2>&1; then
  printf 'unexpected published distribution: run the offline verifier too\n'
else
  printf 'SKIP: auger==0.2.0 has no published index distribution; use bash scripts/upgrade-verify.sh\n'
fi
```

Do not turn that expected `SKIP` into a release failure and do not publish a
retroactive artifact merely to make the probe green.

The cell exports the v0.2.0 tree into a scratch worktree, injects only the
current `pyproject.toml` (the packaging that post-dates the tag — the module
code stays the tag's), builds a wheel offline with a fresh venv's own
pip+setuptools, then:

1. installs `auger==0.2.0` from that artifact into a fresh venv (`--no-index`,
   so the pin can only be satisfied by the local artifact);
2. upgrades the same venv to this checkout and asserts `auger --version`
   reports the checkout's `AUGER_VERSION` and `auger --help` exits 0;
3. installs the same artifact into a second untouched venv — the previous
   release stays installable on its own.

The regression seam `tests/test_upgrade_path.py` pins the procedure (the exact
failed pin, fresh venvs, offline installs, version + help smoke, and that the
artifact is built from the tag tree, not a copy of HEAD's module). The
end-to-end leg of that seam is opt-in (`AUGER_UPGRADE_VERIFY_LIVE=1`, ~30s of
venv creation); the script itself is the executable cell.

Regenerating a comparable artifact for any future release needs nothing extra:
release tags from v0.3.0 onward carry their own `pyproject.toml`, so a cell
targeting a later tag drops the injection step.

## Gates

The release gate (`tests/gate.sh` with `GATE_FULL=1`) runs:

- **Syntax** — `python3 -m py_compile auger.py`
- **Lint** — `ruff check auger.py tests/`
- **Tests** — full pytest suite (`tests/test_auger.py`, `tests/test_openapi_parity.py`, `tests/test_api_service.py`)
- **Smoke** — end-to-end live loop (`tests/smoke.sh`)
- **OpenAPI parity** — verify `docs/openapi.yaml` matches the live schema

All arms must pass. The gate budget is 2400s (see `tests/gate.sh`); under heavy fleet load, override with `AUGER_GATE_PYTEST_BUDGET=3600s`.

## Post-Release

After the release lands:

- Verify the GitHub Release page shows all three artifacts
- Check that `pip install auger==X.Y.Z` resolves (PyPI publish is manual for now)
- Update the fleet's auger pin if this is a breaking change

## Troubleshooting

**"Changelog Unreleased section is empty"** — add at least one bullet under `## Unreleased` before releasing, or pass `--allow-empty-changelog`.

**"Tag vX.Y.Z already exists"** — the version is already released. Bump to the next version or delete the tag if this is a rollback.

**"HEAD is not at origin/main's tip"** — `git pull --rebase origin main` first.

**Gate timeout** — the pytest arm exceeded its budget. Increase with `AUGER_GATE_PYTEST_BUDGET=3600s` or investigate slow tests.

</content>