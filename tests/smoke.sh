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

# ---------------------------------------------------------------- namespace hygiene (AUG-020)
# DuckBrain's namespace list is a SHARED surface: the production `auger` namespace and live
# dogfood/judge namespaces sit beside this script's throwaways. Two rules, both prefix-gated
# to auger-smoke-*/auger-eval-* so nothing else is ever touched:
#   * BEFORE creating its own namespace, sweep PREVIOUS throwaways older than a day (or
#     listed with no directory — the observed leak shape). A live sibling run always has a
#     fresh directory, so an in-flight judge or another smoke run is never a sweep target.
#   * AT EXIT, remove THIS run's namespace. Teardown is best-effort: one line either way,
#     429 retried ONCE inline by the transport, and a failure never flips the verdict.
ns_helper(){  # ns_helper sweep | ns_helper teardown <ns> — all DuckBrain talk via auger's transport
  python3 - "$HERE" "$@" <<'PY'
import os, shutil, sys, time
sys.path.insert(0, sys.argv[1])
import auger

THROWAWAY = ("auger-smoke-", "auger-eval-")
ROOT = os.path.join(os.path.expanduser("~"), "duckbrain", "namespaces")

def listed():
    """The registry's names, or None when DuckBrain cannot be asked right now.

    The transport's own 429 handling with a ONE-retry budget, then give up: hygiene
    must never hang or fail a suite over a saturated limiter.
    """
    try:
        st, body, _ = auger.db("/api/namespaces", retries=1)
    except SystemExit:
        return None
    if st != 200 or not isinstance(body, dict):
        return None
    return [n.get("name") for n in body.get("namespaces", [])
            if isinstance(n, dict) and isinstance(n.get("name"), str)]

def drop(ns):
    """Remove one throwaway namespace over both paths. Returns (gone, why).

    `attempts` counts the caller's own invocations of this function: the 429 contract
    is "retry ONCE", so the caller re-enters after a 429 and the SECOND attempt's
    first DELETE is the third HTTP try overall — and the last.
    """
    try:
        st, _body = auger.delete_namespace(ns, retries=1)
    except SystemExit as exc:
        return False, f"no DuckBrain token ({exc})"
    shutil.rmtree(os.path.join(ROOT, ns), ignore_errors=True)
    if os.path.exists(os.path.join(ROOT, ns)):
        return False, "its directory could not be removed"
    if st not in (200, 404):
        return False, f"DELETE returned HTTP {st}"
    return True, ""

mode = sys.argv[2]
if mode == "sweep":
    names = listed()
    if names is None:
        print("namespace sweep skipped: DuckBrain list unavailable (unreachable or rate-limited)")
    else:
        cutoff = time.time() - 24 * 3600
        stale = []
        for n in names:
            if not n.startswith(THROWAWAY):
                continue
            d = os.path.join(ROOT, n)
            # Stale = directory gone (the observed leak shape: the registry row survives
            # while the directory does not — no live run looks like that) or older than a day.
            if not os.path.isdir(d) or os.path.getmtime(d) < cutoff:
                stale.append(n)
        if not stale:
            print("namespace sweep: no stale auger-smoke-*/auger-eval-* older than a day")
        else:
            gone = [n for n in stale if drop(n)[0]]
            stuck = sorted(n for n in stale if n not in gone)
            if stuck:
                print(f"namespace sweep: removed {len(gone)}, gave up on {len(stuck)}: {' '.join(stuck)}")
            else:
                print(f"namespace sweep: removed {len(gone)} stale throwaway(s)")
elif mode == "teardown":
    ns = sys.argv[3]
    if not ns.startswith(THROWAWAY):
        print(f"teardown skipped: {ns} lacks the auger-smoke-/auger-eval- prefix")
    elif listed() is not None and ns not in listed() and not os.path.isdir(os.path.join(ROOT, ns)):
        print(f"teardown skipped: {ns} was never created (nothing to remove)")
    else:
        ok, why = drop(ns)
        if not ok and "HTTP 429" in why:
            ok, why = drop(ns)   # the ONE retry the 429 contract allows
        print(f"namespace {ns} removed" if ok else f"teardown skipped: {why}")
else:
    print(f"teardown skipped: unknown hygiene mode {mode!r}")
sys.exit(0)
PY
}
TEARDONE=0
cleanup_ns(){
  [ "$TEARDONE" = 1 ] && return 0
  TEARDONE=1
  ns_helper teardown "$NS"
}
trap cleanup_ns EXIT
ns_helper sweep

echo "== init =="
OUT=$($AUGER init 2>&1)
has "$OUT" "declared: 13/13" "all thirteen tables declared and visible"
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
cleanup_ns   # explicit final teardown of THIS run's namespace (also wired to the EXIT trap)
echo "passed $PASS, failed $FAIL"
[ "$FAIL" -eq 0 ]
