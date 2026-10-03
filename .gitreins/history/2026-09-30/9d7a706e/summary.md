# Verdict: AUG-080

**Task:** namespace-leak audit false-positives on teardown race
**Evaluated:** 2026-09-30T16:20:41.958969
**Result:** ✗ FAIL

## Pipeline Stages

- ✗ **tier1**
  -   ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✗ tests: == syntax ==
- ✓ **tier2**
  - COMPLETE
  ✓ offline tests pass; audit still fails on persisting registry row; py_compile clean: All three parts verified with fresh command output. (1) py_compile clean: `python3 -m py_compile auger.py tests/conftest.py tests/test_auger.py` -> exit 0. (2) Offline tests pass: `python3 -m pytest tests/test_auger.py -k "teardown_survives or teardown_honours or leftover_listing or persistently_listed or lag_budget or saturated_limiter or 429" -p no:cacheprovider -q` -> '8 passed, 255 deselected in 0.22s', EXIT=0. (3) Audit still fails on persisting registry row: `pytest -k "persistently_listed or leftover_listing or lag_budget" -v` -> 4 passed, EXIT=0, including tests/test_auger.py::test_a_persistently_listed_leftover_namespace_still_fails_the_audit PASSED, which asserts found == {"api":[ns],"disk":[]} after the bounded re-check window. Implementation: tests/conftest.py:176 `api = [n for n in api if n in disk or not _registry_lag_recheck(n)]`; _registry_lag_recheck (conftest.py:128-144) re-lists LAG_RECHECKS=3 times with LAG_RECHECK_BACKOFF_S=2.0s and returns False (persists) after the window, so a real leak is still reported. ruff check also 'All checks passed!'.
AUG-080 fix verified: offline tests green (8 passed, exit 0), the audit still fails on a persisting registry row (dedicated test passes), and py_compile is clean.

## Summary

Judge Result: AUG-080

Stage tier1: FAIL
    ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✗ tests: == syntax ==

Stage tier2: PASS
  COMPLETE
  ✓ offline tests pass; audit still fails on persisting registry row; py_compile clean: All three parts verified with fresh command output. (1) py_compile clean: `python3 -m py_compile auger.py tests/conftest.py tests/test_auger.py` -> exit 0. (2) Offline tests pass: `python3 -m pytest tests/test_auger.py -k "teardown_survives or teardown_honours or leftover_listing or persistently_listed or lag_budget or saturated_limiter or 429" -p no:cacheprovider -q` -> '8 passed, 255 deselected in 0.22s', EXIT=0. (3) Audit still fails on persisting registry row: `pytest -k "persistently_listed or leftover_listing or lag_budget" -v` -> 4 passed, EXIT=0, including tests/test_auger.py::test_a_persistently_listed_leftover_namespace_still_fails_the_audit PASSED, which asserts found == {"api":[ns],"disk":[]} after the bounded re-check window. Implementation: tests/conftest.py:176 `api = [n for n in api if n in disk or not _registry_lag_recheck(n)]`; _registry_lag_recheck (conftest.py:128-144) re-lists LAG_RECHECKS=3 times with LAG_RECHECK_BACKOFF_S=2.0s and returns False (persists) after the window, so a real leak is still reported. ruff check also 'All checks passed!'.
AUG-080 fix verified: offline tests green (8 passed, exit 0), the audit still fails on a persisting registry row (dedicated test passes), and py_compile is clean.

Overall: FAIL ✗
