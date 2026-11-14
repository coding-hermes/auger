#!/bin/bash
# AUG-161 post-fix paired runs: relative vs absolute PATH, per audited file + the pin file.
set -u
cd /home/kara/worktrees/auger-AUG-161
W=$PWD
EV=$W/evidence/AUG-161/postfix
mkdir -p "$EV"
REL=".venv/bin:/home/kara/.local/bin:/usr/local/bin:/usr/bin:/bin"
ABS="$W/.venv/bin:/home/kara/.local/bin:/usr/local/bin:/usr/bin:/bin"
FILES="tests/test_auger.py tests/test_board_evidence_audit.py tests/test_packaging_wheel.py tests/test_prd_proofs.py tests/test_spec_alignment_check.py tests/test_spec_obligation_gate.py tests/test_spec_repository_root.py tests/test_spec_rollback.py tests/test_spec_validate_obligations.py tests/test_spec_validators_agree.py tests/test_task_packet.py tests/test_trace_artifact.py tests/test_xml_specs.py tests/test_executable_path_robustness.py"
SUM="$EV/summary-postfix.txt"
: > "$SUM"
for f in $FILES; do
  base=$(basename "$f" .py)
  for side in rel abs; do
    if [ "$side" = rel ]; then P="$REL"; PY=python3; else P="$ABS"; PY="$W/.venv/bin/python"; fi
    log="$EV/${base}.${side}.log"
    echo "== $base $side start $(date -Is)" >> "$SUM"
    env PATH="$P" "$PY" -m pytest -q "$f" > "$log" 2>&1
    rc=$?
    tail -1 "$log" | sed "s/^/   /" >> "$SUM"
    echo "rc=$rc" >> "$SUM"
  done
done
echo "ALL PAIRED RUNS DONE $(date -Is)" >> "$SUM"
