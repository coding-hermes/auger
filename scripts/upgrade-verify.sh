#!/usr/bin/env bash
# scripts/upgrade-verify.sh — the release-upgrade cell, reproducible and offline.
#
#   bash scripts/upgrade-verify.sh            # v0.2.0 (tag) -> this checkout
#   bash scripts/upgrade-verify.sh --keep     # keep the scratch dir + artifact, print paths
#
# Why this exists (QA-AUGER-11 / QA-AUGER-11-R2, QA run auger-qa-2026-10-25-06-39-44):
# the QA upgrade cell ran `pip install auger==0.2.0` and got
#   ERROR: No matching distribution found for auger==0.2.0
# because the v0.2.0 tag predates the repo's packaging metadata (pyproject.toml, commit
# 22698ee) and no auger distribution was ever published to an index. The tag itself is
# immutable and is not rewritten; no PyPI publication is fabricated here.
#
# The honest, reproducible replacement this script proves:
#
#   1. export the v0.2.0 TREE into a scratch dir (git worktree add --detach)
#   2. inject ONLY pyproject.toml — the packaging declaration that post-dates the tag —
#      into the scratch copy. NO module code is taken from HEAD: the sdist's auger.py is
#      byte-for-byte the tag's (its AUGER_VERSION = "0.2" and auger:main entry point are
#      what the current pyproject.toml already points at).
#   3. build the artifact with the fresh venv's own pip+setuptools, offline
#      (pip wheel --no-deps --no-build-isolation) — no 'build' package, no network
#   4. fresh venv: pip install --no-index --find-links <dist> auger==0.2.0
#      -> the artifact really installs, with the exact pin the QA cell failed on
#   5. upgrade that same venv: pip install --no-index --no-build-isolation --force-reinstall
#      <this checkout> -> auger --version reports the checkout's AUGER_VERSION, and
#      `auger --help` exits 0 (the basic CLI smoke; needs no substrate)
#   6. a second, untouched venv installs the same artifact and reports its version —
#      the previous release stays installable on its own
#
# The scratch dir is removed on exit unless --keep is given. Nothing outside /tmp (or
# TMPDIR) and the repo's git worktree metadata is touched; no tag, branch, or release
# state is mutated.
#
# Exit codes: 0 verified, 1 a step failed mid-run, 2 refused preflight (nothing built).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PREV_TAG="v0.2.0"
PREV_PIN="auger==0.2.0"   # the exact requirement string the QA cell failed on
PREV_VERSION="0.2"        # the version string the tag's own AUGER_VERSION declares

KEEP=0
case "${1:-}" in
  "") ;;
  --keep) KEEP=1 ;;
  -h|--help)
    sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
  *) printf 'error: unknown argument: %s (try --help)\n' "$1" >&2; exit 2 ;;
esac

PHASE="preflight"
die() {
  if [ "$PHASE" = "preflight" ]; then
    printf 'UPGRADE VERIFY REFUSED (preflight): %s\n' "$1" >&2
    exit 2
  fi
  printf 'UPGRADE VERIFY FAILED: %s\n' "$1" >&2
  exit 1
}
note() { printf '  %s\n' "$*"; }
step() { printf '\n[%s] %s\n' "$1" "$2"; }

# ---------------------------------------------------------------- preflight
command -v git >/dev/null 2>&1 || die "git is not on PATH"
command -v python3 >/dev/null 2>&1 || die "python3 is not on PATH"
[ -f auger.py ] || die "auger.py missing from $ROOT"
[ -f pyproject.toml ] || die "pyproject.toml missing from $ROOT (packaging metadata is the bridge)"

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "$ROOT is not a git checkout"

# The tag must be present in THIS checkout: a shallow clone (CI default fetch-depth=1)
# does not carry it, and guessing is exactly what this cell refuses to do.
if ! git cat-file -e "${PREV_TAG}^{commit}" 2>/dev/null; then
  die "tag ${PREV_TAG} is not present in this checkout (shallow clone? run: git fetch --tags ${PREV_TAG})"
fi
note "tag: ${PREV_TAG} present"

# The version the upgraded venv must report: the checkout's own AUGER_VERSION, read the
# same way scripts/release.sh reads it (exactly one declaration, no import).
CUR_VERSION="$(python3 - "$ROOT/auger.py" <<'PY'
import re, sys
src = open(sys.argv[1], encoding="utf-8").read()
found = re.findall(r'^AUGER_VERSION = "([^"]*)"$', src, re.MULTILINE)
if len(found) != 1:
    sys.exit(f"error: expected exactly one AUGER_VERSION declaration, found {len(found)}")
print(found[0])
PY
)" || die "could not read AUGER_VERSION from auger.py"
[ "$CUR_VERSION" != "$PREV_VERSION" ] || die "this checkout declares AUGER_VERSION $CUR_VERSION — there is no upgrade delta to verify"
note "upgrade under test: ${PREV_TAG} (auger ${PREV_VERSION}) -> checkout (auger ${CUR_VERSION})"

