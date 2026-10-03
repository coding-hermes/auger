# Verdict: AUG-080

**Task:** namespace-leak audit false-positives on teardown race
**Evaluated:** 2026-09-30T15:33:57.272207
**Result:** ✗ FAIL

## Pipeline Stages

- ✗ **tier1**
  -   ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✗ tests: == syntax ==
- ✗ **tier2**
  - INCOMPLETE
  ✗ offline tests pass; audit still fails on persisting registry row; py_compile clean: Criterion: "offline tests pass; audit still fails on persisting registry row; py_compile clean"

EVIDENCE:
1. py_compile clean: `python3 -m py_compile auger.py tests/conftest.py tests/test_auger.py` -> exit 0. ruff check -> "All checks passed!" exit 0.
2. Offline tests pass: `pytest -k "leftover or lag_budget or persists or stale_listing or backoff"` -> 4 passed in 0.06s.
   `pytest -k "teardown or 429 or survives or saturated or limiter"` -> 8 passed in 12.99s.
   Total 12 offline tests green.
3. Audit still fails on persisting registry row: tests/test_auger.py:5704 test_a_persistently_listed_leftover_namespace_still_fails_the_audit asserts found == {"api": [ns], "disk": []} (the persisting row IS reported as a leak). Passed.
   Implementation: tests/conftest.py:176 `api = [n for n in api if n in disk or not _registry_lag_recheck(n)]` — a name still listed after the bounded re-check window stays in the api leak list.
   _registry_lag_recheck (conftest.py:128-144) re-lists up to LAG_RECHECKS=3 times with LAG_RECHECK_BACKOFF_S=2.0s; returns False (persists) after the window.
4. Change scope: commit 7038e19 touches only tests/conftest.py (+42) and tests/test_auger.py (+107).

Full live suite still running in background (long, ~2400s worst case).
Partial verdict — evaluation hit resource cap before all criteria verified

## Summary

Judge Result: AUG-080

Stage tier1: FAIL
    ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✗ tests: == syntax ==

Stage tier2: FAIL
  INCOMPLETE
  ✗ offline tests pass; audit still fails on persisting registry row; py_compile clean: Criterion: "offline tests pass; audit still fails on persisting registry row; py_compile clean"

EVIDENCE:
1. py_compile clean: `python3 -m py_compile auger.py tests/conftest.py tests/test_auger.py` -> exit 0. ruff check -> "All checks passed!" exit 0.
2. Offline tests pass: `pytest -k "leftover or lag_budget or persists or stale_listing or backoff"` -> 4 passed in 0.06s.
   `pytest -k "teardown or 429 or survives or saturated or limiter"` -> 8 passed in 12.99s.
   Total 12 offline tests green.
3. Audit still fails on persisting registry row: tests/test_auger.py:5704 test_a_persistently_listed_leftover_namespace_still_fails_the_audit asserts found == {"api": [ns], "disk": []} (the persisting row IS reported as a leak). Passed.
   Implementation: tests/conftest.py:176 `api = [n for n in api if n in disk or not _registry_lag_recheck(n)]` — a name still listed after the bounded re-check window stays in the api leak list.
   _registry_lag_recheck (conftest.py:128-144) re-lists up to LAG_RECHECKS=3 times with LAG_RECHECK_BACKOFF_S=2.0s; returns False (persists) after the window.
4. Change scope: commit 7038e19 touches only tests/conftest.py (+42) and tests/test_auger.py (+107).

Full live suite still running in background (long, ~2400s worst case).
Partial verdict — evaluation hit resource cap before all criteria verified

Overall: FAIL ✗
