# Verdict: REVIEW-AUGER-006

**Task:** The smoke teardown consults the DELETE status before rmtree and treats 409 like 429 (AUG-117 recurrence)
**Evaluated:** 2026-11-15T12:24:35.337402
**Result:** ✗ FAIL

## Criteria

- ✓ **PASS: the smoke teardown consults the DELETE status before it removes the namespace
directory — the only shutil.rmtree in the helper sits after the 200/404 guard.
PROOF: grep -n "st not in (200, 404)" tests/smoke.sh prints line 101 and
grep -n "shutil.rmtree" tests/smoke.sh prints line 103, so the guard precedes the rmtree.
FAILIF: the rmtree line number is not greater than the guard line number, or either
pattern is absent from tests/smoke.sh.**
  - PASS: grep -n "st not in (200, 404)" tests/smoke.sh -> 101; grep -n "shutil.rmtree" tests/smoke.sh -> 103 (only occurrence). 103 > 101, guard precedes rmtree. drop() at tests/smoke.sh:78-105 returns False on non-200/404 before rmtree.
- ✓ **PASS: the hermetic teardown proof is collected and green — 8 tests, no skips.
PROOF: python3 -m pytest tests/test_smoke_teardown.py -q prints "8 passed".
FAILIF: any failed or errored node, a collection error, or a pass count other than 8.**
  - PASS: python3 -m pytest tests/test_smoke_teardown.py -q -> "........ [100%]" / "8 passed in 0.36s", exit_code 0, no skips.
- ✓ **PASS: the throwaway sweep covers the pytest leak class and still spares fresh and
foreign namespaces (the prefix and 24h gates are unchanged).
PROOF: python3 -m pytest tests/test_smoke_teardown.py -q -k sweep prints "5 passed".
FAILIF: fewer than 5 passed, any failure, or a collection error.**
  - PASS: python3 -m pytest tests/test_smoke_teardown.py -q -k sweep -> "5 passed, 3 deselected in 0.83s", exit_code 0. Sweep tests cover auger-pytest-* stale reap, ghost row, fresh spared, foreign prefix spared, original smoke/eval classes. THROWAWAY at smoke.sh:34 includes auger-pytest-; 24h cutoff at smoke.sh:112 unchanged.
- ✓ **PASS: the gate pytest arm names the proof file, so the gate and CI execute it.
PROOF: grep pytest tests/gate.sh | grep -c test_smoke_teardown.py prints 1.
FAILIF: a count of 0, or a count above 1.**
  - PASS: grep pytest tests/gate.sh | grep -c test_smoke_teardown.py -> 1 (line 212 pytest arm names tests/test_smoke_teardown.py).

## Pipeline Stages

- ✗ **tier1**
  -   ✗ tests: scanners: nice=nice -n 10
  ✓ lint: scanners: nice=nice -n 10
  ✓ secrets: secrets: scanners=gitleaks+builtin cross-check
- ✓ **tier2**
  - COMPLETE
  ✓ PASS: the smoke teardown consults the DELETE status before it removes the namespace
directory — the only shutil.rmtree in the helper sits after the 200/404 guard.
PROOF: grep -n "st not in (200, 404)" tests/smoke.sh prints line 101 and
grep -n "shutil.rmtree" tests/smoke.sh prints line 103, so the guard precedes the rmtree.
FAILIF: the rmtree line number is not greater than the guard line number, or either
pattern is absent from tests/smoke.sh.: PASS: grep -n "st not in (200, 404)" tests/smoke.sh -> 101; grep -n "shutil.rmtree" tests/smoke.sh -> 103 (only occurrence). 103 > 101, guard precedes rmtree. drop() at tests/smoke.sh:78-105 returns False on non-200/404 before rmtree.
  ✓ PASS: the hermetic teardown proof is collected and green — 8 tests, no skips.
PROOF: python3 -m pytest tests/test_smoke_teardown.py -q prints "8 passed".
FAILIF: any failed or errored node, a collection error, or a pass count other than 8.: PASS: python3 -m pytest tests/test_smoke_teardown.py -q -> "........ [100%]" / "8 passed in 0.36s", exit_code 0, no skips.
  ✓ PASS: the throwaway sweep covers the pytest leak class and still spares fresh and
