#!/usr/bin/env bash
# SG8 falsifiers (AUG-119 / AUG-124). Six probes, none of them a happy path:
#
#   1  unapproved plan            -> export refuses, both canonical JSONL files byte-identical
#   2  stale approval             -> refuses, names the approved AND the current digest
#   3  same approved plan twice   -> idempotent: no duplicate row, unchanged bytes
#   4  partial failure            -> first row's outcome recorded, failure named, resume completes
#   5  test path not tracked      -> judge-packet refuses, names the path
#   6  trace ref out of scope     -> judge-packet refuses, names the id
#
# Every probe prints its expected and actual exit code plus the sha256 it compared. The script
# exits non-zero if ANY expectation is unmet. Hermetic: a throwaway board, ledger, corpus and git
# repository under the scratch directory; no network, no DuckBrain, no judge.
set -uo pipefail

WORK=/home/kara/proj-wt-auger-xml-hardening
PY="$WORK/.venv/bin/python"
TMP="$(mktemp -d /home/kara/.hermes/cache/scratch/sg8-falsifiers-XXXXXX)"
PLAN=SG8-FALS-PLAN
TASK_A=SG8-TASK-1
TASK_B=SG8-TASK-2
FAILURES=0

auger() { "$PY" "$WORK/auger.py" "$@"; }
say() { printf '\n=== %s ===\n' "$1"; }
check() { # label expected actual
  if [ "$2" != "$3" ]; then
    printf 'FALSIFIER-FAIL %-32s expected rc=%s got rc=%s\n' "$1" "$2" "$3"
    FAILURES=$((FAILURES + 1))
  else
    printf 'FALSIFIER-OK   %-32s rc=%s\n' "$1" "$3"
  fi
}
note() { printf '%-34s %s\n' "$1" "$2"; }
sha() { sha256sum "$1" | cut -d' ' -f1; }
rowcount() { jq -s --arg id "$2" 'map(select(.id == $id)) | length' "$1" 2>/dev/null || printf '0\n'; }

corpus() { # $1 = directory
  mkdir -p "$1"
  cat > "$1/plan-a.xml" <<'XML'
<spec xmlns="urn:auger:spec:1" id="SG8-FALS-A" version="1.0.0" schema-version="1.0">
  <requirement id="SG8-REQ-1" kind="requirement" status="active" version="1.0.0"
               applies-to="auger.plan" verification="test" verifies="SG8-VER-1">
    A plan is exported as one canonical board row per task.
  </requirement>
  <verification id="SG8-VER-1" kind="verification" status="active" version="1.0.0"
                applies-to="auger.plan" method="test" type="test" runner="pytest"
                path="tests/test_xml_specs.py" selector="test_gate_runs_these_tests"
                assertions="the exported rows name their scope" />
  <acceptance id="SG8-AC-1" kind="acceptance" status="active" version="1.0.0"
              applies-to="auger.plan" verification="test" verifies="SG8-REQ-1">
    The canonical row records the exact acceptance criteria.
  </acceptance>
  <requirement id="SG8-REQ-9" kind="requirement" status="active" version="1.0.0"
               verification="inspection">A later wave owns the migration path.</requirement>
  <task id="SG8-TASK-1" kind="task" status="active" version="1.0.0"
        applies-to="auger.plan" verification="test" plan="SG8-FALS-PLAN" plan-version="1.0.0"
        in-scope="SG8-REQ-1" acceptance="SG8-AC-1" out-of-scope="SG8-REQ-9">
    Export the first approved task.
    <exclusion ref="SG8-REQ-9" reason="owned by a later wave"/>
  </task>
</spec>
XML
  cat > "$1/plan-b.xml" <<'XML'
<spec xmlns="urn:auger:spec:1" id="SG8-FALS-B" version="1.0.0" schema-version="1.0">
  <requirement id="SG8-REQ-2" kind="requirement" status="active" version="1.0.0"
               applies-to="auger.plan" verification="test" verifies="SG8-VER-2">
    The second task is exported after the first.
  </requirement>
  <verification id="SG8-VER-2" kind="verification" status="active" version="1.0.0"
                applies-to="auger.plan" method="test" type="test" runner="pytest"
                path="tests/test_xml_specs.py" selector="test_gate_runs_these_tests"
                assertions="the read-back equals the approved packet" />
  <task id="SG8-TASK-2" kind="task" status="active" version="1.0.0"
        applies-to="auger.plan" verification="test" plan="SG8-FALS-PLAN" plan-version="1.0.0"
        in-scope="SG8-REQ-2" out-of-scope="SG8-REQ-9" depends-on="SG8-TASK-1">
    Export the second approved task after the first.
    <exclusion ref="SG8-REQ-9" reason="owned by a later wave"/>
  </task>
</spec>
XML
}

