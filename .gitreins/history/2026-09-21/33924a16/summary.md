# Verdict: AUG-013

**Task:** CI red on main: lint-gate version drift (unpinned ruff)
**Evaluated:** 2026-09-21T05:28:34.787690
**Result:** ✗ FAIL

## Pipeline Stages

- ✗ **tier1**
  -   ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✗ tests: == syntax ==
- ✓ **tier2**
  - COMPLETE
  ✓ GitHub Actions run on main is GREEN: ruff pinned in ci.yml and the 4 SIM115 sites in auger.py pass under both ruff 0.15.22 and 0.16.8; bash tests/gate.sh green locally: ci.yml:60 now pins `python3 -m pip install "ruff==0.15.22" pytest` (was unpinned), matching the host ruff 0.15.22 (`ruff --version` -> ruff 0.15.22), so CI and local lint mean the same thing. Ruff 0.15.22: `ruff check auger.py` -> 'All checks passed!' exit=0. Ruff 0.16.8 (fresh venv /tmp/r16): `ruff check auger.py` -> 'All checks passed!' exit=0, and `ruff check --select SIM115 auger.py` -> 'All checks passed!' exit=0. The pre-fix code (git show HEAD~1:auger.py) under 0.16.8 `--select SIM115` -> 'Found 4 errors' exit=1, confirming exactly the 4 SIM115 sites were the drift; all 4 are now with-blocks (auger.py:707-712 table-decl writer, 738-741 cmd_start --seed-file, 996-997 cmd_dump --out) with semantics preserved (seed_file still wins over --seed; decl writer still only writes on change). Local gate: `AUGER_CI=1 bash tests/gate.sh` -> GATE_EXIT=0, output '== lint == All checks passed!', '35 passed in 38.07s', 'GATE PASS' (e2e-smoke SKIPPED as expected — no live DuckBrain on a GitHub runner). ci.yml parses as valid YAML with the single gate job and 6 steps; LSP diagnostics: 0. All arms green.
ruff is pinned to 0.15.22 in ci.yml, all 4 SIM115 sites in auger.py pass under both ruff 0.15.22 and 0.16.8, and bash tests/gate.sh exits 0 with GATE PASS locally.

## Summary

Judge Result: AUG-013

Stage tier1: FAIL
    ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✗ tests: == syntax ==

Stage tier2: PASS
  COMPLETE
  ✓ GitHub Actions run on main is GREEN: ruff pinned in ci.yml and the 4 SIM115 sites in auger.py pass under both ruff 0.15.22 and 0.16.8; bash tests/gate.sh green locally: ci.yml:60 now pins `python3 -m pip install "ruff==0.15.22" pytest` (was unpinned), matching the host ruff 0.15.22 (`ruff --version` -> ruff 0.15.22), so CI and local lint mean the same thing. Ruff 0.15.22: `ruff check auger.py` -> 'All checks passed!' exit=0. Ruff 0.16.8 (fresh venv /tmp/r16): `ruff check auger.py` -> 'All checks passed!' exit=0, and `ruff check --select SIM115 auger.py` -> 'All checks passed!' exit=0. The pre-fix code (git show HEAD~1:auger.py) under 0.16.8 `--select SIM115` -> 'Found 4 errors' exit=1, confirming exactly the 4 SIM115 sites were the drift; all 4 are now with-blocks (auger.py:707-712 table-decl writer, 738-741 cmd_start --seed-file, 996-997 cmd_dump --out) with semantics preserved (seed_file still wins over --seed; decl writer still only writes on change). Local gate: `AUGER_CI=1 bash tests/gate.sh` -> GATE_EXIT=0, output '== lint == All checks passed!', '35 passed in 38.07s', 'GATE PASS' (e2e-smoke SKIPPED as expected — no live DuckBrain on a GitHub runner). ci.yml parses as valid YAML with the single gate job and 6 steps; LSP diagnostics: 0. All arms green.
ruff is pinned to 0.15.22 in ci.yml, all 4 SIM115 sites in auger.py pass under both ruff 0.15.22 and 0.16.8, and bash tests/gate.sh exits 0 with GATE PASS locally.

Overall: FAIL ✗