foreign namespaces (the prefix and 24h gates are unchanged).
PROOF: python3 -m pytest tests/test_smoke_teardown.py -q -k sweep prints "5 passed".
FAILIF: fewer than 5 passed, any failure, or a collection error.: PASS: python3 -m pytest tests/test_smoke_teardown.py -q -k sweep -> "5 passed, 3 deselected in 0.83s", exit_code 0. Sweep tests cover auger-pytest-* stale reap, ghost row, fresh spared, foreign prefix spared, original smoke/eval classes. THROWAWAY at smoke.sh:34 includes auger-pytest-; 24h cutoff at smoke.sh:112 unchanged.
  ✓ PASS: the gate pytest arm names the proof file, so the gate and CI execute it.
PROOF: grep pytest tests/gate.sh | grep -c test_smoke_teardown.py prints 1.
FAILIF: a count of 0, or a count above 1.: PASS: grep pytest tests/gate.sh | grep -c test_smoke_teardown.py -> 1 (line 212 pytest arm names tests/test_smoke_teardown.py).
All four criteria verified: the teardown guard precedes the only rmtree in tests/smoke.sh, the 8-test hermetic proof and 5 sweep tests pass, and the gate pytest arm names the proof file.

## Summary

Stage tier1: FAIL
    ✗ tests: scanners: nice=nice -n 10
  ✓ lint: scanners: nice=nice -n 10
  ✓ secrets: secrets: scanners=gitleaks+builtin cross-check

Stage tier2: PASS
  COMPLETE
  ✓ PASS: the smoke teardown consults the DELETE status before it removes the namespace
directory — the only shutil.rmtree in the helper sits after the 200/404 guard.
PROOF: grep -n "st not in (200, 404)" tests/smoke.sh prints line 101 and
grep -n "shutil.rmtree" tests/smoke.sh prints line 103, so the guard precedes the rmtree.
FAILIF: the rmtree line number is not greater than the guard line number, or either
pattern is absent from tests/smoke.sh.: PASS: grep -n "st not in (200, 404)" tests/smoke.sh -> 101; grep -n "shutil.rmtree" tests/smoke.sh -> 103 (only occurrence). 103 > 101, guard precedes rmtree. drop() at tests/smoke.sh:78-105 returns False on non-200/404 before rmtree.
  ✓ PASS: the hermetic teardown proof is collected and green — 8 tests, no skips.
PROOF: python3 -m pytest tests/test_smoke_teardown.py -q prints "8 passed".
FAILIF: any failed or errored node, a collection error, or a pass count other than 8.: PASS: python3 -m pytest tests/test_smoke_teardown.py -q -> "........ [100%]" / "8 passed in 0.36s", exit_code 0, no skips.
  ✓ PASS: the throwaway sweep covers the pytest leak class and still spares fresh and
foreign namespaces (the prefix and 24h gates are unchanged).
PROOF: python3 -m pytest tests/test_smoke_teardown.py -q -k sweep prints "5 passed".
FAILIF: fewer than 5 passed, any failure, or a collection error.: PASS: python3 -m pytest tests/test_smoke_teardown.py -q -k sweep -> "5 passed, 3 deselected in 0.83s", exit_code 0. Sweep tests cover auger-pytest-* stale reap, ghost row, fresh spared, foreign prefix spared, original smoke/eval classes. THROWAWAY at smoke.sh:34 includes auger-pytest-; 24h cutoff at smoke.sh:112 unchanged.
  ✓ PASS: the gate pytest arm names the proof file, so the gate and CI execute it.
PROOF: grep pytest tests/gate.sh | grep -c test_smoke_teardown.py prints 1.
FAILIF: a count of 0, or a count above 1.: PASS: grep pytest tests/gate.sh | grep -c test_smoke_teardown.py -> 1 (line 212 pytest arm names tests/test_smoke_teardown.py).
All four criteria verified: the teardown guard precedes the only rmtree in tests/smoke.sh, the 8-test hermetic proof and 5 sweep tests pass, and the gate pytest arm names the proof file.

Overall: FAIL ✗