plan_digest_of() { # $1 = corpus dir, $2 = board dir, $3 = ledger dir  -> prints digest
  auger spec plan-draft "$1" --plan "$PLAN" | jq -r .digest
}

approve() { # $1 corpus  $2 ledger  $3 digest
  auger spec plan-approve "$1" --plan "$PLAN" --approve --digest "$3" \
    --approver owner --ledger "$2" --at 2026-11-06T00:00:00+00:00 >/dev/null 2>&1
}

export_plan() { # $1 corpus  $2 board  $3 ledger  [extra args...]
  local root="$1" board="$2" ledger="$3"
  shift 3
  auger spec plan-export "$root" --plan "$PLAN" --board "$board" --ledger "$ledger" "$@"
}



printf 'SG8 falsifiers — worktree %s\n' "$WORK"
printf 'scratch root %s\n' "$TMP"
corpus "$TMP/corpus"
boardctl init -C "$TMP/board" --project auger --namespace auger >/dev/null 2>&1 || {
  echo "boardctl init failed — the falsifiers cannot run" >&2
  exit 2
}
BOARD_FILES=$(find "$TMP/board" -name tasks.jsonl | head -1)
EVENTS=$(dirname "$BOARD_FILES")/events.jsonl
mkdir -p "$TMP/ledger"

# ------------------------------------------------------------------ 1. unapproved plan
say "1  unapproved plan: no record -> refusal, board byte-identical"
DIGEST=$(plan_digest_of "$TMP/corpus" "$TMP/board" "$TMP/ledger")
note "plan digest" "$DIGEST"
BEFORE_T=$(sha "$BOARD_FILES"); BEFORE_E=$(sha "$EVENTS")
OUT=$(export_plan "$TMP/corpus" "$TMP/board" "$TMP/ledger" 2>"$TMP/1.err")
RC=$?
check "unapproved export" 2 "$RC"
grep -q "unapproved-plan-digest" "$TMP/1.err" && note "refusal code" "unapproved-plan-digest" \
  || { echo "FALSIFIER-FAIL 1: wrong refusal: $(cat "$TMP/1.err")"; FAILURES=$((FAILURES + 1)); }
AFTER_T=$(sha "$BOARD_FILES"); AFTER_E=$(sha "$EVENTS")
if [ "$BEFORE_T" = "$AFTER_T" ] && [ "$BEFORE_E" = "$AFTER_E" ]; then
  note "tasks.jsonl sha256" "$BEFORE_T -> $AFTER_T (identical)"
  note "events.jsonl sha256" "$BEFORE_E -> $AFTER_E (identical)"
else
  echo "FALSIFIER-FAIL 1: a refused export changed a canonical file"; FAILURES=$((FAILURES + 1))
fi
[ -z "$OUT" ] && note "stdout on refusal" "empty" || { echo "FALSIFIER-FAIL 1: printed a result"; FAILURES=$((FAILURES + 1)); }

# ------------------------------------------------------------------ 2. stale approval
say "2  stale approval: plan edited after approval -> refusal names BOTH digests"
approve "$TMP/corpus" "$TMP/ledger" "$DIGEST"
sed -i 's/Export the first approved task\./Export the first approved task, edited after review./' \
  "$TMP/corpus/plan-a.xml"
NEW_DIGEST=$(plan_digest_of "$TMP/corpus" "$TMP/board" "$TMP/ledger")
note "approved digest" "$DIGEST"
note "current  digest" "$NEW_DIGEST"
export_plan "$TMP/corpus" "$TMP/board" "$TMP/ledger" >/dev/null 2>"$TMP/2.err"
check "stale export" 2 "$?"
if grep -q "stale-plan-digest" "$TMP/2.err" && grep -q "$DIGEST" "$TMP/2.err" && grep -q "$NEW_DIGEST" "$TMP/2.err"; then
  note "refusal names" "stale-plan-digest + both digests"
else
  echo "FALSIFIER-FAIL 2: refusal did not name both digests: $(cat "$TMP/2.err")"; FAILURES=$((FAILURES + 1))
