# Verdict: AUG-021

**Task:** gitreins hook test_timeout (600s) no longer fits the merged-tree battery (~810s under load) — legit commits blocked
**Evaluated:** 2026-09-22T21:19:09.612377
**Result:** ✓ PASS

## Pipeline Stages

- ✓ **tier1**
  -   ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==
- ✓ **tier2**
  - COMPLETE
  ✓ test_timeout in .gitreins/config.yaml is >= 900s and hook_timeout exceeds it, so the full gate battery completes without being cut short; a commit passes the hook without --no-verify: .gitreins/config.yaml:20 `test_timeout: 900` (>=900) and :32 `hook_timeout: 1200` (>900); git show HEAD confirms 600->900 and 900->1200. Engine reads both: engine/guard_manager.py:931-938 (_coerce_timeout for test_timeout/hook_timeout), tests arm uses self._test_timeout (line 1822), outer budget _timed_out() compares elapsed >= self._hook_timeout (line 987), so 1200>900 means no arm is cut short. LIVE PROOF 1: `gitreins guard` (the exact command in .git/hooks/pre-commit) exited 0 in 293s with 'Tier 1: DEGRADED PASS ... ✓ tests (full)' and guard log 'passed 21, failed 0 / GATE PASS' — full battery completed. LIVE PROOF 2: real `git commit` through the pre-commit hook WITHOUT --no-verify succeeded: '[main 1f43560] test: AUG-021 hook verification ... COMMIT_EXIT=0 WALL=201'. Test commit reset via git reset --soft HEAD~1 (tree back at b720a45). allow_skips: true is present so the lint skip is a DEGRADED PASS exiting 0 (cli.py:2050) rather than exit 2. Margins: 90s headroom over the measured 810s; 300s hook headroom over test_timeout.
test_timeout raised to 900s with hook_timeout 1200s, and a real commit passed the pre-commit hook without --no-verify (exit 0, full 21-assertion gate battery completed).

## Summary

Judge Result: AUG-021

Stage tier1: PASS
    ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==

Stage tier2: PASS
  COMPLETE
  ✓ test_timeout in .gitreins/config.yaml is >= 900s and hook_timeout exceeds it, so the full gate battery completes without being cut short; a commit passes the hook without --no-verify: .gitreins/config.yaml:20 `test_timeout: 900` (>=900) and :32 `hook_timeout: 1200` (>900); git show HEAD confirms 600->900 and 900->1200. Engine reads both: engine/guard_manager.py:931-938 (_coerce_timeout for test_timeout/hook_timeout), tests arm uses self._test_timeout (line 1822), outer budget _timed_out() compares elapsed >= self._hook_timeout (line 987), so 1200>900 means no arm is cut short. LIVE PROOF 1: `gitreins guard` (the exact command in .git/hooks/pre-commit) exited 0 in 293s with 'Tier 1: DEGRADED PASS ... ✓ tests (full)' and guard log 'passed 21, failed 0 / GATE PASS' — full battery completed. LIVE PROOF 2: real `git commit` through the pre-commit hook WITHOUT --no-verify succeeded: '[main 1f43560] test: AUG-021 hook verification ... COMMIT_EXIT=0 WALL=201'. Test commit reset via git reset --soft HEAD~1 (tree back at b720a45). allow_skips: true is present so the lint skip is a DEGRADED PASS exiting 0 (cli.py:2050) rather than exit 2. Margins: 90s headroom over the measured 810s; 300s hook headroom over test_timeout.
test_timeout raised to 900s with hook_timeout 1200s, and a real commit passed the pre-commit hook without --no-verify (exit 0, full 21-assertion gate battery completed).

Overall: PASS ✓
