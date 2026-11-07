# Verdict: AUG-116

**Task:** Fix ci-act cell grading and evidence
**Evaluated:** 2026-10-30T21:48:38.182667
**Result:** ✗ FAIL

## Pipeline Stages

- ✗ **tier1**
  -   ✗ tests: scanners: nice=nice -n 10
  ✓ lint: scanners: nice=nice -n 10
  ✗ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
- ✓ **tier2**
  - COMPLETE
  ✓ tests/gate.sh on a box with no DuckBrain/JEV fails FAST with a named cause: run bash tests/gate.sh with DUCKBRAIN_URL unset and assert rc!=0 within 20 s with a message naming the missing prerequisite (DuckBrain URL/key), matching the closed QA-AUGER-14 precedent. (Row AC2 live-fail-fast branch; AC1 bunker-qa evidence probe is externally blocked on QA-AUGER-16 (bunker-mvp cannot spawn agents); AC3 is the hosted-CI green check.): Ran `env -u DUCKBRAIN_URL -u DUCKBRAIN_API_KEY -u AUGER_CI bash tests/gate.sh` in /home/kara/auger: exit_code=2, ELAPSED_MS=10 (well under 20 s), stderr = 'ERROR: DuckBrain URL missing — set DUCKBRAIN_URL before running the live gate (or use AUGER_CI=1 on hosted CI).'; stdout empty, i.e. no gate arm started (no '== syntax =='). Implementation: tests/gate.sh:52-70 `require_live_prerequisites()` checks DUCKBRAIN_URL, then DUCKBRAIN_API_KEY / ~/.duckbrain/foreman-status.token / ~/.duckbrain/token, then OPENROUTER_API_KEY / OR_API_KEY / ~/.hermes/.env, each with a named ERROR; `if ! require_live_prerequisites; then exit 2; fi` runs before the doc-only fast path and every arm. Key branch also verified: with DUCKBRAIN_URL set and no key the gate prints 'ERROR: DuckBrain API key missing — set DUCKBRAIN_API_KEY or provide ~/.duckbrain/foreman-status.token...' (observed in the second run, rc=1 after arms, and asserted by the new negative controls). Regression test `bash tests/test_gate_mode.sh` -> RC=0, output 'gate prerequisite preflight: PASS' / 'AUG-081 wrapper regression: PASS'; tests/test_gate_mode.sh:79-107 isolates HOME via `env -i` + mktemp HOME and asserts exit 2, absence of '== syntax ==', and each of 'DuckBrain URL missing', 'DuckBrain API key missing', 'JEV/OpenRouter key missing'. Landed in commit 2707973 ('fix(AUG-116): live gate fails fast with a named missing prerequisite').
tests/gate.sh now exits 2 in ~10 ms with a named 'DuckBrain URL missing' (and API-key/JEV-key) message before any gate arm, verified by direct run and by the new isolated negative controls in tests/test_gate_mode.sh (RC=0).

## Summary

Judge Result: AUG-116

Stage tier1: FAIL
    ✗ tests: scanners: nice=nice -n 10
  ✓ lint: scanners: nice=nice -n 10
  ✗ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)

Stage tier2: PASS
  COMPLETE
  ✓ tests/gate.sh on a box with no DuckBrain/JEV fails FAST with a named cause: run bash tests/gate.sh with DUCKBRAIN_URL unset and assert rc!=0 within 20 s with a message naming the missing prerequisite (DuckBrain URL/key), matching the closed QA-AUGER-14 precedent. (Row AC2 live-fail-fast branch; AC1 bunker-qa evidence probe is externally blocked on QA-AUGER-16 (bunker-mvp cannot spawn agents); AC3 is the hosted-CI green check.): Ran `env -u DUCKBRAIN_URL -u DUCKBRAIN_API_KEY -u AUGER_CI bash tests/gate.sh` in /home/kara/auger: exit_code=2, ELAPSED_MS=10 (well under 20 s), stderr = 'ERROR: DuckBrain URL missing — set DUCKBRAIN_URL before running the live gate (or use AUGER_CI=1 on hosted CI).'; stdout empty, i.e. no gate arm started (no '== syntax =='). Implementation: tests/gate.sh:52-70 `require_live_prerequisites()` checks DUCKBRAIN_URL, then DUCKBRAIN_API_KEY / ~/.duckbrain/foreman-status.token / ~/.duckbrain/token, then OPENROUTER_API_KEY / OR_API_KEY / ~/.hermes/.env, each with a named ERROR; `if ! require_live_prerequisites; then exit 2; fi` runs before the doc-only fast path and every arm. Key branch also verified: with DUCKBRAIN_URL set and no key the gate prints 'ERROR: DuckBrain API key missing — set DUCKBRAIN_API_KEY or provide ~/.duckbrain/foreman-status.token...' (observed in the second run, rc=1 after arms, and asserted by the new negative controls). Regression test `bash tests/test_gate_mode.sh` -> RC=0, output 'gate prerequisite preflight: PASS' / 'AUG-081 wrapper regression: PASS'; tests/test_gate_mode.sh:79-107 isolates HOME via `env -i` + mktemp HOME and asserts exit 2, absence of '== syntax ==', and each of 'DuckBrain URL missing', 'DuckBrain API key missing', 'JEV/OpenRouter key missing'. Landed in commit 2707973 ('fix(AUG-116): live gate fails fast with a named missing prerequisite').
tests/gate.sh now exits 2 in ~10 ms with a named 'DuckBrain URL missing' (and API-key/JEV-key) message before any gate arm, verified by direct run and by the new isolated negative controls in tests/test_gate_mode.sh (RC=0).

Overall: FAIL ✗
