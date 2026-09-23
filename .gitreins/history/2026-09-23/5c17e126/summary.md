# Verdict: AUG-050

**Task:** Prevent AUGER_CI from downgrading the dev gate
**Evaluated:** 2026-09-23T23:35:53.958357
**Result:** ✗ FAIL

## Pipeline Stages

- ✗ **tier1**
  -   ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✗ tests: FAIL (tests/test_auger.py::test_init_creates_a_namespace_the_api_then_lists [first failing id])
- ✓ **tier2**
  - COMPLETE
  ✓ A non-runner host with AUGER_CI=1 exported still runs live arms or fails loudly naming the downgrade; a dev-host gate reports the live arm count actually run.: Both halves verified by execution. (1) Non-runner host + AUGER_CI=1 fails loudly naming the downgrade: `env -u GITHUB_ACTIONS -u CI AUGER_CI=1 bash tests/gate.sh` -> exit_code 2, stderr 'ERROR: refusing AUGER_CI=1 downgrade on a non-runner host; require GITHUB_ACTIONS=true or CI=1/true.' (tests/gate.sh:26-31 select_gate_mode, invoked at :41 `GATE_MODE="$(select_gate_mode)" || exit $?`). It aborts BEFORE any arm — output contains no '== syntax ==' line. Bogus values also rejected: AUGER_CI=yes -> exit 2 'AUGER_CI must be 0 or 1 (got yes)'. (2) Dev-host gate reports the live arm count actually run: `env -u GITHUB_ACTIONS -u CI -u AUGER_CI bash tests/gate.sh` (arms stubbed for speed) -> 'LIVE ARMS: ran 2; skipped 0' + 'GATE PASS', exit_code 0; counters at tests/gate.sh:53-59, run_live_arm at :83/:91, summary printed at :95. Hosted-ci-skip mode prints 'LIVE ARMS: ran 0; skipped 2' and names pytest-live, e2e-smoke. Regression suite: `bash tests/test_gate_mode.sh` -> exit_code 0, 'gate mode regression: PASS' (tests/test_gate_mode.sh:20-33 asserts non-zero exit + 'non-runner' + 'downgrade' + absence of '== syntax =='; :36-44 assert GITHUB_ACTIONS=true and CI=1 both select hosted-ci-skip). CI wiring consistent: .github/workflows/ci.yml:42 sets AUGER_CI=1 (runner supplies GITHUB_ACTIONS/CI) and the opt-in live step at :79-81 sets AUGER_CI=0 so real arms run on a self-hosted runner. Note: the full unstubbed gate cannot complete on this eval host (no live DuckBrain/JEV) — the documented, expected condition, not a defect.
AUGER_CI=1 on a non-runner host is rejected loudly before any arm runs, and the dev-host gate reports the live arm count actually run; regression test passes.

## Summary

Judge Result: AUG-050

Stage tier1: FAIL
    ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✗ tests: FAIL (tests/test_auger.py::test_init_creates_a_namespace_the_api_then_lists [first failing id])

Stage tier2: PASS
  COMPLETE
  ✓ A non-runner host with AUGER_CI=1 exported still runs live arms or fails loudly naming the downgrade; a dev-host gate reports the live arm count actually run.: Both halves verified by execution. (1) Non-runner host + AUGER_CI=1 fails loudly naming the downgrade: `env -u GITHUB_ACTIONS -u CI AUGER_CI=1 bash tests/gate.sh` -> exit_code 2, stderr 'ERROR: refusing AUGER_CI=1 downgrade on a non-runner host; require GITHUB_ACTIONS=true or CI=1/true.' (tests/gate.sh:26-31 select_gate_mode, invoked at :41 `GATE_MODE="$(select_gate_mode)" || exit $?`). It aborts BEFORE any arm — output contains no '== syntax ==' line. Bogus values also rejected: AUGER_CI=yes -> exit 2 'AUGER_CI must be 0 or 1 (got yes)'. (2) Dev-host gate reports the live arm count actually run: `env -u GITHUB_ACTIONS -u CI -u AUGER_CI bash tests/gate.sh` (arms stubbed for speed) -> 'LIVE ARMS: ran 2; skipped 0' + 'GATE PASS', exit_code 0; counters at tests/gate.sh:53-59, run_live_arm at :83/:91, summary printed at :95. Hosted-ci-skip mode prints 'LIVE ARMS: ran 0; skipped 2' and names pytest-live, e2e-smoke. Regression suite: `bash tests/test_gate_mode.sh` -> exit_code 0, 'gate mode regression: PASS' (tests/test_gate_mode.sh:20-33 asserts non-zero exit + 'non-runner' + 'downgrade' + absence of '== syntax =='; :36-44 assert GITHUB_ACTIONS=true and CI=1 both select hosted-ci-skip). CI wiring consistent: .github/workflows/ci.yml:42 sets AUGER_CI=1 (runner supplies GITHUB_ACTIONS/CI) and the opt-in live step at :79-81 sets AUGER_CI=0 so real arms run on a self-hosted runner. Note: the full unstubbed gate cannot complete on this eval host (no live DuckBrain/JEV) — the documented, expected condition, not a defect.
AUGER_CI=1 on a non-runner host is rejected loudly before any arm runs, and the dev-host gate reports the live arm count actually run; regression test passes.

Overall: FAIL ✗
