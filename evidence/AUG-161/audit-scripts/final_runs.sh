#!/bin/bash
# Final AUG-161 runs: pin green, alignment re-paired, AC2 exact command, full selection (abs PATH).
set -u
cd /home/kara/worktrees/auger-AUG-161
W=$PWD
EV=$W/evidence/AUG-161
REL=".venv/bin:/home/kara/.local/bin:/usr/local/bin:/usr/bin:/bin"
ABS="$W/.venv/bin:/home/kara/.local/bin:/usr/local/bin:/usr/bin:/bin"
SUM="$EV/summary-final.txt"
: > "$SUM"
run() { # label, path, py, files...
  local label=$1 p=$2 py=$3; shift 3
  echo "== $label start $(date -Is)" >> "$SUM"
  env PATH="$p" "$py" -m pytest -q "$@" > "$EV/$label.log" 2>&1
  echo "rc=$?" >> "$SUM"
  tail -1 "$EV/$label.log" | sed "s/^/   /" >> "$SUM"
}
run pin-green-rel "$REL" python3 tests/test_executable_path_robustness.py
run pin-green-abs "$ABS" "$W/.venv/bin/python" tests/test_executable_path_robustness.py
run align-postfix2-rel "$REL" python3 tests/test_spec_alignment_check.py
run align-postfix2-abs "$ABS" "$W/.venv/bin/python" tests/test_spec_alignment_check.py
# AC2 verbatim
run ac2-exact ".venv/bin:$PATH" python3 tests/test_spec_repository_root.py
# full audited selection, absolute PATH
run full-selection-abs "$ABS" "$W/.venv/bin/python" \
  tests/test_spec_repository_root.py tests/test_prd_proofs.py tests/test_task_packet.py \
  tests/test_spec_obligation_gate.py tests/test_auger.py tests/test_board_evidence_audit.py \
  tests/test_packaging_wheel.py tests/test_spec_alignment_check.py tests/test_spec_rollback.py \
  tests/test_spec_validate_obligations.py tests/test_spec_validators_agree.py \
  tests/test_trace_artifact.py tests/test_xml_specs.py tests/test_executable_path_robustness.py
echo "FINAL RUNS DONE $(date -Is)" >> "$SUM"
