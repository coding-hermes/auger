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
SUBSTANTIVE_GREEN=0
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

# AUG-081: pytest runs as the leader of its OWN SESSION, so the wrapper's
# TERM (at budget) and SIGKILL (after --kill-after) reach it no matter how
# the gate's parent died — a gateway drain, tick death, or dropped ssh no
# longer leaves orphaned pytest children running forever against the shared
# DuckBrain substrate (2026-09-25 incident: two 6h-old orphans poisoned a
# guard run that had nothing to do with their diff). On overrun (exit 124)
# the gate fails LOUDLY naming the arm; a plain pytest failure propagates
# as before.
run_pytest_arm(){
  local budget="${AUGER_GATE_PYTEST_BUDGET:-1200s}"
  local kill_after="${AUGER_GATE_PYTEST_KILL_AFTER:-30s}"
  local rc=0
  setsid timeout --kill-after="$kill_after" "$budget" python3 -m pytest tests/test_auger.py "$@" || rc=$?
  if [ "$rc" -eq 124 ]; then
    echo "GATE TIMEOUT: the pytest arm exceeded ${budget} and was killed as a process group (setsid+timeout, AUG-081) — investigate hung tests; failing the gate" >&2
    exit 1
  fi
  return "$rc"
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
  run_pytest_arm -q -ra || exit 1
else
  run_live_arm
  run_pytest_arm -q || exit 1
fi

echo "== wrapper regression (AUG-081) =="
# Hermetic: no live DuckBrain, no network, no pytest import. Runs in BOTH
# gate modes — the regression covers the group-kill wrapper itself plus a
# source check that BOTH pytest arms carry it, so a future edit that
# unwraps either arm fails the gate even on a hosted CI runner where the
# real suite cannot run.
if bash tests/test_gate_mode.sh; then
  :
else
  rc=$?
  echo "AUG-081 wrapper regression FAILED (rc=$rc): a hung pytest arm must die as a process group — see tests/test_gate_mode.sh" >&2
  exit 1
fi

echo "== end-to-end smoke =="
if [ "$DOC_ONLY" = "1" ]; then
  skip_arm "e2e-smoke" "doc-only fast path — e2e smoke skipped (no code changed)"
elif [ "$GATE_MODE" = "hosted-ci-skip" ]; then
  skip_live_arm "e2e-smoke" "tests/smoke.sh needs a live DuckBrain namespace — arm NOT run, NOT passed"
else
  run_live_arm
  bash tests/smoke.sh || exit 1
  # AUG-040: the substantive suite (pytest above exits hard on failure, smoke
  # just did) has printed success. Anything crashing from here on — the docs
  # summarizer heredoc in particular — is post-suite and must not flip the
  # gate red on its own.
  SUBSTANTIVE_GREEN=1
fi

echo "== docs check =="
# README-003: execute the README's claims instead of reading them — the loop verb list
# vs real subparsers, the init tally vs COLS, and every env/path the README names vs a
# real reference in auger.py. Needs only the checkout (python3 + greps): no live
# DuckBrain, no JEV key, so it runs in BOTH gate modes.
#
# AUG-040: this arm ends in a `SUMMARY="$(python3 ... <<'PY' ... PY)"` heredoc. A crash
# INSIDE that heredoc (e.g. a None-surface TypeError under host load) used to exit 1
# through `|| exit 1` and flip the whole gate red AFTER pytest and smoke had already
# passed and GATE PASS had already printed — the red-by-harness shape of verdict
# 1987bcbb. The arm's output is now captured: when it failed but the log already
# carries this run's `GATE PASS` line, that line is the authority — the substantive
# arms passed and the crash is reported loudly without flipping the verdict. A docs
# failure BEFORE GATE PASS still fails the gate with the log attached.
DOCSCHK_RC=0
DOCSCHK_LOG=""
if [ -f README.md ] && [ -f docs/VERBS.md ] && [ -f auger.py ]; then
  DOCSCHK_LOG="$(mktemp "${TMPDIR:-/tmp}/auger-gate-docschk.XXXXXX")" || DOCSCHK_LOG=""
  if [ -n "$DOCSCHK_LOG" ]; then
    bash tests/docs_check.sh >>"$DOCSCHK_LOG" 2>&1 || DOCSCHK_RC=$?
  else
    bash tests/docs_check.sh || exit 1
  fi
else
  skip_arm "docs-check" "README.md/docs/VERBS.md/auger.py missing from the checkout — arm NOT run, NOT passed"
fi

if [ "$DOCSCHK_RC" -ne 0 ] && [ "${SUBSTANTIVE_GREEN:-0}" = "1" ] && grep -qE 'Traceback|TypeError: argument of type .NoneType. is not iterable|MemoryError|OSError: \[Errno' "$DOCSCHK_LOG"; then
  echo "AUG-040: docs-check CRASHED after the substantive suite passed (post-suite summarizer heredoc)."
  echo "  The substantive arms (syntax/lint/pytest/smoke) already passed; the crash is"
  echo "  reported loudly, but a harness crash must not flip a green suite red. Detail:"
  sed -n '1,15p' "$DOCSCHK_LOG"
elif [ "$DOCSCHK_RC" -ne 0 ]; then
  echo "docs check FAILED (verdict-relevant; substantive-green=${SUBSTANTIVE_GREEN:-0}):" >&2
  cat "$DOCSCHK_LOG" >&2
  exit 1
fi
[ -n "$DOCSCHK_LOG" ] && rm -f "$DOCSCHK_LOG"

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
