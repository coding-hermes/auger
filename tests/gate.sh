#!/usr/bin/env bash
# The repo's real quality gate. Verifies the module parses, lints, and that the
# end-to-end smoke loop passes against a live throwaway DuckBrain namespace.
#
#   bash tests/gate.sh
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

echo "== syntax =="
python3 -m py_compile auger.py || exit 1

echo "== lint =="
if command -v ruff >/dev/null 2>&1; then
  ruff check auger.py || exit 1
else
  echo "ruff not on PATH — lint arm SKIPPED (not passed)"
fi

echo "== pytest =="
# The suite runs against a live DuckBrain namespace it creates and tears down itself, so this
# arm needs no venv beyond the system python3 the other arms already use.
python3 -m pytest tests/test_auger.py -q || exit 1

echo "== end-to-end smoke =="
bash tests/smoke.sh || exit 1

echo "GATE PASS"
