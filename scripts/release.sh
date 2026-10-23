#!/usr/bin/env bash
# scripts/release.sh — cut an auger release end to end.
#
#   bash scripts/release.sh --version 0.4.0
#   bash scripts/release.sh --version 0.4.0 --dry-run      # plan only, no mutation
#
# The release is one ordered act, and every step is named in the output so a failed run
# says exactly where it stopped:
#
#   1. preflight       every precondition, checked BEFORE anything is mutated
#   2. bump            AUGER_VERSION in auger.py (the single source of truth; pyproject.toml
#                      reads it through setuptools' `attr` directive, so nothing else needs
#                      a version edit)
#   3. changelog       promote `## Unreleased` to `## vX.Y.Z` and re-open an empty
#                      `## Unreleased` beside it; the bullets double as the release notes
#   4. openapi         regenerate docs/openapi.yaml (scripts/gen_openapi.py), byte-identical
#                      to what the parity arm checks
#   5. gates           tests/gate.sh, forced full (GATE_FULL=1) — see docs/RELEASE.md
#   6. build           sdist + wheel into dist/
#   7. sha256          dist/SHA256SUMS, then verified by reading the artifacts back
#   8. commit + tag    the release commit, then annotated tag vX.Y.Z
#   9. push + release  fast-forward main, push the tag, create the GitHub release
#
# THE ORDER IS LOAD-BEARING, not cosmetic: the build happens before the commit so a broken
# build aborts with nothing committed, and the commit happens before the tag so the tag can
# never point at a tree without its own version bump.
#
# This script refuses to guess. Anything it cannot verify (tag already exists, tree dirty,
# HEAD not at origin/main's tip, an empty Unreleased section) stops the run at step 1, while
# the tree is still clean and `git reset --hard` is not needed.
#
# Exit codes: 0 released, 1 a step failed mid-run, 2 usage error or a refused preflight.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VERSION=""
DRY_RUN=0
RUN_GATES=1
ALLOW_EMPTY_CHANGELOG=0
PUSH=1
BASE_BRANCH="main"

usage() {
  cat <<'USAGE'
usage: scripts/release.sh --version X.Y.Z [options]

  --version X.Y.Z          the version to release (strict MAJOR.MINOR.PATCH, no prefix,
                           no pre-release suffix). Required.
  --dry-run                run every read-only preflight check, print the plan, mutate
                           nothing (no gates, no build, no commit, no tag).
  --skip-gates             do not run tests/gate.sh. Recorded as SKIPPED in the closing
                           summary; only for re-cutting a tag on a tree CI already proved.
  --allow-empty-changelog  permit a release whose `## Unreleased` section is empty.
  --no-push                stop after the local commit + tag: no push, no GitHub release.
  -h, --help

What it edits: auger.py (AUGER_VERSION), CHANGELOG.md, docs/openapi.yaml (regenerated),
dist/ (build output, gitignored). Nothing else is touched.

Env: AUGER_GATE_PYTEST_BUDGET / AUGER_GATE_PYTEST_KILL_AFTER are passed through to
tests/gate.sh, and AUGER_GATE_FULL=1 forces the full gate regardless of the diff.
USAGE
}

die_usage() { printf 'error: %s\n\n' "$1" >&2; usage >&2; exit 2; }
# Preflight refusals exit 2 (nothing was mutated); a step failing mid-run exits 1.
PHASE="run"
die() {
  if [ "$PHASE" = "preflight" ]; then
    printf 'RELEASE REFUSED (preflight): %s\n' "$1" >&2
    exit 2
  fi
  printf 'RELEASE FAILED: %s\n' "$1" >&2
  exit 1
}
note()      { printf '  %s\n' "$*"; }
step()      { printf '\n[%s] %s\n' "$1" "$2"; }

while [ "$#" -gt 0 ]; do
  case "$1" in
    --version)                [ "$#" -ge 2 ] || die_usage "--version needs a value"; VERSION="$2"; shift 2 ;;
    --version=*)              VERSION="${1#*=}"; shift ;;
    --dry-run)                DRY_RUN=1; shift ;;
    --skip-gates)             RUN_GATES=0; shift ;;
    --allow-empty-changelog)  ALLOW_EMPTY_CHANGELOG=1; shift ;;
    --no-push)                PUSH=0; shift ;;
    -h|--help)                usage; exit 0 ;;
    *)                        die_usage "unknown argument: $1" ;;
  esac
done

[ -n "$VERSION" ] || die_usage "--version X.Y.Z is required"
TAG="v${VERSION}"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/auger-release.XXXXXX")"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

