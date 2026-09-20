#!/usr/bin/env bash
# End-to-end smoke test for auger.
# Runs the whole loop against a throwaway DuckBrain namespace and ASSERTS on the results —
# a smoke test that only prints output proves nothing. Exit 0 = pass.
#
#   bash tests/smoke.sh [namespace]
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NS="${1:-auger-smoke-$$}"
AUGER="python3 $HERE/auger.py -n $NS"
PASS=0; FAIL=0
ok(){ printf '  \033[32mPASS\033[0m %s\n' "$1"; PASS=$((PASS+1)); }
no(){ printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAIL=$((FAIL+1)); }
chk(){ if [ "$2" = "$3" ]; then ok "$1"; else no "$1 (want [$3] got [$2])"; fi; }
has(){ if grep -q "$2" <<<"$1"; then ok "$3"; else no "$3 (missing: $2)"; fi; }

echo "namespace: $NS"

echo "== init =="
OUT=$($AUGER init 2>&1)
has "$OUT" "declared: 9/9" "all nine tables declared and visible"
if grep -q "WARNING" <<<"$OUT"; then no "init reported a visibility warning"; else ok "no API-visibility warnings"; fi

echo "== start =="
SEED=$(mktemp)
cat > "$SEED" <<'EOF'
One small CLI that watches directories, records what it has seen, and posts an hourly digest.
Runs on one Linux box, survives reboot, and tells me when it has stopped working.
Must not lose anything if it crashes mid-run. Keep it simple - one box, no extra services.
EOF
OUT=$($AUGER start --name smoketest --seed-file "$SEED" 2>&1)
has "$OUT" "seed stored" "seed stored and embedded"

echo "== answer =="
OUT=$($AUGER answer --id D-001 --domain 4.05 --chosen "single SQLite file" \
      --option "single SQLite file" --option "Postgres" \
      --why-not "Postgres needs a service the seed forbids" --confidence 0.82 2>&1)
has "$OUT" "D-001 recorded" "decision recorded"
OUT=$($AUGER answer --id D-002 --domain 4.06 --chosen "staging table" \
      --option "staging table" --option "row locking" \
      --why-not "no concurrent writer" --confidence 0.41 2>&1)
has "$OUT" "D-002 recorded" "second decision recorded"

echo "== status =="
OUT=$($AUGER status 2>&1)
has "$OUT" "decisions 2" "status counts decisions"
has "$OUT" "needs drilling" "status surfaces the thin decision"

echo "== check: retrieval + JEV says ALREADY ANSWERED =="
OUT=$($AUGER check "How should we store the record of files we have seen?" 2>&1)
has "$OUT" "nearest stored rows" "retrieval returned neighbours"
if grep -q "ALREADY ANSWERED" <<<"$OUT"; then ok "JEV confirms the question is answered"; else
  no "JEV verdict (got: $(grep -o 'NOT YET ANSWERED\|ALREADY ANSWERED\|JEV unavailable' <<<"$OUT" | head -1))"; fi
grep -q "ALREADY ANSWERED" <<<"$OUT" || echo "    ^ if JEV keys are all expired this is expected"

echo "== check: a genuinely new question is NOT answered =="
OUT=$($AUGER check "What message queue brokers the watcher to the digest sender?" 2>&1)
if grep -q "NOT YET ANSWERED" <<<"$OUT"; then ok "JEV correctly rejects an unanswered question"; else
  no "new-question verdict"; fi

echo "== dump --config must NOT mutate stored state =="
BEFORE=$($AUGER dump | grep "ACTIVE CONFIGURATION")
$AUGER dump --config D-002=row\ locking >/dev/null 2>&1
AFTER=$($AUGER dump | grep "ACTIVE CONFIGURATION")
chk "hypothetical dump left the record untouched" "$AFTER" "$BEFORE"

echo "== the what-if names the contradiction =="
OUT=$($AUGER dump --config D-002=row\ locking 2>&1)
has "$OUT" "HYPOTHETICAL" "marked hypothetical, not written"
has "$OUT" "CONTRADICTIONS WITH THE RECORD" "flags contradiction with the reason on file"
has "$OUT" "no concurrent writer" "quotes the actual recorded reason"

echo "== toggle writes =="
$AUGER toggle --off D-002-O1 --on D-002-O2 >/dev/null 2>&1
OUT=$($AUGER dump | grep "ACTIVE CONFIGURATION")
has "$OUT" "D-002=row locking" "toggle changed the stored configuration"

rm -f "$SEED"
echo
echo "passed $PASS, failed $FAIL   (namespace left behind: $NS — remove with:"
echo "  rm -rf ~/duckbrain/namespaces/$NS)"
[ "$FAIL" -eq 0 ]
