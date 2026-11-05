#!/usr/bin/env bash
# Agreement matrix: run BOTH verbs over every fixture corpus root and print verb/rc.
# Used as the AUG-134 red/green transcript (pre-fix vs post-fix tree).
set -uo pipefail
tree="$1"
out="$2"
cd "$tree" || exit 8
{
  printf '# tree: %s\n' "$tree"
  printf '# corpus root                                     validate  check   agree\n'
  printf '# -------------------------------------------------------------------------\n'
} >"$out"
disagreements=0
for root in \
  tests/fixtures/xml/specs \
  tests/fixtures/xml/verification \
  tests/fixtures/xml/expected \
  tests/fixtures/xml/invalid \
  tests/fixtures/xml/invalid/duplicate-id \
  tests/fixtures/xml/invalid/external-entity \
  tests/fixtures/xml/invalid/include-cycle \
  tests/fixtures/xml/invalid/invalid-verification-method \
  tests/fixtures/xml/invalid/missing-verification-method \
  tests/fixtures/xml/invalid/reference-cycle \
  tests/fixtures/xml/invalid/schema-version \
  tests/fixtures/xml/invalid/stale-test-obligation \
  tests/fixtures/xml/invalid/unresolved-reference; do
  python3 auger.py spec validate "$root" >/dev/null 2>&1
  vrc=$?
  python3 auger.py spec check "$root" >/dev/null 2>&1
  crc=$?
  agree=yes
  if [ "$vrc" != "$crc" ]; then
    agree="NO"
    disagreements=$((disagreements + 1))
  fi
  printf '  %-48s %-8s %-7s %s\n' "$root" "$vrc" "$crc" "$agree" >>"$out"
done
printf '# -------------------------------------------------------------------------\n' >>"$out"
printf 'disagreements=%s\n' "$disagreements" >>"$out"
cat "$out"
