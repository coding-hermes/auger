#!/usr/bin/env bash
# The repo's real quality gate. Verifies the module parses, lints, and that the
# end-to-end smoke loop passes against a live throwaway DuckBrain namespace.
#
#   bash tests/gate.sh
#
# CI: `AUGER_CI=1` (set by .github/workflows/ci.yml) marks a host with NO live DuckBrain and NO
# JEV key. The arms that need them then SKIP OUT LOUD — named here and repeated in the closing
# summary — instead of failing the job. A green CI run therefore means "syntax + lint + the
# tests that need no live API passed"; it never means the live arms passed. Nothing is dropped
# silently: every skip prints a `SKIPPED [<arm>]: <why>` line.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

CI="${AUGER_CI:-0}"
SKIPPED=""
skip_arm(){ SKIPPED="${SKIPPED}${SKIPPED:+, }$1"; echo "  SKIPPED [$1]: $2"; }

echo "== syntax =="
python3 -m py_compile auger.py || exit 1

echo "== lint =="
if command -v ruff >/dev/null 2>&1; then
  ruff check auger.py || exit 1
else
  skip_arm "lint" "ruff not on PATH — lint arm SKIPPED (not passed)"
fi

echo "== pytest =="
# The suite runs against a live DuckBrain namespace it creates and tears down itself, so this
# arm needs no venv beyond the system python3 the other arms already use.
# Cases needing DuckBrain/JEV skip themselves loudly and name the reason (`-ra` prints them in
# CI); the cases that need no live API still run, and still fail the gate when they break.
if [ "$CI" = "1" ]; then
  echo "  AUGER_CI=1: cases needing the live DuckBrain/JEV APIs SKIP — reasons in the summary"
  python3 -m pytest tests/test_auger.py -q -ra || exit 1
else
  python3 -m pytest tests/test_auger.py -q || exit 1
fi

echo "== end-to-end smoke =="
if [ "$CI" = "1" ]; then
  skip_arm "e2e-smoke" "tests/smoke.sh needs a live DuckBrain namespace — arm NOT run, NOT passed"
else
  bash tests/smoke.sh || exit 1
fi

echo "GATE PASS"
if [ -n "$SKIPPED" ]; then
  echo "arms SKIPPED (not passed): $SKIPPED"
  if [ "$CI" = "1" ]; then
    echo "  AUGER_CI=1 (GitHub runner): the live DuckBrain/JEV arms cannot run here. See tests/gate.sh."
  fi
fi
