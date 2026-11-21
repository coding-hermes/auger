# Verdict: AUG-171

**Task:** smoke.sh 429 teardown path has no committed regression test (verdict e273af1b residual)
**Evaluated:** 2026-11-20T17:43:47.544886
**Result:** ✓ PASS

## Criteria

- ✓ **PASS: a committed test drives the 429 teardown path in tests/smoke.sh through the real extracted helper.
CURRENT-STATE PROBE (reproduces the gap): grep -c '429' tests/smoke.sh prints 6 (the contract sits at lines 157-158: one re-entry when the status text contains HTTP 429) while grep -c '429' tests/test_smoke_teardown.py prints 1 - that single hit is the docstring at line 187, so no test exercises the retry. Expect those exact counts before the change.
POST-CHANGE ARM 1: grep -c '429' tests/test_smoke_teardown.py prints 3 or more, the extra hits being test code rather than prose.
POST-CHANGE ARM 2: python3 -m pytest tests/test_smoke_teardown.py -q prints 0 failed and the new 429 test is col**
  - ARM 1: grep -c '429' tests/test_smoke_teardown.py = 8 (>=3); the new hits are test code at lines 204-228: test_teardown_reenters_after_429_and_removes_directory (scripts delete_statuses=[429,200], asserts the delete call sequence [429,200], directory removed, 'namespace ... removed' printed) and test_teardown_stops_after_persistent_429_and_keeps_directory (scripts [429], asserts exactly two attempts [429,429], directory intact, 'teardown skipped: DELETE returned HTTP 429' printed). Both drive the REAL helper: _heredoc_body() extracts the ns_helper python heredoc from tests/smoke.sh and _run executes it, so the 429 re-entry at smoke.sh:157-158 ('if not ok and "HTTP 429" in why: ok, why = drop(ns)') is the code under test. smoke.sh count unchanged at 6. ARM 2: python3 -m pytest tests/test_smoke_teardown.py -q -> exit 0, '13 passed in 2.01s', 0 failed; --collect-only and -v output confirm both 429 tests collected and PASSED. Committed: git log shows a2cdd76 'AUG-171: prove the smoke teardown retries on 429 instead of swallowing it' touching tests/test_smoke_teardown.py; working tree clean for both files.

## Pipeline Stages

- ✓ **tier1**
  -   ✓ lint: scanners: nice=nice -n 10
  ✓ secrets: secrets: scanners=gitleaks+builtin cross-check
  ✓ tests: scanners: nice=nice -n 10
- ✓ **tier2**
  - COMPLETE
  ✓ PASS: a committed test drives the 429 teardown path in tests/smoke.sh through the real extracted helper.
CURRENT-STATE PROBE (reproduces the gap): grep -c '429' tests/smoke.sh prints 6 (the contract sits at lines 157-158: one re-entry when the status text contains HTTP 429) while grep -c '429' tests/test_smoke_teardown.py prints 1 - that single hit is the docstring at line 187, so no test exercises the retry. Expect those exact counts before the change.
POST-CHANGE ARM 1: grep -c '429' tests/test_smoke_teardown.py prints 3 or more, the extra hits being test code rather than prose.
POST-CHANGE ARM 2: python3 -m pytest tests/test_smoke_teardown.py -q prints 0 failed and the new 429 test is col: ARM 1: grep -c '429' tests/test_smoke_teardown.py = 8 (>=3); the new hits are test code at lines 204-228: test_teardown_reenters_after_429_and_removes_directory (scripts delete_statuses=[429,200], asserts the delete call sequence [429,200], directory removed, 'namespace ... removed' printed) and test_teardown_stops_after_persistent_429_and_keeps_directory (scripts [429], asserts exactly two attempts [429,429], directory intact, 'teardown skipped: DELETE returned HTTP 429' printed). Both drive the REAL helper: _heredoc_body() extracts the ns_helper python heredoc from tests/smoke.sh and _run executes it, so the 429 re-entry at smoke.sh:157-158 ('if not ok and "HTTP 429" in why: ok, why = drop(ns)') is the code under test. smoke.sh count unchanged at 6. ARM 2: python3 -m pytest tests/test_smoke_teardown.py -q -> exit 0, '13 passed in 2.01s', 0 failed; --collect-only and -v output confirm both 429 tests collected and PASSED. Committed: git log shows a2cdd76 'AUG-171: prove the smoke teardown retries on 429 instead of swallowing it' touching tests/test_smoke_teardown.py; working tree clean for both files.
AUG-171 delivered: two committed hermetic tests exercise the smoke.sh 429 teardown retry through the real extracted ns_helper heredoc, grep count is 8 (>=3), and the pytest file runs green with 13 passed / 0 failed.

## Summary

Stage tier1: PASS
    ✓ lint: scanners: nice=nice -n 10
  ✓ secrets: secrets: scanners=gitleaks+builtin cross-check
  ✓ tests: scanners: nice=nice -n 10

Stage tier2: PASS
  COMPLETE
  ✓ PASS: a committed test drives the 429 teardown path in tests/smoke.sh through the real extracted helper.
CURRENT-STATE PROBE (reproduces the gap): grep -c '429' tests/smoke.sh prints 6 (the contract sits at lines 157-158: one re-entry when the status text contains HTTP 429) while grep -c '429' tests/test_smoke_teardown.py prints 1 - that single hit is the docstring at line 187, so no test exercises the retry. Expect those exact counts before the change.
POST-CHANGE ARM 1: grep -c '429' tests/test_smoke_teardown.py prints 3 or more, the extra hits being test code rather than prose.
POST-CHANGE ARM 2: python3 -m pytest tests/test_smoke_teardown.py -q prints 0 failed and the new 429 test is col: ARM 1: grep -c '429' tests/test_smoke_teardown.py = 8 (>=3); the new hits are test code at lines 204-228: test_teardown_reenters_after_429_and_removes_directory (scripts delete_statuses=[429,200], asserts the delete call sequence [429,200], directory removed, 'namespace ... removed' printed) and test_teardown_stops_after_persistent_429_and_keeps_directory (scripts [429], asserts exactly two attempts [429,429], directory intact, 'teardown skipped: DELETE returned HTTP 429' printed). Both drive the REAL helper: _heredoc_body() extracts the ns_helper python heredoc from tests/smoke.sh and _run executes it, so the 429 re-entry at smoke.sh:157-158 ('if not ok and "HTTP 429" in why: ok, why = drop(ns)') is the code under test. smoke.sh count unchanged at 6. ARM 2: python3 -m pytest tests/test_smoke_teardown.py -q -> exit 0, '13 passed in 2.01s', 0 failed; --collect-only and -v output confirm both 429 tests collected and PASSED. Committed: git log shows a2cdd76 'AUG-171: prove the smoke teardown retries on 429 instead of swallowing it' touching tests/test_smoke_teardown.py; working tree clean for both files.
AUG-171 delivered: two committed hermetic tests exercise the smoke.sh 429 teardown retry through the real extracted ns_helper heredoc, grep count is 8 (>=3), and the pytest file runs green with 13 passed / 0 failed.

Overall: PASS ✓
