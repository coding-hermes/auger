# Verdict: DOC-003

**Task:** Document DuckBrain auth resolution in VERBS.md shared conventions
**Evaluated:** 2026-09-29T20:59:33.905240
**Result:** ✓ PASS

## Pipeline Stages

- ✓ **tier1**
  -   ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==
- ✓ **tier2**
  - COMPLETE
  ✓ docs/VERBS.md conventions block documents the full auth resolution: DUCKBRAIN_API_KEY env first, then ~/.duckbrain/foreman-status.token, then ~/.duckbrain/token, in that order, plus what a fresh install must create and the exact refusal line (verified against auger.py:105-109,349-361): docs/VERBS.md:29-35 conventions block states: 'the DUCKBRAIN_API_KEY environment variable wins; if it is unset or empty, the token files ~/.duckbrain/foreman-status.token then ~/.duckbrain/token are tried in that order and the first non-empty one is used.' Verified against code: auger.py:107-110 TOKEN_PATHS=[~/.duckbrain/foreman-status.token, ~/.duckbrain/token] (correct order); auger.py:455 checks DUCKBRAIN_API_KEY first; auger.py:458 iterates TOKEN_PATHS returning first non-empty. Fresh-install requirement documented: 'Nothing creates these files for you — a fresh install does not ship with ~/.duckbrain/foreman-status.token ... so set the env var or write one of the token files yourself.' Exact refusal line byte-identical: grep confirms auger.py:467 and docs/VERBS.md:35 both read 'no DuckBrain token: set DUCKBRAIN_API_KEY or ~/.duckbrain/foreman-status.token'. (Criterion's cited line numbers 105-109/349-361 have drifted; actual code is at 107-110/454-468, but content matches exactly.)
docs/VERBS.md conventions block fully and accurately documents the DuckBrain auth resolution order, fresh-install requirement, and exact refusal line, verified byte-for-byte against auger.py.

## Summary

Judge Result: DOC-003

Stage tier1: PASS
    ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==

Stage tier2: PASS
  COMPLETE
  ✓ docs/VERBS.md conventions block documents the full auth resolution: DUCKBRAIN_API_KEY env first, then ~/.duckbrain/foreman-status.token, then ~/.duckbrain/token, in that order, plus what a fresh install must create and the exact refusal line (verified against auger.py:105-109,349-361): docs/VERBS.md:29-35 conventions block states: 'the DUCKBRAIN_API_KEY environment variable wins; if it is unset or empty, the token files ~/.duckbrain/foreman-status.token then ~/.duckbrain/token are tried in that order and the first non-empty one is used.' Verified against code: auger.py:107-110 TOKEN_PATHS=[~/.duckbrain/foreman-status.token, ~/.duckbrain/token] (correct order); auger.py:455 checks DUCKBRAIN_API_KEY first; auger.py:458 iterates TOKEN_PATHS returning first non-empty. Fresh-install requirement documented: 'Nothing creates these files for you — a fresh install does not ship with ~/.duckbrain/foreman-status.token ... so set the env var or write one of the token files yourself.' Exact refusal line byte-identical: grep confirms auger.py:467 and docs/VERBS.md:35 both read 'no DuckBrain token: set DUCKBRAIN_API_KEY or ~/.duckbrain/foreman-status.token'. (Criterion's cited line numbers 105-109/349-361 have drifted; actual code is at 107-110/454-468, but content matches exactly.)
docs/VERBS.md conventions block fully and accurately documents the DuckBrain auth resolution order, fresh-install requirement, and exact refusal line, verified byte-for-byte against auger.py.

Overall: PASS ✓
