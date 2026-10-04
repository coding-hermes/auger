#!/usr/bin/env bash
# The repo's real quality gate. Verifies the module parses, lints, and that the
# end-to-end smoke loop passes against a live throwaway DuckBrain namespace.
#
#   bash tests/gate.sh
#
# CI: `AUGER_CI=1` (set by .github/workflows/ci.yml) marks a GitHub-hosted runner with NO live
# DuckBrain and NO JEV key. It is accepted only when GITHUB_ACTIONS or CI also identifies a real
# runner. On a development host, a leaked AUGER_CI=1 fails before syntax/tests instead of silently
# downgrading the gate. The arms that need live services then SKIP OUT LOUD — named here and
# repeated in the closing summary — instead of failing the job. A green CI run therefore means
# "syntax + lint + the tests that need no live API passed"; it never means the live arms passed.
# Nothing is dropped silently: every skip prints a `SKIPPED [<arm>]: <why>` line.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

runner_marker_present(){
  if [ "${GITHUB_ACTIONS:-}" = "true" ]; then
    return 0
  fi
  case "${CI:-}" in
    1|true|TRUE|yes|YES) return 0 ;;
  esac
  return 1
}

select_gate_mode(){
  case "${AUGER_CI:-0}" in
    1)
      if runner_marker_present; then
        printf '%s\n' "hosted-ci-skip"
      else
        echo "ERROR: refusing AUGER_CI=1 downgrade on a non-runner host; require GITHUB_ACTIONS=true or CI=1/true." >&2
        return 2
      fi
      ;;
    0|'') printf '%s\n' "live" ;;
    *)
      echo "ERROR: AUGER_CI must be 0 or 1 (got ${AUGER_CI})" >&2
      return 2
      ;;
  esac
}

GATE_MODE="$(select_gate_mode)" || exit $?
if [ "${GATE_MODE_ONLY:-0}" = "1" ]; then
  echo "gate mode: $GATE_MODE"
  exit 0
fi

# INT-GITREINS-20260927-01: doc-only fast path.
# When the latest commit touches ONLY documentation files (no .py, no tests/),
# skip the expensive pytest + e2e-smoke arms. The docs-check arm still runs.
# Override: GATE_FULL=1 forces the full gate regardless of diff.
DOC_ONLY=0
if [ "${GATE_FULL:-0}" != "1" ]; then
  # Detect changed files: prefer staged (pre-commit hook), fall back to HEAD~1..HEAD (post-commit / gitreins)
  CHANGED=""
  if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    STAGED="$(git diff --cached --name-only 2>/dev/null)"
    if [ -n "$STAGED" ]; then
      CHANGED="$STAGED"
    elif git rev-parse HEAD~1 >/dev/null 2>&1; then
      CHANGED="$(git diff --name-only HEAD~1 HEAD 2>/dev/null)"
    fi
  fi
  if [ -n "$CHANGED" ]; then
    # If ANY changed file is NOT a doc file, run the full gate
    NON_DOC="$(echo "$CHANGED" | grep -vE '^(README\.md|CHANGELOG\.md|docs/|.*\.md$|LICENSE|\.gitignore)' || true)"
    if [ -z "$NON_DOC" ]; then
      DOC_ONLY=1
    fi
  fi
fi

if [ "$DOC_ONLY" = "1" ]; then
  echo "== gate mode: DOC-ONLY fast path (skipping pytest + e2e smoke) =="
else
  echo "== gate mode: FULL gate =="
fi

SKIPPED=""
LIVE_ARMS_RAN=0
LIVE_ARMS_SKIPPED=0
LIVE_SKIPPED=""
skip_arm(){ SKIPPED="${SKIPPED}${SKIPPED:+, }$1"; echo "  SKIPPED [$1]: $2"; }
run_live_arm(){ LIVE_ARMS_RAN=$((LIVE_ARMS_RAN + 1)); }
skip_live_arm(){
  LIVE_ARMS_SKIPPED=$((LIVE_ARMS_SKIPPED + 1))
  LIVE_SKIPPED="${LIVE_SKIPPED}${LIVE_SKIPPED:+, }$1"
  skip_arm "$1" "$2"
}

echo "== syntax =="
python3 -m py_compile auger.py || exit 1

echo "== lint =="
if command -v ruff >/dev/null 2>&1; then
  ruff check auger.py || exit 1
else
  skip_arm "lint" "ruff not on PATH — lint arm SKIPPED (not passed)"
fi

echo "== pytest =="
if [ "$DOC_ONLY" = "1" ]; then
  skip_arm "pytest" "doc-only fast path — pytest skipped (no code changed)"
elif ! python3 -c 'import pytest' 2>/dev/null; then
  skip_arm "pytest" "pytest not importable by python3 (pip install pytest) — suite arm NOT run, NOT passed"
elif [ "$GATE_MODE" = "hosted-ci-skip" ]; then
  skip_live_arm "pytest-live" "cases needing the live DuckBrain/JEV APIs SKIP — reasons in the summary"
  python3 -m pytest tests/test_auger.py -q -ra || exit 1
else
  run_live_arm
  python3 -m pytest tests/test_auger.py -q || exit 1
fi

echo "== end-to-end smoke =="
if [ "$DOC_ONLY" = "1" ]; then
  skip_arm "e2e-smoke" "doc-only fast path — e2e smoke skipped (no code changed)"
elif [ "$GATE_MODE" = "hosted-ci-skip" ]; then
  skip_live_arm "e2e-smoke" "tests/smoke.sh needs a live DuckBrain namespace — arm NOT run, NOT passed"
else
  run_live_arm
  bash tests/smoke.sh || exit 1
fi

echo "== docs check =="
# README-003: execute the README's claims instead of reading them — the loop verb list
# vs real subparsers, the init tally vs COLS, and every env/path the README names vs a
# real reference in auger.py. Needs only the checkout (python3 + greps): no live
# DuckBrain, no JEV key, so it runs in BOTH gate modes.
if [ -f README.md ] && [ -f docs/VERBS.md ] && [ -f auger.py ]; then
  bash tests/docs_check.sh || exit 1
else
  skip_arm "docs-check" "README.md/docs/VERBS.md/auger.py missing from the checkout — arm NOT run, NOT passed"
fi

echo "LIVE ARMS: ran $LIVE_ARMS_RAN; skipped $LIVE_ARMS_SKIPPED"
if [ -n "$LIVE_SKIPPED" ]; then
  echo "  live arms SKIPPED (not passed): $LIVE_SKIPPED"
fi

echo "GATE PASS"
if [ -n "$SKIPPED" ]; then
  echo "arms SKIPPED (not passed): $SKIPPED"
  if [ "$GATE_MODE" = "hosted-ci-skip" ]; then
    echo "  AUGER_CI=1 accepted on a runner (GITHUB_ACTIONS/CI marker present): the live DuckBrain/JEV arms cannot run here. See tests/gate.sh."
  fi
fi
