#!/usr/bin/env bash
# AUG-204 AC3 — full-corpus register+run wrapper over the AUG-176 recall battery.
#
# Usage: scripts/auger-chd-battery.sh [--check]
#
#   --check   hermetic battery-readiness pass (scripts/auger_chd_battery.py --check):
#             the committed full corpus loads through auger's own loader, both manifests
#             reconcile, and every AUG-176 battery target resolves. NO substrate contact.
#   default   FULL-corpus register+run against the LIVE substrate: registers every
#             active block of corpora/coding-hermes-director as recall rows
#             (scripts/recall_battery.py register) then runs the AUG-176 queries
#             (run), and judges the report (scripts/auger_chd_battery.py --verdict).
#             Exits 0 ONLY on a PASS verdict: every query measured, zero register
#             failures, hit rate not below the AUG-176 distilled baseline (7/12).
#
# Environment:
#   DUCKBRAIN_URL + DUCKBRAIN_API_KEY  required for the default (live) mode; run mode
#                                      refuses fail-closed without them.
#   AUGER_CORPORA_ROOT                 corpora root override (default <repo>/corpora).
#   PYTHON                             interpreter (default python3; put the repo venv
#                                      first on PATH for the lxml/pytest-capable one).
#
# A full-corpus register is a ~30-minute live-substrate operation (one row per block,
# embedded on write) and appends to the namespace on every run — this wrapper is a
# deliberate, explicit action, NOT part of the gate.

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$HERE")"
CORPORA="${AUGER_CORPORA_ROOT:-$REPO_ROOT/corpora}"
CHD="$CORPORA/coding-hermes-director"
PY="${PYTHON:-python3}"
PREFLIGHT="$HERE/auger_chd_battery.py"
RECALL_BATTERY="$REPO_ROOT/scripts/recall_battery.py"
PROJECT="coding-hermes-director"

die() {
  echo "auger-chd-battery: $*" >&2
  exit 1
}

[ -f "$CHD/manifest.json" ] || die "corpus manifest missing: $CHD/manifest.json — generate the full corpus first (scripts/ingest_markdown_specs.py $PROJECT)"
[ -f "$PREFLIGHT" ] || die "preflight helper missing: $PREFLIGHT"
[ -f "$RECALL_BATTERY" ] || die "AUG-176 battery missing: $RECALL_BATTERY"

mode="${1:---run}"
if [ "$mode" = "--check" ]; then
  exec "$PY" "$PREFLIGHT" --check --corpus "$CHD"
fi
[ "$mode" = "--run" ] || [ "$mode" = "" ] || die "usage: $0 [--check|--run]"

# Fail closed BEFORE any substrate contact: the live mode is useless without credentials.
: "${DUCKBRAIN_URL:?auger-chd-battery: DUCKBRAIN_URL not set — the live battery runs fail-closed; set DUCKBRAIN_URL and DUCKBRAIN_API_KEY}"
: "${DUCKBRAIN_API_KEY:?auger-chd-battery: DUCKBRAIN_API_KEY not set — the live battery runs fail-closed; set DUCKBRAIN_URL and DUCKBRAIN_API_KEY}"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/auger-chd-battery.XXXXXX")"
REG_REPORT="$WORK/register.json"
RUN_REPORT="$WORK/run.json"
RUN_LOG="$WORK/run.log"
trap 'echo "battery artifacts: $WORK (register/run reports, run log)" >&2' EXIT

echo "== AUG-204 full-corpus recall battery ($PROJECT) =="
echo "== corpus preflight =="
"$PY" "$PREFLIGHT" --check --corpus "$CHD" || die "corpus preflight failed — fix the corpus before running the battery"

echo "== register (full corpus; appends one row per block, ~30 min) =="
"$PY" "$RECALL_BATTERY" register --project "$PROJECT" >"$REG_REPORT" 2>>"$RUN_LOG" || die "register failed — see $RUN_LOG"

echo "== run (AUG-176 queries over the full corpus) =="
"$PY" "$RECALL_BATTERY" run --project "$PROJECT" --out "$RUN_REPORT" >>"$RUN_LOG" 2>&1 || die "battery run failed — see $RUN_LOG"

grep -E "^[a-z-]+: [0-9]+/[0-9]+ hit rate" "$RUN_LOG" | tail -1 || true

"$PY" "$PREFLIGHT" --verdict "$RUN_REPORT" --register-report "$REG_REPORT" --run-log "$RUN_LOG"
rc=$?
exit "$rc"
