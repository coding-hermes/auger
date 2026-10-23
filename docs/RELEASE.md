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