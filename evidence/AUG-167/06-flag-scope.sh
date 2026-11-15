#!/usr/bin/env bash
# AUG-167 AC4 — `--as-of` is accepted ONLY by the selection verbs.
# Every non-selection verb must refuse it through argparse (rc 2, "unrecognized arguments");
# the positive control proves the flag is not simply unknown everywhere.
set -u
cd "$(dirname "$0")/../.." || exit 1
REPO=$PWD
PY=$REPO/.venv/bin/python
[ -x "$PY" ] || PY=python3
AUG=$REPO/auger.py
OUT=$(mktemp -d)
fail=0

refuse() { # verb, expected rc, args...
  local verb=$1; shift
  "$PY" "$AUG" spec "$verb" "$@" --as-of HEAD >"$OUT/$verb.out" 2>"$OUT/$verb.err"
  local rc=$?
  local hits
  hits=$(grep -c 'unrecognized arguments' "$OUT/$verb.err")
  printf '  %-9s rc=%s unrecognized=%s stdout_bytes=%s\n' "$verb" "$rc" "$hits" "$(wc -c < "$OUT/$verb.out")"
  [ "$rc" = 2 ] && [ "$hits" = 1 ] && [ "$(wc -c < "$OUT/$verb.out")" = 0 ] || fail=1
}

accept() { # verb, args...
  local verb=$1; shift
  "$PY" "$AUG" spec "$verb" "$@" --as-of HEAD >"$OUT/ok-$verb.out" 2>"$OUT/ok-$verb.err"
  local rc=$?
  printf '  %-9s rc=%s stdout_bytes=%s (positive control)\n' "$verb" "$rc" "$(wc -c < "$OUT/ok-$verb.out")"
  [ "$rc" = 0 ] && [ -s "$OUT/ok-$verb.out" ] || fail=1
}

echo "== non-selection verbs must refuse --as-of =="
refuse validate specs
refuse check specs
refuse bundle specs AUG-REQ-014
refuse render specs --out "$OUT/views"
echo "== selection verbs must accept --as-of =="
accept list specs
accept assemble specs --id SPEC-VERBS-SELECTION --format json

rm -rf "$OUT"
if [ "$fail" = 0 ]; then echo FLAG_SCOPE_OK; exit 0; fi
echo FLAG_SCOPE_FAILED
exit 1
