# Verdict: AUG-211

**Task:** Judge-named uncovered failure paths in AUG-209 lock classification
**Evaluated:** 2026-11-26T04:41:54.588855
**Result:** ✗ FAIL (DEGRADED — skipped checks)

## Pipeline Stages

- ✓ **tier1**
  -   ✓ lint: scanners: nice=nice -n 10
  ✓ secrets: secrets: scanners=gitleaks+builtin cross-check
  ✓ tests: scanners: nice=nice -n 10
- ✗ **tier2**
  - INCOMPLETE
  ✗ Coverage FAILED — ungraded: AC1: run_cli_exit converts a SubstrateLockContended raised inside a refusal-asserting test into an attributed skip naming the lock (committed test proves the skip shape). AC2: tests/gate.sh pytest arm gains a skip-count bound that reports a lock storm loudly instead of exiting 0 green; ceiling and loud line visible and exercised by a committed test. AC3: scripts/audit_board_evidence.py passes and the shipped typed_contention/still_fails_loudly/lock_code_on_any_other/named_skip subset stays green.
Evaluator error: LLM call failed: 403 Client Error: Forbidden for url: https://openrouter.ai/api/v1/chat/completions (provider=openai model=deepseek/deepseek-v4.1-flash-1017 url=https://openrouter.ai/api/v1/chat/completions key=<GITREINS_LLM_API_KEY>)
- ✓ **fix_verification**
  - fix_verification: 0 finding(s) verified

## Summary

Stage tier1: PASS
    ✓ lint: scanners: nice=nice -n 10
  ✓ secrets: secrets: scanners=gitleaks+builtin cross-check
  ✓ tests: scanners: nice=nice -n 10

Stage tier2: FAIL
  INCOMPLETE
  ✗ Coverage FAILED — ungraded: AC1: run_cli_exit converts a SubstrateLockContended raised inside a refusal-asserting test into an attributed skip naming the lock (committed test proves the skip shape). AC2: tests/gate.sh pytest arm gains a skip-count bound that reports a lock storm loudly instead of exiting 0 green; ceiling and loud line visible and exercised by a committed test. AC3: scripts/audit_board_evidence.py passes and the shipped typed_contention/still_fails_loudly/lock_code_on_any_other/named_skip subset stays green.
Evaluator error: LLM call failed: 403 Client Error: Forbidden for url: https://openrouter.ai/api/v1/chat/completions (provider=openai model=deepseek/deepseek-v4.1-flash-1017 url=https://openrouter.ai/api/v1/chat/completions key=<GITREINS_LLM_API_KEY>)

Stage fix_verification: PASS
  fix_verification: 0 finding(s) verified

Overall: FAIL ✗