# ---------------------------------------------------------------- scratch dir
WORK="$(mktemp -d "${TMPDIR:-/tmp}/auger-upgrade-verify.XXXXXX")"
cleanup() {
  if [ "$KEEP" = "1" ]; then
    printf '\nKEEP: scratch dir kept at %s\n' "$WORK"
    return
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT

step 1/6 "export the ${PREV_TAG} tree to a scratch dir"
# Stale worktree admin entries from an interrupted earlier run must never block this one.
git worktree prune
git worktree add --detach "$WORK/prev-src" "$PREV_TAG" >/dev/null 2>&1 \
  || die "git worktree add of ${PREV_TAG} failed"
wt_remove() { git worktree remove --force "$WORK/prev-src" >/dev/null 2>&1 || true; }
trap 'wt_remove; cleanup' EXIT
note "exported: $WORK/prev-src"

step 2/6 "inject the current packaging declaration (pyproject.toml) — nothing else"
cp "$ROOT/pyproject.toml" "$WORK/prev-src/pyproject.toml"
note "prev-src now carries the tag's auger.py + the checkout's pyproject.toml (the packaging that post-dates the tag)"
if ! cmp -s "$ROOT/auger.py" "$WORK/prev-src/auger.py"; then
  note "note: auger.py differs from HEAD, as expected — the artifact is built from the TAG's module"
fi

step 3/6 "build the artifact offline with the venv's own pip+setuptools"
python3 -m venv "$WORK/builder-venv" 2>/dev/null || die "python3 -m venv failed — install ensurepip (python3-venv)"
BUILDER_PIP="$WORK/builder-venv/bin/pip"
"$BUILDER_PIP" --version >/dev/null 2>&1 || die "the fresh venv has no working pip"
if ! "$BUILDER_PIP" show setuptools >/dev/null 2>&1; then
  die "the fresh venv has no setuptools and this cell is offline — build the artifact on a python whose venv ships setuptools (python3.11 does)"
fi
"$BUILDER_PIP" wheel --no-deps --no-build-isolation --wheel-dir "$WORK/dist" "$WORK/prev-src" \
  >"$WORK/build.log" 2>&1 || { sed -n '1,30p' "$WORK/build.log" >&2; die "pip wheel of ${PREV_TAG} failed (log above)"; }
shopt -s nullglob
artifacts=("$WORK/dist/auger-${PREV_VERSION}"-*.whl)
shopt -u nullglob
[ "${#artifacts[@]}" -eq 1 ] || die "expected exactly one auger-${PREV_VERSION} wheel in $WORK/dist, found ${#artifacts[@]}"
ARTIFACT="${artifacts[0]}"
( cd "$WORK/dist" && sha256sum ./"$(basename "$ARTIFACT")" > SHA256SUMS )
note "artifact: $ARTIFACT"
note "sha256: $(cut -d' ' -f1 "$WORK/dist/SHA256SUMS")  ($(basename "$ARTIFACT"))"

step 4/6 "fresh venv installs the previous release from the local artifact"
python3 -m venv "$WORK/venv-prev" || die "python3 -m venv failed (venv-prev)"
"$WORK/venv-prev/bin/pip" install --no-index --find-links "$WORK/dist" "$PREV_PIN" \
  >"$WORK/install-prev.log" 2>&1 || {
    sed -n '1,30p' "$WORK/install-prev.log" >&2
    die "pip install $PREV_PIN failed from the local artifact (log above)"
  }
got="$("$WORK/venv-prev/bin/auger" --version)"
[ "$got" = "auger v${PREV_VERSION}" ] || die "previous-release install reported '$got', want 'auger v${PREV_VERSION}'"
note "installed: $PREV_PIN -> $( "$WORK/venv-prev/bin/auger" --version )"

step 5/6 "upgrade the same venv to this checkout and smoke it"
# --no-index keeps the cell offline; --no-build-isolation builds the checkout's wheel with
# the venv's own setuptools instead of trying to fetch an isolated build env.
"$WORK/venv-prev/bin/pip" install --no-index --no-build-isolation --force-reinstall "$ROOT" \
  >"$WORK/upgrade.log" 2>&1 || { sed -n '1,30p' "$WORK/upgrade.log" >&2; die "the upgrade install failed (log above)"; }
got="$("$WORK/venv-prev/bin/auger" --version)"
[ "$got" = "auger v${CUR_VERSION}" ] || die "upgraded install reported '$got', want 'auger v${CUR_VERSION}'"
"$WORK/venv-prev/bin/auger" --help >/dev/null || die "auger --help failed after the upgrade (CLI smoke)"
note "upgraded: auger v${PREV_VERSION} -> $( "$WORK/venv-prev/bin/auger" --version ); auger --help rc=0"

step 6/6 "a second, untouched venv installs the same artifact"
python3 -m venv "$WORK/venv-fresh" || die "python3 -m venv failed (venv-fresh)"
"$WORK/venv-fresh/bin/pip" install --no-index --find-links "$WORK/dist" "$PREV_PIN" \
  >"$WORK/install-fresh.log" 2>&1 || {
    sed -n '1,30p' "$WORK/install-fresh.log" >&2
    die "the fresh-install leg failed (log above)"
  }
got="$("$WORK/venv-fresh/bin/auger" --version)"
[ "$got" = "auger v${PREV_VERSION}" ] || die "fresh install reported '$got', want 'auger v${PREV_VERSION}'"
note "fresh install: $( "$WORK/venv-fresh/bin/auger" --version )"

printf '\nUPGRADE VERIFY: PASS\n'
printf '  previous release: %s installed from local artifact (%s)\n' "$PREV_PIN" "$(basename "$ARTIFACT")"
printf '  upgraded to:      this checkout (auger %s), auger --version + auger --help verified\n' "$CUR_VERSION"
printf '  artifact:         %s\n' "$ARTIFACT"
