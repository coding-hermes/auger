# Verdict: AUG-074

**Task:** init resolves the substrate namespaces path and refuses a green 0/N declared tally
**Evaluated:** 2026-09-26T15:44:49.643701
**Result:** ✓ PASS

## Pipeline Stages

- ✓ **tier1**
  -   ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==
- ✓ **tier2**
  - COMPLETE
  ✓ ns_dir resolves the namespaces base from DUCKBRAIN_NAMESPACES_PATH when it is set and non-empty, and falls back to the legacy ~/duckbrain/namespaces otherwise: auger.py:325 defines NAMESPACES_PATH_ENV="DUCKBRAIN_NAMESPACES_PATH"; auger.py:328-332 ns_dir() does base=os.environ.get(NAMESPACES_PATH_ENV,"").strip(); if not base: base=os.path.join(os.path.expanduser("~"),"duckbrain","namespaces"); return os.path.join(base,ns). The .strip() makes a whitespace-only value fall back too. Tests test_ns_dir_honors_the_namespaces_path_override (tests/test_auger.py:190-193) and test_ns_dir_falls_back_to_the_legacy_default_without_the_override (:196-201) both PASS.
  ✓ when the API serves 0 of the declared tables, init exits non-zero with a message naming the resolved declarations directory and the DUCKBRAIN_NAMESPACES_PATH knob — a misplaced-declarations run can never print a green init: auger.py:3231-3239: `if not have:` raises SystemExit(f"declared: 0/{len(COLS)} tables — nothing visible to the API under {d}; namespaces base resolved from {NAMESPACES_PATH_ENV}"), where d=os.path.join(ns_dir(ns),"tables") (auger.py:3198) is the resolved declarations dir. The raise happens before any success print, so a 0/N run cannot print green. test_init_refuses_a_zero_declared_tally (tests/test_auger.py:204-241) asserts code==1, "declared: 0/" in msg, auger.ns_dir(ns) in msg, and auger.NAMESPACES_PATH_ENV in msg — PASSED.
  ✓ the ordinary path is unchanged: init against a default-base substrate declares 14/14 tables and exits 0: auger.py:114 COLS has 14 entries (verified: python -c "import auger; print(len(auger.COLS))" -> 14). tests/test_auger.py:181-183 test_init_tolerates_namespace_created_before_init asserts rc==0 and "declared: 14/14 tables" in out against a live default-base substrate; it PASSED. Full gate `bash tests/gate.sh` -> "212 passed in 1708.51s", smoke "passed 21, failed 0", "GATE PASS".
  ✓ regression tests cover the override, the fallback and the 0/N refusal, and they pass on the current tree: tests/test_auger.py:190-193 (override), :196-201 (fallback), :204-241 (0/N refusal), :244-259 (partial-tally still exit 0). Ran `python -m pytest tests/test_auger.py -k "ns_dir or zero_declared or partial_tally" -q` -> "4 passed, 208 deselected in 0.09s". Full gate `bash tests/gate.sh` -> syntax OK, ruff "All checks passed!", "212 passed in 1708.51s (0:28:28)", smoke "passed 21, failed 0", "LIVE ARMS: ran 2; skipped 0", "GATE PASS".
All four AUG-074 criteria are implemented in auger.py (ns_dir env override + fallback, 0/N SystemExit refusal naming the resolved dir and knob) and covered by passing regression tests; the full gate is green (212 passed, smoke 21/21, GATE PASS).

## Summary

Judge Result: AUG-074

Stage tier1: PASS
    ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==

Stage tier2: PASS
  COMPLETE
  ✓ ns_dir resolves the namespaces base from DUCKBRAIN_NAMESPACES_PATH when it is set and non-empty, and falls back to the legacy ~/duckbrain/namespaces otherwise: auger.py:325 defines NAMESPACES_PATH_ENV="DUCKBRAIN_NAMESPACES_PATH"; auger.py:328-332 ns_dir() does base=os.environ.get(NAMESPACES_PATH_ENV,"").strip(); if not base: base=os.path.join(os.path.expanduser("~"),"duckbrain","namespaces"); return os.path.join(base,ns). The .strip() makes a whitespace-only value fall back too. Tests test_ns_dir_honors_the_namespaces_path_override (tests/test_auger.py:190-193) and test_ns_dir_falls_back_to_the_legacy_default_without_the_override (:196-201) both PASS.
  ✓ when the API serves 0 of the declared tables, init exits non-zero with a message naming the resolved declarations directory and the DUCKBRAIN_NAMESPACES_PATH knob — a misplaced-declarations run can never print a green init: auger.py:3231-3239: `if not have:` raises SystemExit(f"declared: 0/{len(COLS)} tables — nothing visible to the API under {d}; namespaces base resolved from {NAMESPACES_PATH_ENV}"), where d=os.path.join(ns_dir(ns),"tables") (auger.py:3198) is the resolved declarations dir. The raise happens before any success print, so a 0/N run cannot print green. test_init_refuses_a_zero_declared_tally (tests/test_auger.py:204-241) asserts code==1, "declared: 0/" in msg, auger.ns_dir(ns) in msg, and auger.NAMESPACES_PATH_ENV in msg — PASSED.
  ✓ the ordinary path is unchanged: init against a default-base substrate declares 14/14 tables and exits 0: auger.py:114 COLS has 14 entries (verified: python -c "import auger; print(len(auger.COLS))" -> 14). tests/test_auger.py:181-183 test_init_tolerates_namespace_created_before_init asserts rc==0 and "declared: 14/14 tables" in out against a live default-base substrate; it PASSED. Full gate `bash tests/gate.sh` -> "212 passed in 1708.51s", smoke "passed 21, failed 0", "GATE PASS".
  ✓ regression tests cover the override, the fallback and the 0/N refusal, and they pass on the current tree: tests/test_auger.py:190-193 (override), :196-201 (fallback), :204-241 (0/N refusal), :244-259 (partial-tally still exit 0). Ran `python -m pytest tests/test_auger.py -k "ns_dir or zero_declared or partial_tally" -q` -> "4 passed, 208 deselected in 0.09s". Full gate `bash tests/gate.sh` -> syntax OK, ruff "All checks passed!", "212 passed in 1708.51s (0:28:28)", smoke "passed 21, failed 0", "LIVE ARMS: ran 2; skipped 0", "GATE PASS".
All four AUG-074 criteria are implemented in auger.py (ns_dir env override + fallback, 0/N SystemExit refusal naming the resolved dir and knob) and covered by passing regression tests; the full gate is green (212 passed, smoke 21/21, GATE PASS).

Overall: PASS ✓