fi
auger spec plan-approve "$TMP/corpus" --plan "$PLAN" --approve --digest "$DIGEST" \
  --approver owner --ledger "$TMP/ledger" >/dev/null 2>"$TMP/2b.err"
check "approve an old digest" 2 "$?"

# ------------------------------------------------------------------ 3. idempotent double export
say "3  same approved plan twice: idempotent (no duplicate row, identical bytes)"
approve "$TMP/corpus" "$TMP/ledger" "$NEW_DIGEST"
export_plan "$TMP/corpus" "$TMP/board" "$TMP/ledger" >"$TMP/3a.json" 2>"$TMP/3a.err"
check "first export" 0 "$?"
note "first status" "$(jq -r .status "$TMP/3a.json")"
FIRST_T=$(sha "$BOARD_FILES"); FIRST_E=$(sha "$EVENTS")
export_plan "$TMP/corpus" "$TMP/board" "$TMP/ledger" >"$TMP/3b.json" 2>"$TMP/3b.err"
check "second export" 0 "$?"
note "second status" "$(jq -r .status "$TMP/3b.json")"
SECOND_T=$(sha "$BOARD_FILES"); SECOND_E=$(sha "$EVENTS")
note "tasks.jsonl sha256" "$FIRST_T -> $SECOND_T"
RA=$(rowcount "$BOARD_FILES" "$TASK_A"); RB=$(rowcount "$BOARD_FILES" "$TASK_B")
note "rows for TASK-1/TASK-2" "$RA / $RB"
if [ "$FIRST_T" = "$SECOND_T" ] && [ "$FIRST_E" = "$SECOND_E" ] && [ "$RA" = 1 ] && [ "$RB" = 1 ] \
   && [ "$(jq -r .status "$TMP/3b.json")" = unchanged ]; then
  note "verdict" "idempotent: unchanged bytes, one row per id"
else
  echo "FALSIFIER-FAIL 3: the second export was not idempotent"; FAILURES=$((FAILURES + 1))
fi

# ------------------------------------------------------------------ 4. partial failure
say "4  partial failure: second row fails -> status partial, first outcome kept, resume completes"
mkdir -p "$TMP/board2" "$TMP/ledger2"
boardctl init -C "$TMP/board2" --project auger --namespace auger >/dev/null 2>&1
BOARD2=$(find "$TMP/board2" -name tasks.jsonl | head -1)
approve "$TMP/corpus" "$TMP/ledger2" "$NEW_DIGEST"
cat > "$TMP/picky-boardctl" <<EOF
#!/bin/sh
case "\$*" in *create*$TASK_B*) echo "simulated boardctl failure for $TASK_B" >&2; exit 1;; esac
exec $(command -v boardctl) "\$@"
EOF
chmod +x "$TMP/picky-boardctl"
export_plan "$TMP/corpus" "$TMP/board2" "$TMP/ledger2" \
  --boardctl "$TMP/picky-boardctl" --out "$TMP/4-recovery.json" >"$TMP/4.json" 2>"$TMP/4.err"
check "partial export" 1 "$?"
note "status" "$(jq -r .status "$TMP/4.json")"
note "completed rows" "$(jq -c .completed "$TMP/4.json")"
note "failed row" "$(jq -c .failed "$TMP/4.json")"
note "recovery record" "$(jq -r .status "$TMP/4-recovery.json") completed=$(jq -c .completed "$TMP/4-recovery.json")"
R2A=$(rowcount "$BOARD2" "$TASK_A"); R2B=$(rowcount "$BOARD2" "$TASK_B")
note "rows landed (TASK-1/TASK-2)" "$R2A / $R2B"
if [ "$(jq -r .status "$TMP/4.json")" = partial ] && [ "$R2A" = 1 ] && [ "$R2B" = 0 ] \
   && [ "$(jq -r .failed.task "$TMP/4.json")" = "$TASK_B" ]; then
  note "verdict" "partial reported as partial, first row recorded, failure named"
else
  echo "FALSIFIER-FAIL 4: a partial run was not reported as partial"; FAILURES=$((FAILURES + 1))
fi
export_plan "$TMP/corpus" "$TMP/board2" "$TMP/ledger2" >"$TMP/4b.json" 2>"$TMP/4b.err"
check "resume export" 0 "$?"
R2A=$(rowcount "$BOARD2" "$TASK_A"); R2B=$(rowcount "$BOARD2" "$TASK_B")
note "after resume (TASK-1/TASK-2)" "$R2A / $R2B status=$(jq -r .status "$TMP/4b.json")"
if [ "$R2A" = 1 ] && [ "$R2B" = 1 ]; then
  note "verdict" "resume completed the plan without duplicating the landed row"
