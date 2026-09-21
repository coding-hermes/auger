# Verdict: AUG-006

**Task:** a pytest suite against an ephemeral namespace, replacing shell-only coverage
**Evaluated:** 2026-09-21T00:55:42.343610
**Result:** ✓ PASS

## Pipeline Stages

- ✓ **tier1**
  -   ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==
- ✓ **tier2**
  - COMPLETE
  ✓ pytest runs green from a clean checkout against a live ephemeral DuckBrain namespace: Fresh, cache-defeating run in /home/kara/auger: `python3 -m pytest tests/test_auger.py -q -p no:cacheprovider` -> "27 passed in 74.96s" (0 failed, 0 skipped; 27 tests collected). The repo gate also ran: /tmp/gate.log shows "== pytest == 27 passed in 51.87s" and "GATE PASS". Live service confirmed: `curl http://127.0.0.1:3000/api/namespaces` -> 401 (up, auth required) and `auger.db('/api/namespaces')` -> 200. Namespaces are ephemeral per test (conftest.py:169-196, TEST_NS_PREFIX + uuid4).
  ✓ the suite asserts on STORED ROWS (re-read via the API), not only on stdout: tests/test_auger.py:75-88 defines rows()/row()/option_flags() on top of auger.select (auger.py:191 -> HTTP GET /api/ns/<ns>/tables/<table>), and they are the assertion basis throughout: :140 (project row), :167/:180/:186 (decision + option rows), :197, :208, :220 (status counts stored rows), :243-253 (dump --config non-mutation on stored flags), :263, :297-327 (toggle flips re-read flags), :426-431, :448-462. Also auger.recall at :159 and the on-disk JSONL at :151-157. Not stdout-only.
  ✓ the namespace is torn down even when a test fails: Empirically proven: I added a temporary test using the `ns` fixture that asserts False. Output: "NS_UNDER_TEST=auger-pytest-cc40aae93699 ... 1 failed in 0.09s". Immediately after: `ls -d ~/duckbrain/namespaces/auger-pytest-cc40aae93699` -> No such file or directory; `grep auger-pytest-cc40aae93699 ~/duckbrain/duckbrain.config.json` -> exit 1 (no entry); API /api/namespaces -> 200 with [] for that name. Teardown ran despite the failure. Mechanism: conftest.py:186-196 finalizer calls teardown_namespace in a `finally` (both DELETE /api/namespaces/<ns> and shutil.rmtree), and test_auger.py:522-533 audits leaks scoped to CREATED. Temp test file removed afterwards; working tree clean.
  ✓ a deliberately broken verb fails exactly one named test: Ran `python3 -m pytest tests/test_auger.py -q -p no:cacheprovider -k broken_verb -s` (test_auger.py:472-505). The child suite (BROKEN_VERB_SUITE, auger.patch = lambda: {"updated": 0}) reported "1 failed, 3 passed in 0.62s" with exactly one FAILED line: `FAILED test_broken_3c365744.py::test_alpha_toggle_flips_the_stored_flag - AssertionError: the toggle reported success but wrote nothing`. The outer test passed ("1 passed, 26 deselected"), and it asserts "1 failed, 3 passed", exactly one FAILED line, and that the failure names the toggle test.
All four criteria verified with fresh command output: 27 pytest tests pass against a live ephemeral DuckBrain namespace, assertions are on API-re-read stored rows, teardown provably runs on failure (namespace gone from disk/config/API), and a deliberately broken toggle verb fails exactly one named test.

## Summary

Judge Result: AUG-006

Stage tier1: PASS
    ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==

Stage tier2: PASS
  COMPLETE
  ✓ pytest runs green from a clean checkout against a live ephemeral DuckBrain namespace: Fresh, cache-defeating run in /home/kara/auger: `python3 -m pytest tests/test_auger.py -q -p no:cacheprovider` -> "27 passed in 74.96s" (0 failed, 0 skipped; 27 tests collected). The repo gate also ran: /tmp/gate.log shows "== pytest == 27 passed in 51.87s" and "GATE PASS". Live service confirmed: `curl http://127.0.0.1:3000/api/namespaces` -> 401 (up, auth required) and `auger.db('/api/namespaces')` -> 200. Namespaces are ephemeral per test (conftest.py:169-196, TEST_NS_PREFIX + uuid4).
  ✓ the suite asserts on STORED ROWS (re-read via the API), not only on stdout: tests/test_auger.py:75-88 defines rows()/row()/option_flags() on top of auger.select (auger.py:191 -> HTTP GET /api/ns/<ns>/tables/<table>), and they are the assertion basis throughout: :140 (project row), :167/:180/:186 (decision + option rows), :197, :208, :220 (status counts stored rows), :243-253 (dump --config non-mutation on stored flags), :263, :297-327 (toggle flips re-read flags), :426-431, :448-462. Also auger.recall at :159 and the on-disk JSONL at :151-157. Not stdout-only.
  ✓ the namespace is torn down even when a test fails: Empirically proven: I added a temporary test using the `ns` fixture that asserts False. Output: "NS_UNDER_TEST=auger-pytest-cc40aae93699 ... 1 failed in 0.09s". Immediately after: `ls -d ~/duckbrain/namespaces/auger-pytest-cc40aae93699` -> No such file or directory; `grep auger-pytest-cc40aae93699 ~/duckbrain/duckbrain.config.json` -> exit 1 (no entry); API /api/namespaces -> 200 with [] for that name. Teardown ran despite the failure. Mechanism: conftest.py:186-196 finalizer calls teardown_namespace in a `finally` (both DELETE /api/namespaces/<ns> and shutil.rmtree), and test_auger.py:522-533 audits leaks scoped to CREATED. Temp test file removed afterwards; working tree clean.
  ✓ a deliberately broken verb fails exactly one named test: Ran `python3 -m pytest tests/test_auger.py -q -p no:cacheprovider -k broken_verb -s` (test_auger.py:472-505). The child suite (BROKEN_VERB_SUITE, auger.patch = lambda: {"updated": 0}) reported "1 failed, 3 passed in 0.62s" with exactly one FAILED line: `FAILED test_broken_3c365744.py::test_alpha_toggle_flips_the_stored_flag - AssertionError: the toggle reported success but wrote nothing`. The outer test passed ("1 passed, 26 deselected"), and it asserts "1 failed, 3 passed", exactly one FAILED line, and that the failure names the toggle test.
All four criteria verified with fresh command output: 27 pytest tests pass against a live ephemeral DuckBrain namespace, assertions are on API-re-read stored rows, teardown provably runs on failure (namespace gone from disk/config/API), and a deliberately broken toggle verb fails exactly one named test.

Overall: PASS ✓
