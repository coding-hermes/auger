# Verdict: REVIEW-AUGER-006

**Task:** The smoke teardown consults the DELETE status before rmtree and treats 409 like 429 (AUG-117 recurrence)
**Evaluated:** 2026-11-15T13:02:04.739424
**Result:** ✗ FAIL

## Criteria

- ✓ **PASS: the smoke teardown consults the DELETE status before it removes the namespace
directory — the only shutil.rmtree in the helper sits after the 200/404 guard.
PROOF: grep -n "st not in (200, 404)" tests/smoke.sh prints line 101 and
grep -n "shutil.rmtree" tests/smoke.sh prints line 103, so the guard precedes the rmtree.
FAILIF: the rmtree line number is not greater than the guard line number, or either
pattern is absent from tests/smoke.sh.
**
  - `grep -n "st not in (200, 404)" tests/smoke.sh` -> `101:    if st not in (200, 404):`; `grep -n "shutil.rmtree" tests/smoke.sh` -> `103:    shutil.rmtree(os.path.join(ROOT, ns), ignore_errors=True)` (single occurrence). 103 > 101. drop() at tests/smoke.sh:95-105 runs the DELETE (with the 409 ladder) first, returns `False, f"DELETE returned HTTP {st}"` on any non-200/404, and only then rmtree's.
- ✓ **PASS: the hermetic teardown proof is collected and green — 8 tests, no skips.
PROOF: python3 -m pytest tests/test_smoke_teardown.py -q prints "8 passed".
FAILIF: any failed or errored node, a collection error, or a pass count other than 8.
**
  - `python3 -m pytest tests/test_smoke_teardown.py -q` -> `........ [100%]` / `8 passed in 0.41s` (exit_code 0). Re-run with `-q -rs` also printed `8 passed in 0.47s` with no skip lines, so no node is skipped.
- ✓ **PASS: the throwaway sweep covers the pytest leak class and still spares fresh and
foreign namespaces (the prefix and 24h gates are unchanged).
PROOF: python3 -m pytest tests/test_smoke_teardown.py -q -k sweep prints "5 passed".
FAILIF: fewer than 5 passed, any failure, or a collection error.
**
  - `python3 -m pytest tests/test_smoke_teardown.py -q -k sweep` -> `..... [100%]` / `5 passed, 3 deselected in 0.54s` (exit_code 0). The five sweep tests (tests/test_smoke_teardown.py:198-262) cover stale auger-pytest-* reaping, the ghost-row shape, a fresh pytest dir spared, foreign prefixes (auger-prod-one/releases/auger) untouched, and the original auger-smoke-/auger-eval- classes still reaped. Gates unchanged in shape: THROWAWAY at tests/smoke.sh:34, 24h cutoff at tests/smoke.sh:113.
- ✓ **PASS: the gate pytest arm names the proof file, so the gate and CI execute it.
PROOF: grep pytest tests/gate.sh | grep -c test_smoke_teardown.py prints 1.
FAILIF: a count of 0, or a count above 1.
**
  - `grep pytest tests/gate.sh | grep -c test_smoke_teardown.py` -> `1`. The single hit is the run_pytest_arm invocation at tests/gate.sh:212 which names `tests/test_smoke_teardown.py`; run_pytest_arm is called at tests/gate.sh:237 and :240 in both gate modes, and tests/test_smoke_teardown.py:266-278 (test_gate_arm_runs_this_file) asserts the file is named in that arm.
- ✓ **The 409 ladder in drop() is bounded and terminates (no unbounded retry loop) and a persistent 409 is reported, never reinterpreted as deletion.**
  - tests/smoke.sh:95-105: `for attempt in range(len(conflict_backoffs) + 1)` with `if st != 409 or attempt == len(conflict_backoffs): break`; a persistent 409 returns `(False, "DELETE returned HTTP 409")` and the caller prints `teardown skipped: ...` (tests/smoke.sh:155-157). test_teardown_survives_a_persistent_409_with_the_directory_intact asserts the delete count equals len(ladder)+1 and the directory survives — `python3 -m pytest tests/test_smoke_teardown.py -q -k "persistent_409 or transient_409"` -> `2 passed, 6 deselected`.
