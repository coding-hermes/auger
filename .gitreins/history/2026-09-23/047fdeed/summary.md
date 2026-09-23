# Verdict: AUG-004

**Task:** seed the 44-domain grid as real rows with triage scores, and report coverage and depth per domain
**Evaluated:** 2026-09-23T06:40:47.360706
**Result:** ✗ FAIL

## Pipeline Stages

- ✗ **tier1**
  -   ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✗ tests: Command timed out
- ✓ **tier2**
  - COMPLETE
  ✓ A run seeds domains 4.01-4.44 as real domain rows (44 rows) in a live namespace; status prints per-domain coverage and reports a domain with no rows as absent rather than silently omitted; terminating ring is reported per domain: All three sub-requirements verified live AND by a fresh full gate. (1) SEEDING: `python3 auger.py -n auger-eval-verify-742960 init --seed-domains` -> "domain grid: 44 domains -> 44 row(s) written, 0 already present", ids DM-000001..DM-000044; direct read `auger.select(ns,'domain','')` -> "stored domain rows: 44", nums 4.01..4.44 (code: auger.py:2461 seed_domains(), auger.py:2702-2709 cmd_init). (2) PER-DOMAIN COVERAGE: status prints "domain grid coverage (44 in the grid | 44 seeded | 0 absent)" + "answered 0 ... | NOT-REACHED 44"; `grep -cE '^  4\.[0-9]{2} '` -> 44 lines, one per domain (code: auger.py:3390-3415, domain_line() auger.py:2605-2640). (3) ABSENT NOT OMITTED: moved 4.05 off-grid -> "44 in the grid | 43 seeded | 1 absent", line "  4.05  data  ABSENT  triage 27 floor 5 terminating ring 5", plus "ABSENT (a grid domain with no stored `domain` row ...): 4.05" and "OFF-GRID rows ...: 9.99" (code: auger.py:2622 state="ABSENT", auger.py:3416-3421). (4) TERMINATING RING PER DOMAIN: 4.01 "terminating ring none (not opened)", 4.04/4.05/4.44 "terminating ring 5" (code: auger.py:2626-2630, 2640). FRESH GATE EVIDENCE (test_command `bash tests/gate.sh`, run detached via setsid): "== syntax ==" OK, "== lint == All checks passed!", "== pytest == 104 passed in 221.49s (0:03:41)", smoke "passed 21, failed 0", "GATE PASS", "GATE_EXIT=0" (gate.sh exits 1 on any arm failure at lines 21/25/37/39/46 and prints GATE PASS only at line 49). AUG-004 tests green: tests/test_auger.py:3634 test_seeding_writes_the_44_domain_rows_with_the_grids_own_numbers, :3681 test_reseeding_the_grid_adds_no_second_copy, :3709 test_status_names_every_absent_grid_domain_when_no_row_is_stored, :3730 test_status_reports_coverage_and_the_terminating_ring_per_domain, :3767 test_a_domain_whose_row_is_gone_is_reported_absent_not_skipped.
AUG-004 criterion 1 passes: 44 real domain rows seed into a live namespace, status prints per-domain coverage with the terminating ring, and a domain with no row is named ABSENT rather than silently omitted — confirmed live and by a fresh full gate (104 passed, smoke 21/0, GATE PASS, exit 0).

## Summary

Judge Result: AUG-004

Stage tier1: FAIL
    ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✗ tests: Command timed out

Stage tier2: PASS
  COMPLETE
  ✓ A run seeds domains 4.01-4.44 as real domain rows (44 rows) in a live namespace; status prints per-domain coverage and reports a domain with no rows as absent rather than silently omitted; terminating ring is reported per domain: All three sub-requirements verified live AND by a fresh full gate. (1) SEEDING: `python3 auger.py -n auger-eval-verify-742960 init --seed-domains` -> "domain grid: 44 domains -> 44 row(s) written, 0 already present", ids DM-000001..DM-000044; direct read `auger.select(ns,'domain','')` -> "stored domain rows: 44", nums 4.01..4.44 (code: auger.py:2461 seed_domains(), auger.py:2702-2709 cmd_init). (2) PER-DOMAIN COVERAGE: status prints "domain grid coverage (44 in the grid | 44 seeded | 0 absent)" + "answered 0 ... | NOT-REACHED 44"; `grep -cE '^  4\.[0-9]{2} '` -> 44 lines, one per domain (code: auger.py:3390-3415, domain_line() auger.py:2605-2640). (3) ABSENT NOT OMITTED: moved 4.05 off-grid -> "44 in the grid | 43 seeded | 1 absent", line "  4.05  data  ABSENT  triage 27 floor 5 terminating ring 5", plus "ABSENT (a grid domain with no stored `domain` row ...): 4.05" and "OFF-GRID rows ...: 9.99" (code: auger.py:2622 state="ABSENT", auger.py:3416-3421). (4) TERMINATING RING PER DOMAIN: 4.01 "terminating ring none (not opened)", 4.04/4.05/4.44 "terminating ring 5" (code: auger.py:2626-2630, 2640). FRESH GATE EVIDENCE (test_command `bash tests/gate.sh`, run detached via setsid): "== syntax ==" OK, "== lint == All checks passed!", "== pytest == 104 passed in 221.49s (0:03:41)", smoke "passed 21, failed 0", "GATE PASS", "GATE_EXIT=0" (gate.sh exits 1 on any arm failure at lines 21/25/37/39/46 and prints GATE PASS only at line 49). AUG-004 tests green: tests/test_auger.py:3634 test_seeding_writes_the_44_domain_rows_with_the_grids_own_numbers, :3681 test_reseeding_the_grid_adds_no_second_copy, :3709 test_status_names_every_absent_grid_domain_when_no_row_is_stored, :3730 test_status_reports_coverage_and_the_terminating_ring_per_domain, :3767 test_a_domain_whose_row_is_gone_is_reported_absent_not_skipped.
AUG-004 criterion 1 passes: 44 real domain rows seed into a live namespace, status prints per-domain coverage with the terminating ring, and a domain with no row is named ABSENT rather than silently omitted — confirmed live and by a fresh full gate (104 passed, smoke 21/0, GATE PASS, exit 0).

Overall: FAIL ✗
