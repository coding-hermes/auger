# Verdict: AUG-004

**Task:** seed the 44-domain grid as real rows with triage scores, and report coverage and depth per domain
**Evaluated:** 2026-09-22T22:04:00.846686
**Result:** ✗ FAIL

## Pipeline Stages

- ✓ **tier1**
  -   ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==
- ✗ **tier2**
  - INCOMPLETE
  ✗ A run seeds domains 4.01-4.44 as real domain rows (44 rows) in a live namespace; status prints per-domain coverage and reports a domain with no rows as absent rather than silently omitted; terminating ring is reported per domain: Criterion 1 (AUG-004): PASS so far.
- Live run: `python3 auger.py -n eval-aug004-1790112886 init --seed-domains` -> "domain grid: 44 domains -> 44 row(s) written, 0 already present", ids DM-000001..DM-000044.
- `status` on seeded ns: "domain grid coverage (44 in the grid | 44 seeded | 0 absent)" + 44 per-domain lines each with triage/floor/terminating ring.
- `status` on unseeded ns: "44 in the grid | 0 seeded | 44 absent", all 44 domains printed ABSENT (not omitted), each with terminating ring.
- Targeted pytest: `python3 -m pytest tests/test_auger.py -q -k "grid or domain or seed"` -> 10 passed, 90 deselected in 5.69s.
- Full gate running in background (/tmp/gate.log).
Partial verdict — evaluation hit resource cap before all criteria verified

## Summary

Judge Result: AUG-004

Stage tier1: PASS
    ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==

Stage tier2: FAIL
  INCOMPLETE
  ✗ A run seeds domains 4.01-4.44 as real domain rows (44 rows) in a live namespace; status prints per-domain coverage and reports a domain with no rows as absent rather than silently omitted; terminating ring is reported per domain: Criterion 1 (AUG-004): PASS so far.
- Live run: `python3 auger.py -n eval-aug004-1790112886 init --seed-domains` -> "domain grid: 44 domains -> 44 row(s) written, 0 already present", ids DM-000001..DM-000044.
- `status` on seeded ns: "domain grid coverage (44 in the grid | 44 seeded | 0 absent)" + 44 per-domain lines each with triage/floor/terminating ring.
- `status` on unseeded ns: "44 in the grid | 0 seeded | 44 absent", all 44 domains printed ABSENT (not omitted), each with terminating ring.
- Targeted pytest: `python3 -m pytest tests/test_auger.py -q -k "grid or domain or seed"` -> 10 passed, 90 deselected in 5.69s.
- Full gate running in background (/tmp/gate.log).
Partial verdict — evaluation hit resource cap before all criteria verified

Overall: FAIL ✗