- ✗ **The hermetic rig's namespace root (AUGER_NS_ROOT) is the same directory the real auger writes to (DUCKBRAIN_NAMESPACES_PATH / DUCKBRAIN_DATA_DIR), so the tests' "directory intact/removed" assertions are about the real namespace directory.**
  - tests/smoke.sh:40 sets `ROOT = os.environ.get("AUGER_NS_ROOT") or ~/duckbrain/namespaces`, but auger resolves its namespace tree via namespaces_base() (auger.py:483-491) from DUCKBRAIN_NAMESPACES_PATH / DUCKBRAIN_DATA_DIR; AUGER_NS_ROOT is read nowhere in auger.py (grep hits only tests/smoke.sh:38,40 and tests/test_smoke_teardown.py). Reproduced by extracting the real ns_helper heredoc and running `helper teardown auger-smoke-x` with a stub DELETE returning 200 while the namespace directory existed at $DUCKBRAIN_NAMESPACES_PATH/auger-smoke-x (the layout auger actually writes): stdout `namespace auger-smoke-x removed`, rc 0, but `REAL namespace dir still exists: True`. So the smoke teardown never removes the real namespace directory, and the tests' directory assertions (tests/test_smoke_teardown.py:139-144, 168-172, 185-190) are made against a directory auger never writes to — the 409 "directory intact" assertion is vacuous.
- ✓ **The 429 contract is preserved: a 429 from the DELETE still leads to exactly one re-entry of drop() and never to an rmtree under a surviving registry row.**
  - 429 is still absorbed inline by the transport (auger.py:568-583, honouring retryAfter/Retry-After, bounded by `retries`), and the teardown caller re-enters drop() exactly once (tests/smoke.sh:155-157: `if not ok and "HTTP 429" in why: ok, why = drop(ns)`). Because rmtree now sits behind the 200/404 guard (tests/smoke.sh:101-103), a 429 surviving both attempts returns before rmtree, so no directory is destroyed under a surviving row; the 409 ladder is a separate path and does not consume the 429 budget.
- ✓ **The fix does not regress the non-throwaway guard: teardown still refuses namespaces lacking the throwaway prefix.**
  - tests/smoke.sh:149-150 still refuses before any DELETE: `teardown skipped: {ns} lacks the auger-smoke-/auger-eval- prefix`; the sweep still prefix-gates on THROWAWAY (tests/smoke.sh:34, 116-117). test_sweep_never_takes_a_foreign_prefix (tests/test_smoke_teardown.py:236-250) asserts zero deletes and untouched dirs for auger-prod-one/releases/auger — green in the `-k sweep` run (`5 passed`).

## Pipeline Stages

