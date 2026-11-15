# AUG-173 — evidence notes

Defect: the AUG-130 case in tests/test_gate_mode.sh runs the inner gate without
GATE_FULL=1, so when the checkout's HEAD is a docs-only commit the inner gate takes the
doc-only fast path (tests/gate.sh:74-107), skips pytest as a NON-required skip, prints
GATE PASS and exits 0 — and the case fails with "gate exited 0 with its pytest arm NOT
run". Every docs-only commit therefore reds CI (runs 43771550165, 43766513325,
43770948049).

Fix: one token — add GATE_FULL=1 to the run_inner_gate() env (tests/test_gate_mode.sh:423),
which disables the doc-only shortcut for that probe only. tests/gate.sh honours it at
gate.sh:77-79 ("Override: GATE_FULL=1 forces the full gate regardless of diff").
No assertion weakened; AUGER_ALLOW_SKIPPED_ARMS still unset in the strict leg;
tests/gate.sh untouched (git diff --stat HEAD -- tests/gate.sh is empty).

## Legs

1. RED (pre-fix) — 01-prefix-red-docsonly.txt
   git worktree add --detach /tmp/aug173-docsonly c461056
   cd /tmp/aug173-docsonly && PATH=<worktree>/.venv/bin:$PATH bash tests/test_gate_mode.sh
   → rc=1; line 4: "AUG-130: gate exited 0 with its pytest arm NOT run:" with
   "GATE PASS" + "arms SKIPPED (not passed): pytest, wrapper-regression, e2e-smoke".

2. GREEN on docs-only tree — 02-postfix-green-docsonly.txt
   cp <worktree>/tests/test_gate_mode.sh /tmp/aug173-docsonly/tests/
   same command in /tmp/aug173-docsonly → rc=0, "AUG-130 skipped-arm policy: PASS".

3. Runner leg in this worktree — 03-postfix-green-worktree.txt
   cd /home/kara/worktrees/auger-AUG-173 && PATH=.venv/bin:$PATH bash tests/test_gate_mode.sh
   (HEAD here is the same docs-only commit c461056) → rc=0, "AUG-130 skipped-arm policy: PASS".

4. Non-vacuity — 04-nonvacuity.txt
   Scratch copy of the fixed file with the strict leg's expected needle mutated
   ('GATE FAIL: required arms did not run' → 'GATE FAIL: arms did not run (MUTATED)'),
   installed in /tmp/aug173-docsonly/tests/, case re-run → rc=1 with
   "expected output to contain GATE\ FAIL:\ arms\ did\ not\ run\ \(MUTATED\)". The fixed
   file was restored afterwards. The assertions are live.

## AC greps (measured on this tree)

- AC2: grep -c "AUGER_GATE_INNER=1 GATE_FULL=1" tests/test_gate_mode.sh → 1
- AC4: grep -c "env -u AUGER_ALLOW_SKIPPED_ARMS" tests/test_gate_mode.sh → 1
       grep -c "GATE PASS (DEGRADED)" tests/test_gate_mode.sh → 1
       grep -c "doc-only fast path" tests/gate.sh → 5 (>= 2)
- AC5: both shapes covered by legs 2/3 (docs-only tree and this worktree — both rc 0).

## Cleanup

/tmp/aug173-docsonly removed with `git worktree remove --force` after the legs.

## Not verified here

The hosted-CI half of AC1 (run 43771550165 red at c461056) is the foreman's measured
evidence quoted in the brief; local leg 1 reproduces its failure lines exactly. CI going
green again requires a commit past this one and is outside this worktree.