else
  echo "FALSIFIER-FAIL 4b: the resume did not complete the plan"; FAILURES=$((FAILURES + 1))
fi

# ------------------------------------------------------------------ 5. untracked test path
say "5  test_refs path not tracked at HEAD -> judge-packet refuses, names the path"
CONSUMER="$TMP/consumer"
mkdir -p "$CONSUMER/tests"
printf 'def test_good():\n    assert True\n' > "$CONSUMER/tests/test_good.py"
printf 'def test_untracked():\n    assert True\n' > "$CONSUMER/tests/test_untracked.py"
git -C "$CONSUMER" -c user.email=auger@example.invalid -c user.name=auger init -q -b main
git -C "$CONSUMER" add tests/test_good.py
git -C "$CONSUMER" -c user.email=auger@example.invalid -c user.name=auger commit -q -m "tracked test"
corpus "$TMP/corpus-untracked"
sed -i 's#tests/test_xml_specs.py#tests/test_untracked.py#; s#test_gate_runs_these_tests#test_untracked#g' \
  "$TMP/corpus-untracked/plan-a.xml" "$TMP/corpus-untracked/plan-b.xml"
mkdir -p "$TMP/board5" "$TMP/ledger5"
boardctl init -C "$TMP/board5" --project auger --namespace auger >/dev/null 2>&1
D5=$(plan_digest_of "$TMP/corpus-untracked" "$TMP/board5" "$TMP/ledger5")
approve "$TMP/corpus-untracked" "$TMP/ledger5" "$D5"
export_plan "$TMP/corpus-untracked" "$TMP/board5" "$TMP/ledger5" >/dev/null 2>&1
auger spec judge-packet "$TMP/corpus-untracked" --plan "$PLAN" --row "$TASK_A" \
  --board "$TMP/board5" --ledger "$TMP/ledger5" --repo-root "$CONSUMER" \
  --out "$TMP/5-packet.json" >/dev/null 2>"$TMP/5.err"
check "untracked test path" 2 "$?"
grep -q "untracked-test-ref" "$TMP/5.err" && grep -q "tests/test_untracked.py" "$TMP/5.err" \
  && note "refusal names" "untracked-test-ref + tests/test_untracked.py" \
  || { echo "FALSIFIER-FAIL 5: refusal did not name the path: $(cat "$TMP/5.err")"; FAILURES=$((FAILURES + 1)); }

# ------------------------------------------------------------------ 6. trace ref out of scope
say "6  ch:trace spec ref OUT of the approved scope -> judge-packet refuses, names the id"
TRACE="ch:trace row=$TASK_A spec=plan-a.xml#SG8-REQ-9 test=tests/test_xml_specs.py::test_gate_runs_these_tests evidence=none:not-exported verdict=none:not-judged commit=none:not-implemented witness=boardctl:show-readback"
auger spec judge-packet "$TMP/corpus" --plan "$PLAN" --row "$TASK_A" \
  --board "$TMP/board" --ledger "$TMP/ledger" --repo-root "$WORK" \
  --trace "$TRACE" --out "$TMP/6-packet.json" >/dev/null 2>"$TMP/6.err"
check "out-of-scope trace ref" 2 "$?"
grep -q "trace-spec-out-of-scope" "$TMP/6.err" && grep -q "SG8-REQ-9" "$TMP/6.err" \
  && note "refusal names" "trace-spec-out-of-scope + SG8-REQ-9" \
  || { echo "FALSIFIER-FAIL 6: refusal did not name the id: $(cat "$TMP/6.err")"; FAILURES=$((FAILURES + 1)); }
# control: the marker bound into the approved row is accepted (the refusal is not unconditional).
auger spec judge-packet "$TMP/corpus" --plan "$PLAN" --row "$TASK_A" \
  --board "$TMP/board" --ledger "$TMP/ledger" --repo-root "$WORK" \
  --out "$TMP/6-control.json" >/dev/null 2>"$TMP/6b.err"
check "valid bound marker (control)" 0 "$?"

printf '\n=== summary ===\n'
if [ "$FAILURES" -eq 0 ]; then
  printf 'FALSIFIERS: all expectations met (0 failures)\n'
  exit 0
fi
printf 'FALSIFIERS: %s expectation(s) UNMET\n' "$FAILURES"
exit 1
