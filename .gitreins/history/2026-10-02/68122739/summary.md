# Verdict: QA-AUGER-6

**Task:** Live-service tests fail under full-suite concurrent load: serialize substrate access
**Evaluated:** 2026-10-02T13:29:18.065700
**Result:** ✗ FAIL

## Pipeline Stages

- ✓ **tier1**
  -   ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==
- ✗ **tier2**
  - INCOMPLETE
  ✗ conftest.py gains a stdlib-only cross-process serialization mechanism (fcntl.flock) so two concurrent pytest legs never hit the live DuckBrain substrate simultaneously; a regression test proves mutual exclusion; the 3 previously failing tests (test_recall_cli_scopes_semantic_hits_to_the_selected_project, test_ask_stores_a_different_question_after_answer_closes_the_first, test_ask_stores_nothing_when_jev_is_unreachable) pass in isolation at HEAD; no non-stdlib imports; no changes outside tests/.: The lock does NOT cover substrate access, so the criterion's central requirement ('two concurrent pytest legs never hit the live DuckBrain substrate simultaneously') is unmet. conftest.py:331-340 defines live_service as `with substrate_lock(), _test_transport_budget(): url = require_live()` then `yield url` OUTSIDE the with-block, so the flock is released before the fixture yields. ns_created (conftest.py:344) depends on live_service but runs namespace create, the test body, and teardown with NO lock held. substrate_lock is referenced only at conftest.py:338 (liveness probe only) and in the two regression tests; there is no autouse fixture. Empirically verified: (a) after the fixture yields, _SUBSTRATE_LOCK_FD is None / depth 0; (b) two concurrent processes emulating the fixture path both reached the 'test body' with lock_held_in_body=False at t=1790947750.464 and t=1790947750.465 (1ms apart) — not serialized. The docstring at conftest.py:347 falsely claims 'the cross-process substrate lock is held for the whole namespace lifecycle (create, test body, teardown)'. The regression test test_substrate_lock_serializes_two_processes (test_auger.py:9417) proves the lock PRIMITIVE works (2 passed in 1.05s) but calls conftest.substrate_lock() directly, not through live_service, so it does not prove the fixture path holds the lock during substrate access. Sub-requirements that DO pass: stdlib-only fcntl.flock mechanism present (conftest.py:274-303); the 3 named tests pass in isolation at HEAD (pytest -k '...' => '3 passed, 280 deselected in 30.31s'); no non-stdlib imports (AST check: conftest.py imports only contextlib/fcntl/io/os/pathlib/shutil/sys/time/uuid + pytest + local auger); no changes outside tests/ (commit 0ddeeeb touches only tests/conftest.py and tests/test_auger.py).


## Summary

Judge Result: QA-AUGER-6

Stage tier1: PASS
    ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==

Stage tier2: FAIL
  INCOMPLETE
  ✗ conftest.py gains a stdlib-only cross-process serialization mechanism (fcntl.flock) so two concurrent pytest legs never hit the live DuckBrain substrate simultaneously; a regression test proves mutual exclusion; the 3 previously failing tests (test_recall_cli_scopes_semantic_hits_to_the_selected_project, test_ask_stores_a_different_question_after_answer_closes_the_first, test_ask_stores_nothing_when_jev_is_unreachable) pass in isolation at HEAD; no non-stdlib imports; no changes outside tests/.: The lock does NOT cover substrate access, so the criterion's central requirement ('two concurrent pytest legs never hit the live DuckBrain substrate simultaneously') is unmet. conftest.py:331-340 defines live_service as `with substrate_lock(), _test_transport_budget(): url = require_live()` then `yield url` OUTSIDE the with-block, so the flock is released before the fixture yields. ns_created (conftest.py:344) depends on live_service but runs namespace create, the test body, and teardown with NO lock held. substrate_lock is referenced only at conftest.py:338 (liveness probe only) and in the two regression tests; there is no autouse fixture. Empirically verified: (a) after the fixture yields, _SUBSTRATE_LOCK_FD is None / depth 0; (b) two concurrent processes emulating the fixture path both reached the 'test body' with lock_held_in_body=False at t=1790947750.464 and t=1790947750.465 (1ms apart) — not serialized. The docstring at conftest.py:347 falsely claims 'the cross-process substrate lock is held for the whole namespace lifecycle (create, test body, teardown)'. The regression test test_substrate_lock_serializes_two_processes (test_auger.py:9417) proves the lock PRIMITIVE works (2 passed in 1.05s) but calls conftest.substrate_lock() directly, not through live_service, so it does not prove the fixture path holds the lock during substrate access. Sub-requirements that DO pass: stdlib-only fcntl.flock mechanism present (conftest.py:274-303); the 3 named tests pass in isolation at HEAD (pytest -k '...' => '3 passed, 280 deselected in 30.31s'); no non-stdlib imports (AST check: conftest.py imports only contextlib/fcntl/io/os/pathlib/shutil/sys/time/uuid + pytest + local auger); no changes outside tests/ (commit 0ddeeeb touches only tests/conftest.py and tests/test_auger.py).


Overall: FAIL ✗