# ---------------------------------------------------------------- step 1: preflight
# Every check here is read-only. The point is that a run which CANNOT succeed dies with a
# clean tree, before step 2 makes the first edit.
preflight() {
  PHASE="preflight"
  step 1/9 "preflight (every precondition, before anything is mutated)"

  # --- the version itself -------------------------------------------------------
  if ! [[ "$VERSION" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]; then
    die "version '$VERSION' is not strict MAJOR.MINOR.PATCH (digits only, no leading zeros, no 'v', no -rc suffix). See docs/RELEASE.md."
  fi
  note "version: $VERSION   tag: $TAG"

  # --- tools --------------------------------------------------------------------
  local t
  for t in git python3 sha256sum; do
    command -v "$t" >/dev/null 2>&1 || die "'$t' is not on PATH"
  done
  python3 -m build --version >/dev/null 2>&1 \
    || die "'python3 -m build' is unavailable (pip install build)"
  [ "$RUN_GATES" = "1" ] || note "gates: --skip-gates given; the gate arm will be SKIPPED, not passed"
  if [ "$PUSH" = "1" ]; then
    command -v gh >/dev/null 2>&1 || die "'gh' (GitHub CLI) is required to create the release; use --no-push to stop after the tag"
    gh auth status >/dev/null 2>&1 || die "gh is not authenticated for this host (gh auth login)"
  else
    note "push + GitHub release: --no-push given; this run stops after the local tag"
  fi

  # --- repo state ---------------------------------------------------------------
  git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "$ROOT is not a git checkout"
  [ -f auger.py ] || die "auger.py missing from $ROOT"
  [ -f CHANGELOG.md ] || die "CHANGELOG.md missing from $ROOT"

  local branch head remote_head
  branch="$(git symbolic-ref -q --short HEAD || true)"
  [ -n "$branch" ] || die "detached HEAD — check out $BASE_BRANCH (or a branch at its tip) before releasing"
  head="$(git rev-parse HEAD)"
  if git rev-parse --verify -q "refs/remotes/origin/$BASE_BRANCH" >/dev/null; then
    remote_head="$(git rev-parse "refs/remotes/origin/$BASE_BRANCH")"
    [ "$head" = "$remote_head" ] || die \
      "HEAD ($(git rev-parse --short HEAD)) is not origin/$BASE_BRANCH's tip ($(git rev-parse --short "$remote_head")). Fetch + merge/push first: a release commit must fast-forward $BASE_BRANCH."
  else
    note "WARNING: no origin/$BASE_BRANCH ref — cannot prove HEAD is published (fetch skipped)"
  fi
  [ "$branch" = "$BASE_BRANCH" ] || note "WARNING: on branch '$branch' (not $BASE_BRANCH) — the release commit will be pushed to $BASE_BRANCH"

  # Untracked files count as dirty: the release commit stages named paths only, so an
  # untracked file would silently stay out of the release.
  if [ -n "$(git status --porcelain)" ]; then
    git status --short >&2
    die "working tree is not clean — commit or stash first (dist/ is gitignored, so build output is fine)"
  fi
  note "tree: clean at $(git rev-parse --short HEAD) on '$branch'"

  # --- tag must be free, locally AND on the remote ------------------------------
  if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
    die "tag $TAG already exists locally — pick the next version (see CHANGELOG.md)"
  fi
  if [ "$PUSH" = "1" ]; then
    local remote_tag
    if ! remote_tag="$(git ls-remote --tags origin "refs/tags/$TAG")"; then
      die "could not query origin for existing tags (network/auth) — a tag collision must be proved, not assumed"
    fi
    [ -z "$remote_tag" ] || die "tag $TAG already exists on origin — pick the next version"
  fi
  note "tag: $TAG is free"

  # --- the files the mutation steps will edit ----------------------------------
  python3 - "$VERSION" "$ALLOW_EMPTY_CHANGELOG" <<'PY' || exit 2
import re, sys

version, allow_empty = sys.argv[1], sys.argv[2] == "1"
tag = f"v{version}"

src = open("auger.py", encoding="utf-8").read()
decl = re.findall(r'^AUGER_VERSION = "([^"]*)"$', src, re.MULTILINE)
if len(decl) != 1:
    sys.exit(f"error: expected exactly one AUGER_VERSION declaration in auger.py, found {len(decl)}")
if decl[0] == version:
    sys.exit(f"error: auger.py already declares AUGER_VERSION = \"{version}\"")

changelog = open("CHANGELOG.md", encoding="utf-8").read()
unreleased = list(re.finditer(r'^## Unreleased[ \t]*$', changelog, re.MULTILINE))
if len(unreleased) != 1:
    sys.exit(f"error: expected exactly one '## Unreleased' heading in CHANGELOG.md, found {len(unreleased)}")
if re.search(rf'^## {re.escape(tag)}[ \t]*$', changelog, re.MULTILINE):
    sys.exit(f"error: CHANGELOG.md already documents '{tag}'")

start = unreleased[0].end()
nxt = re.compile(r'^## ', re.MULTILINE).search(changelog, start)
body = changelog[start:nxt.start() if nxt else len(changelog)]
bullets = [line for line in body.splitlines() if line.strip()]
if not bullets and not allow_empty:
    sys.exit("error: the '## Unreleased' section of CHANGELOG.md is empty — write the release "
             "bullets first, or pass --allow-empty-changelog for a no-change release")

print(f"  auger.py: AUGER_VERSION {decl[0]} -> {version}")
print(f"  CHANGELOG.md: '## Unreleased' ({len(bullets)} bullet(s)) -> '## {tag}' + fresh '## Unreleased'")
PY
}

# ---------------------------------------------------------------- step 2: bump
bump_version() {
  step 2/9 "bump AUGER_VERSION in auger.py"
  python3 - "$VERSION" <<'PY'
import re, sys

version = sys.argv[1]
path = "auger.py"
src = open(path, encoding="utf-8").read()
pattern = re.compile(r'^AUGER_VERSION = "([^"]*)"$', re.MULTILINE)
found = pattern.findall(src)
if len(found) != 1:
    sys.exit(f"error: expected exactly one AUGER_VERSION declaration in {path}, found {len(found)}")
current = found[0]
out = pattern.sub(lambda _m: f'AUGER_VERSION = "{version}"', src)
if out == src:
    sys.exit("error: AUGER_VERSION rewrite produced no change")
open(path, "w", encoding="utf-8").write(out)
print(f"  auger.py: AUGER_VERSION {current} -> {version}")
PY
  note "pyproject.toml needs no edit: [tool.setuptools.dynamic] version = { attr = \"auger.AUGER_VERSION\" }"
}

# ---------------------------------------------------------------- step 3: changelog
promote_changelog() {
  step 3/9 "promote CHANGELOG '## Unreleased' -> '## $TAG' and re-open Unreleased"
  python3 - "$VERSION" "$WORK/notes.md" "$ALLOW_EMPTY_CHANGELOG" <<'PY'
import re, sys
from datetime import datetime, timezone

version, notes_path, allow_empty = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
tag = f"v{version}"
path = "CHANGELOG.md"
src = open(path, encoding="utf-8").read()

unreleased = list(re.finditer(r'^## Unreleased[ \t]*$', src, re.MULTILINE))
if len(unreleased) != 1:
    sys.exit(f"error: expected exactly one '## Unreleased' heading in {path}, found {len(unreleased)}")
if re.search(rf'^## {re.escape(tag)}[ \t]*$', src, re.MULTILINE):
    sys.exit(f"error: {path} already documents '{tag}'")
heading = unreleased[0]
nxt = re.compile(r'^## ', re.MULTILINE).search(src, heading.end())
block_end = nxt.start() if nxt else len(src)

# The bullets as written under Unreleased, verbatim: same words, same order, same indent.
bullets = [line for line in src[heading.end():block_end].split("\n") if line.strip()]
if not bullets and not allow_empty:
    sys.exit("error: the '## Unreleased' section is empty — write the release bullets first, "
             "or pass --allow-empty-changelog for a no-change release")

stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
section = f"## {tag}\n\nTagged {tag} on {stamp}.\n"
if bullets:
    section += "\n" + "\n".join(bullets) + "\n"

# The promoted section stays exactly where Unreleased stood; the fresh empty Unreleased is
# opened directly above it, so the next release edits one obvious heading.
out = src[:heading.start()] + "## Unreleased\n\n" + section + ("\n" + src[block_end:] if nxt else "\n")
open(path, "w", encoding="utf-8").write(out)

# Release notes = the same bullets. One source, never retyped.
notes = "\n".join(bullets).strip() or "_No user-facing changes were recorded for this release._"
with open(notes_path, "w", encoding="utf-8") as fh:
    fh.write(notes + "\n")

print(f"  {path}: {len(bullets)} bullet(s) promoted to '## {tag}'; '## Unreleased' re-opened empty")
print(f"  release notes: {len(notes.splitlines())} line(s) staged for the tag and the GitHub release")
PY
}

# ---------------------------------------------------------------- step 4: openapi
regen_openapi() {
  step 4/9 "regenerate docs/openapi.yaml"
  OPENAPI_REGEN=0
  if [ -f scripts/gen_openapi.py ]; then
    # Generate to a temp file and move it in, so a crashing generator cannot truncate the
    # committed contract (the gate's parity arm compares this file byte-for-byte).
    local tmp="$WORK/openapi.yaml"
    if ! python3 scripts/gen_openapi.py --output "$tmp" 1>&2; then
      die "scripts/gen_openapi.py failed — docs/openapi.yaml left untouched"
    fi
    if cmp -s "$tmp" docs/openapi.yaml; then
      note "docs/openapi.yaml already matches the generator (no change)"
    else
      mv "$tmp" docs/openapi.yaml
      note "docs/openapi.yaml regenerated from scripts/gen_openapi.py"
    fi
    OPENAPI_REGEN=1
  else
    echo "  SKIPPED [openapi]: scripts/gen_openapi.py is not in this checkout — docs/openapi.yaml was NOT regenerated" >&2
    OPENAPI_REGEN=0
  fi
}

# ---------------------------------------------------------------- step 5: gates
run_gates() {
  step 5/9 "pre-release gates (tests/gate.sh, forced full)"
  if [ "$RUN_GATES" != "1" ]; then
    echo "  SKIPPED [gates]: --skip-gates was given — the gate arm did NOT run, and this release is not gate-proved" >&2
    GATES_SKIPPED=1
    return 0
  fi
  # Stage first: tests/gate.sh decides its doc-only fast path from `git diff --cached`, and
  # an unstaged auger.py would let it skip pytest on the exact commit that changes the code.
  git add auger.py CHANGELOG.md
  if [ "$OPENAPI_REGEN" = "1" ]; then
    git add docs/openapi.yaml
  fi
  local rc=0
  GATE_FULL=1 bash tests/gate.sh || rc=$?
  if [ "$rc" -ne 0 ]; then
    printf '  the version bump above is on disk and staged, NOT committed. To abandon the run:\n' >&2
    printf '    git restore --staged --worktree auger.py CHANGELOG.md docs/openapi.yaml\n' >&2
    die "tests/gate.sh exited $rc — nothing committed, nothing tagged. Fix the gate and re-run."
  fi
  GATES_SKIPPED=0
  note "gate: PASS"
}

# ---------------------------------------------------------------- step 6: build
build_artifacts() {
  step 6/9 "build sdist + wheel"
  rm -rf dist
  python3 -m build --outdir dist >"$WORK/build.log" 2>&1 || {
    sed -n '1,40p' "$WORK/build.log" >&2
    die "python3 -m build failed (full log above); nothing committed, nothing tagged"
  }
  local sdist wheel
  shopt -s nullglob
  local sdists=(dist/auger-"$VERSION".tar.gz)
  local wheels=(dist/auger-"$VERSION"-*.whl)
  shopt -u nullglob
  [ "${#sdists[@]}" -eq 1 ] || die "expected exactly one sdist dist/auger-$VERSION.tar.gz, found ${#sdists[@]} (is pyproject's version attrs still wired to auger.AUGER_VERSION?)"
  [ "${#wheels[@]}" -eq 1 ] || die "expected exactly one wheel dist/auger-$VERSION-*.whl, found ${#wheels[@]}"
  sdist="${sdists[0]}"; wheel="${wheels[0]}"
  note "sdist: $sdist ($(wc -c <"$sdist") bytes)"
  note "wheel: $wheel ($(wc -c <"$wheel") bytes)"
  ARTIFACTS=("$sdist" "$wheel")
}

# ---------------------------------------------------------------- step 7: sha256
checksum_artifacts() {
  step 7/9 "sha256 of the artifacts"
  ( cd dist && sha256sum ./auger-"$VERSION".tar.gz ./auger-"$VERSION"-*.whl > SHA256SUMS )
  # Read the bytes back: never publish a checksum nobody verified.
  ( cd dist && sha256sum -c SHA256SUMS ) | sed 's/^/  /'
  note "dist/SHA256SUMS:"
  sed 's/^/    /' dist/SHA256SUMS
}

# ---------------------------------------------------------------- step 8: commit + tag
commit_and_tag() {
  step 8/9 "commit the release, then tag $TAG"
  local before after
  before="$(git rev-parse HEAD)"

  {
    printf 'release: %s\n\n' "$TAG"
    printf 'Bump AUGER_VERSION to %s, promote CHANGELOG Unreleased -> %s, regenerate\n' "$VERSION" "$TAG"
    printf 'docs/openapi.yaml.\n\n'
    printf 'Artifacts built from this commit:\n'
    printf '  %s\n  %s\n  dist/SHA256SUMS\n' "${ARTIFACTS[@]}"
    printf '\nSee docs/RELEASE.md for the gate set and the rollback path.\n'
  } >"$WORK/commit-msg.txt"

  git add auger.py CHANGELOG.md
  if [ "$OPENAPI_REGEN" = "1" ]; then
    git add docs/openapi.yaml
  fi
  git commit -F "$WORK/commit-msg.txt" >/dev/null

  after="$(git rev-parse HEAD)"
  # A commit that leaves HEAD where it was is the gitreins-python shape: exit 0, no commit.
  [ "$after" != "$before" ] || die "git commit reported success but HEAD did not move — nothing to tag. Inspect the tree before re-running."
  note "commit: $(git rev-parse --short "$after") $(git log -1 --format=%s)"

  git tag -a "$TAG" -F "$WORK/notes.md"
  # The tag must point at the release commit, not at its parent or a re-read of a moving ref.
  [ "$(git rev-parse "$TAG^{commit}")" = "$after" ] || die "tag $TAG does not point at the release commit"
  note "tag: $TAG -> $(git rev-parse --short "$TAG^{commit}") (annotated)"
}

# ---------------------------------------------------------------- step 9: push + release
push_and_release() {
  step 9/9 "push $BASE_BRANCH + tag, create the GitHub release"
  if [ "$PUSH" != "1" ]; then
    echo "  SKIPPED [push]: --no-push was given — nothing was pushed and NO GitHub release exists." >&2
    echo "  Finish by hand when ready:" >&2
    echo "    git push origin HEAD:$BASE_BRANCH" >&2
    echo "    git push origin $TAG" >&2
    echo "    gh release create $TAG --title $TAG --notes-file <notes> --verify-tag dist/*" >&2
    return 0
  fi

  git push origin "HEAD:$BASE_BRANCH"
  git push origin "$TAG"
  note "pushed $BASE_BRANCH and $TAG to origin"

  gh release create "$TAG" \
    --title "$TAG" \
    --notes-file "$WORK/notes.md" \
    --verify-tag \
    "${ARTIFACTS[@]}" dist/SHA256SUMS

  # Read the release back: a successful `gh` call is not the same as a published release.
  local url
  url="$(gh release view "$TAG" --json url --jq .url)"
  note "release: $url"
  local assets
  assets="$(gh release view "$TAG" --json assets --jq '.assets | length')"
  [ "$assets" -ge 3 ] || die "release $TAG was created but lists $assets asset(s); expected sdist + wheel + SHA256SUMS"
  note "assets: $assets (sdist, wheel, SHA256SUMS)"
}

# ---------------------------------------------------------------- main
preflight

if [ "$DRY_RUN" = "1" ]; then
  cat <<PLAN

DRY RUN — no file was changed, no gate ran, no artifact was built, nothing was committed.
The real run would perform, in this order:

  2. auger.py:        AUGER_VERSION -> "$VERSION"
  3. CHANGELOG.md:    '## Unreleased' -> '## $TAG' + 'Tagged $TAG on <today>.' + fresh '## Unreleased'
  4. docs/openapi.yaml: regenerate via scripts/gen_openapi.py (byte-identical to the committed file,
                      or rewritten when the generator's output moved)
  5. gates:           $([ "$RUN_GATES" = "1" ] && echo "GATE_FULL=1 bash tests/gate.sh" || echo "SKIPPED (--skip-gates)")
  6. build:           python3 -m build --outdir dist   -> dist/auger-$VERSION.tar.gz + wheel
  7. sha256:          dist/SHA256SUMS, verified with sha256sum -c
  8. commit + tag:    'release: $TAG', then annotated tag $TAG
  9. push + release:  $([ "$PUSH" = "1" ] && echo "git push origin HEAD:$BASE_BRANCH, git push origin $TAG, gh release create $TAG" || echo "SKIPPED (--no-push)")

Preflight is the whole contract: every check above already passed against this tree.
PLAN
  exit 0
fi

bump_version
promote_changelog
regen_openapi
run_gates
build_artifacts
checksum_artifacts
commit_and_tag
push_and_release

printf '\nRELEASE OK: %s\n' "$TAG"
printf '  commit + tag: %s\n' "$(git rev-parse --short HEAD)"
printf '  artifacts:    %s\n' "${ARTIFACTS[*]} dist/SHA256SUMS"
if [ "$GATES_SKIPPED" = "1" ]; then
  printf '  gates:        SKIPPED (--skip-gates) — not gate-proved\n'
fi
printf '\nFollow-up (not automated, deliberately): update the README status line to %s, and add\n' "$TAG"
printf 'the release link to CHANGELOG.md the way earlier sections carry theirs.\n'
