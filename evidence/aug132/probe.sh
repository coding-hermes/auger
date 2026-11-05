#!/usr/bin/env bash
# Pre-change (RED) transcript for AUG-132: `spec validate` on a corpus whose
# verification block names tests/does-not-exist.py. Run against a clean checkout of
# the BASE commit (pre-fix code), with the new fixture copied in.
set -uo pipefail
tree="$1"
out="$2"
label="${3:-tree under test}"
root="$tree/tests/fixtures/xml/invalid/stale-test-obligation"
{
  printf '# tree: %s (%s)\n' "$tree" "$label"
  printf '# fixture: %s\n' "$root"
  printf '# ---\n'
} >"$out"
cd "$tree" || exit 8
for verb in validate check; do
  printf '$ python3 auger.py spec %s tests/fixtures/xml/invalid/stale-test-obligation\n' "$verb" >>"$out"
  python3 auger.py spec "$verb" tests/fixtures/xml/invalid/stale-test-obligation >>"$out" 2>&1
  printf 'rc=%s\n\n' "$?" >>"$out"
done
cat "$out"