- ✗ **tier1**
  -   ✓ lint: scanners: nice=nice -n 10
  ✓ secrets: secrets: scanners=gitleaks+builtin cross-check
  ✗ tests: FAIL (tests/test_auger.py::test_a_question_landing_in_a_domain_advances_its_row_too [first failing i
- ✗ **tier2**
  - INCOMPLETE
  ✓ PASS: the smoke teardown consults the DELETE status before it removes the namespace
directory — the only shutil.rmtree in the helper sits after the 200/404 guard.
PROOF: grep -n "st not in (200, 404)" tests/smoke.sh prints line 101 and
grep -n "shutil.rmtree" tests/smoke.sh prints line 103, so the guard precedes the rmtree.
FAILIF: the rmtree line number is not greater than the guard line number, or either
pattern is absent from tests/smoke.sh.
: `grep -n "st not in (200, 404)" tests/smoke.sh` -> `101:    if st not in (200, 404):`; `grep -n "shutil.rmtree" tests/smoke.sh` -> `103:    shutil.rmtree(os.path.join(ROOT, ns), ignore_errors=True)` (single occurrence). 103 > 101. drop() at tests/smoke.sh:95-105 runs the DELETE (with the 409 ladder) first, returns `False, f"DELETE returned HTTP {st}"` on any non-200/404, and only then rmtree's.
  ✓ PASS: the hermetic teardown proof is collected and green — 8 tests, no skips.
PROOF: python3 -m pytest tests/test_smoke_teardown.py -q prints "8 passed".
FAILIF: any failed or errored node, a collection error, or a pass count other than 8.
: `python3 -m pytest tests/test_smoke_teardown.py -q` -> `........ [100%]` / `8 passed in 0.41s` (exit_code 0). Re-run with `-q -rs` also printed `8 passed in 0.47s` with no skip lines, so no node is skipped.
  ✓ PASS: the throwaway sweep covers the pytest leak class and still spares fresh and
foreign namespaces (the prefix and 24h gates are unchanged).
PROOF: python3 -m pytest tests/test_smoke_teardown.py -q -k sweep prints "5 passed".
FAILIF: fewer than 5 passed, any failure, or a collection error.
: `python3 -m pytest tests/test_smoke_teardown.py -q -k sweep` -> `..... [100%]` / `5 passed, 3 deselected in 0.54s` (exit_code 0). The five sweep tests (tests/test_smoke_teardown.py:198-262) cover stale auger-pytest-* reaping, the ghost-row shape, a fresh pytest dir spared, foreign prefixes (auger-prod-one/releases/auger) untouched, and the original auger-smoke-/auger-eval- classes still reaped. Gates unchanged in shape: THROWAWAY at tests/smoke.sh:34, 24h cutoff at tests/smoke.sh:113.
  ✓ PASS: the gate pytest arm names the proof file, so the gate and CI execute it.
PROOF: grep pytest tests/gate.sh | grep -c test_smoke_teardown.py prints 1.
FAILIF: a count of 0, or a count above 1.
: `grep pytest tests/gate.sh | grep -c test_smoke_teardown.py` -> `1`. The single hit is the run_pytest_arm invocation at tests/gate.sh:212 which names `tests/test_smoke_teardown.py`; run_pytest_arm is called at tests/gate.sh:237 and :240 in both gate modes, and tests/test_smoke_teardown.py:266-278 (test_gate_arm_runs_this_file) asserts the file is named in that arm.
  ✓ The 409 ladder in drop() is bounded and terminates (no unbounded retry loop) and a persistent 409 is reported, never reinterpreted as deletion.: tests/smoke.sh:95-105: `for attempt in range(len(conflict_backoffs) + 1)` with `if st != 409 or attempt == len(conflict_backoffs): break`; a persistent 409 returns `(False, "DELETE returned HTTP 409")` and the caller prints `teardown skipped: ...` (tests/smoke.sh:155-157). test_teardown_survives_a_persistent_409_with_the_directory_intact asserts the delete count equals len(ladder)+1 and the directory survives — `python3 -m pytest tests/test_smoke_teardown.py -q -k "persistent_409 or transient_409"` -> `2 passed, 6 deselected`.
  ✗ The hermetic rig's namespace root (AUGER_NS_ROOT) is the same directory the real auger writes to (DUCKBRAIN_NAMESPACES_PATH / DUCKBRAIN_DATA_DIR), so the tests' "directory intact/removed" assertions are about the real namespace directory.: tests/smoke.sh:40 sets `ROOT = os.environ.get("AUGER_NS_ROOT") or ~/duckbrain/namespaces`, but auger resolves its namespace tree via namespaces_base() (auger.py:483-491) from DUCKBRAIN_NAMESPACES_PATH / DUCKBRAIN_DATA_DIR; AUGER_NS_ROOT is read nowhere in auger.py (grep hits only tests/smoke.sh:38,40 and tests/test_smoke_teardown.py). Reproduced by extracting the real ns_helper heredoc and running `helper teardown auger-smoke-x` with a stub DELETE returning 200 while the namespace directory existed at $DUCKBRAIN_NAMESPACES_PATH/auger-smoke-x (the layout auger actually writes): stdout `namespace auger-smoke-x removed`, rc 0, but `REAL namespace dir still exists: True`. So the smoke teardown never removes the real namespace directory, and the tests' directory assertions (tests/test_smoke_teardown.py:139-144, 168-172, 185-190) are made against a directory auger never writes to — the 409 "directory intact" assertion is vacuous.
  ✓ The 429 contract is preserved: a 429 from the DELETE still leads to exactly one re-entry of drop() and never to an rmtree under a surviving registry row.: 429 is still absorbed inline by the transport (auger.py:568-583, honouring retryAfter/Retry-After, bounded by `retries`), and the teardown caller re-enters drop() exactly once (tests/smoke.sh:155-157: `if not ok and "HTTP 429" in why: ok, why = drop(ns)`). Because rmtree now sits behind the 200/404 guard (tests/smoke.sh:101-103), a 429 surviving both attempts returns before rmtree, so no directory is destroyed under a surviving row; the 409 ladder is a separate path and does not consume the 429 budget.
  ✓ The fix does not regress the non-throwaway guard: teardown still refuses namespaces lacking the throwaway prefix.: tests/smoke.sh:149-150 still refuses before any DELETE: `teardown skipped: {ns} lacks the auger-smoke-/auger-eval- prefix`; the sweep still prefix-gates on THROWAWAY (tests/smoke.sh:34, 116-117). test_sweep_never_takes_a_foreign_prefix (tests/test_smoke_teardown.py:236-250) asserts zero deletes and untouched dirs for auger-prod-one/releases/auger — green in the `-k sweep` run (`5 passed`).
The four command-shaped criteria all hold (guard at smoke.sh:101 precedes the sole rmtree at :103; 8 passed; 5 passed for -k sweep; gate count 1), but the teardown's ROOT (AUGER_NS_ROOT) is not the directory auger actually writes to (DUCKBRAIN_NAMESPACES_PATH/DATA_DIR), so the smoke teardown reports removal while leaving the real namespace directory behind and the hermetic directory assertions are vacuous. [uncovered failure paths: failure mode 0 (The smoke teardown reports "namespace <ns> removed" while the real namespace directory (under DUCKBRAIN_NAMESPACES_PATH / DUCKBRAIN_DATA_DIR) is left on disk — the helper's ROOT is a different path than auger's namespaces_base().): detected_by is empty — uncovered failure path; failure mode 1 (The hermetic proof's "directory intact after a 409" assertion is vacuous: the rig creates the directory under AUGER_NS_ROOT, which the helper rmtree's, but auger never writes there, so the test cannot observe the real ghost-row shape.): detected_by is empty — uncovered failure path]

## Summary

Stage tier1: FAIL
    ✓ lint: scanners: nice=nice -n 10
  ✓ secrets: secrets: scanners=gitleaks+builtin cross-check
  ✗ tests: FAIL (tests/test_auger.py::test_a_question_landing_in_a_domain_advances_its_row_too [first failing i

Stage tier2: FAIL
  INCOMPLETE
  ✓ PASS: the smoke teardown consults the DELETE status before it removes the namespace
directory — the only shutil.rmtree in the helper sits after the 200/404 guard.
PROOF: grep -n "st not in (200, 404)" tests/smoke.sh prints line 101 and
grep -n "shutil.rmtree" tests/smoke.sh prints line 103, so the guard precedes the rmtree.
FAILIF: the rmtree line number is not greater than the guard line number, or either
pattern is absent from tests/smoke.sh.
: `grep -n "st not in (200, 404)" tests/smoke.sh` -> `101:    if st not in (200, 404):`; `grep -n "shutil.rmtree" tests/smoke.sh` -> `103:    shutil.rmtree(os.path.join(ROOT, ns), ignore_errors=True)` (single occurrence). 103 > 101. drop() at tests/smoke.sh:95-105 runs the DELETE (with the 409 ladder) first, returns `False, f"DELETE returned HTTP {st}"` on any non-200/404, and only then rmtree's.
  ✓ PASS: the hermetic teardown proof is collected and green — 8 tests, no skips.
PROOF: python3 -m pytest tests/test_smoke_teardown.py -q prints "8 passed".
FAILIF: any failed or errored node, a collection error, or a pass count other than 8.
: `python3 -m pytest tests/test_smoke_teardown.py -q` -> `........ [100%]` / `8 passed in 0.41s` (exit_code 0). Re-run with `-q -rs` also printed `8 passed in 0.47s` with no skip lines, so no node is skipped.
  ✓ PASS: the throwaway sweep covers the pytest leak class and still spares fresh and
foreign namespaces (the prefix and 24h gates are unchanged).
PROOF: python3 -m pytest tests/test_smoke_teardown.py -q -k sweep prints "5 passed".
FAILIF: fewer than 5 passed, any failure, or a collection error.
: `python3 -m pytest tests/test_smoke_teardown.py -q -k sweep` -> `..... [100%]` / `5 passed, 3 deselected in 0.54s` (exit_code 0). The five sweep tests (tests/test_smoke_teardown.py:198-262) cover stale auger-pytest-* reaping, the ghost-row shape, a fresh pytest dir spared, foreign prefixes (auger-prod-one/releases/auger) untouched, and the original auger-smoke-/auger-eval- classes still reaped. Gates unchanged in shape: THROWAWAY at tests/smoke.sh:34, 24h cutoff at tests/smoke.sh:113.
  ✓ PASS: the gate pytest arm names the proof file, so the gate and CI execute it.
PROOF: grep pytest tests/gate.sh | grep -c test_smoke_teardown.py prints 1.
FAILIF: a count of 0, or a count above 1.
: `grep pytest tests/gate.sh | grep -c test_smoke_teardown.py` -> `1`. The single hit is the run_pytest_arm invocation at tests/gate.sh:212 which names `tests/test_smoke_teardown.py`; run_pytest_arm is called at tests/gate.sh:237 and :240 in both gate modes, and tests/test_smoke_teardown.py:266-278 (test_gate_arm_runs_this_file) asserts the file is named in that arm.
  ✓ The 409 ladder in drop() is bounded and terminates (no unbounded retry loop) and a persistent 409 is reported, never reinterpreted as deletion.: tests/smoke.sh:95-105: `for attempt in range(len(conflict_backoffs) + 1)` with `if st != 409 or attempt == len(conflict_backoffs): break`; a persistent 409 returns `(False, "DELETE returned HTTP 409")` and the caller prints `teardown skipped: ...` (tests/smoke.sh:155-157). test_teardown_survives_a_persistent_409_with_the_directory_intact asserts the delete count equals len(ladder)+1 and the directory survives — `python3 -m pytest tests/test_smoke_teardown.py -q -k "persistent_409 or transient_409"` -> `2 passed, 6 deselected`.
  ✗ The hermetic rig's namespace root (AUGER_NS_ROOT) is the same directory the real auger writes to (DUCKBRAIN_NAMESPACES_PATH / DUCKBRAIN_DATA_DIR), so the tests' "directory intact/removed" assertions are about the real namespace directory.: tests/smoke.sh:40 sets `ROOT = os.environ.get("AUGER_NS_ROOT") or ~/duckbrain/namespaces`, but auger resolves its namespace tree via namespaces_base() (auger.py:483-491) from DUCKBRAIN_NAMESPACES_PATH / DUCKBRAIN_DATA_DIR; AUGER_NS_ROOT is read nowhere in auger.py (grep hits only tests/smoke.sh:38,40 and tests/test_smoke_teardown.py). Reproduced by extracting the real ns_helper heredoc and running `helper teardown auger-smoke-x` with a stub DELETE returning 200 while the namespace directory existed at $DUCKBRAIN_NAMESPACES_PATH/auger-smoke-x (the layout auger actually writes): stdout `namespace auger-smoke-x removed`, rc 0, but `REAL namespace dir still exists: True`. So the smoke teardown never removes the real namespace directory, and the tests' directory assertions (tests/test_smoke_teardown.py:139-144, 168-172, 185-190) are made against a directory auger never writes to — the 409 "directory intact" assertion is vacuous.
  ✓ The 429 contract is preserved: a 429 from the DELETE still leads to exactly one re-entry of drop() and never to an rmtree under a surviving registry row.: 429 is still absorbed inline by the transport (auger.py:568-583, honouring retryAfter/Retry-After, bounded by `retries`), and the teardown caller re-enters drop() exactly once (tests/smoke.sh:155-157: `if not ok and "HTTP 429" in why: ok, why = drop(ns)`). Because rmtree now sits behind the 200/404 guard (tests/smoke.sh:101-103), a 429 surviving both attempts returns before rmtree, so no directory is destroyed under a surviving row; the 409 ladder is a separate path and does not consume the 429 budget.
  ✓ The fix does not regress the non-throwaway guard: teardown still refuses namespaces lacking the throwaway prefix.: tests/smoke.sh:149-150 still refuses before any DELETE: `teardown skipped: {ns} lacks the auger-smoke-/auger-eval- prefix`; the sweep still prefix-gates on THROWAWAY (tests/smoke.sh:34, 116-117). test_sweep_never_takes_a_foreign_prefix (tests/test_smoke_teardown.py:236-250) asserts zero deletes and untouched dirs for auger-prod-one/releases/auger — green in the `-k sweep` run (`5 passed`).
The four command-shaped criteria all hold (guard at smoke.sh:101 precedes the sole rmtree at :103; 8 passed; 5 passed for -k sweep; gate count 1), but the teardown's ROOT (AUGER_NS_ROOT) is not the directory auger actually writes to (DUCKBRAIN_NAMESPACES_PATH/DATA_DIR), so the smoke teardown reports removal while leaving the real namespace directory behind and the hermetic directory assertions are vacuous. [uncovered failure paths: failure mode 0 (The smoke teardown reports "namespace <ns> removed" while the real namespace directory (under DUCKBRAIN_NAMESPACES_PATH / DUCKBRAIN_DATA_DIR) is left on disk — the helper's ROOT is a different path than auger's namespaces_base().): detected_by is empty — uncovered failure path; failure mode 1 (The hermetic proof's "directory intact after a 409" assertion is vacuous: the rig creates the directory under AUGER_NS_ROOT, which the helper rmtree's, but auger never writes there, so the test cannot observe the real ghost-row shape.): detected_by is empty — uncovered failure path]

Overall: FAIL ✗
