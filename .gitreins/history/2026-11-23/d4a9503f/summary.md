# Verdict: AUG-198

**Task:** code-factory corpus manifest drift reds tier-1 tests(full) on this host
**Evaluated:** 2026-11-23T20:18:13.633522
**Result:** ✗ FAIL

## Criteria

- — **tests/test_spec_ingest.py::test_shipped_blocks_are_verbatim_and_manifests_reconcile[code-factory] passes on this host with default env; bash tests/gate.sh full mode exits 0 on this host OR the code-factory leg documents host-local source drift as a typed skip naming the measured revision; verbatim reconciliation assertions not weakened**
  - Not examined — evaluation ended before this criterion was reached (no judgement was made about it)

## Pipeline Stages

- ✗ **tier1**
  -   ✗ tests: scanners: nice=nice -n 10
  ✗ lint: scanners: nice=nice -n 10
  ✓ secrets: secrets: scanners=gitleaks+builtin cross-check
- ✗ **tier2**
  - INCOMPLETE
  — tests/test_spec_ingest.py::test_shipped_blocks_are_verbatim_and_manifests_reconcile[code-factory] passes on this host with default env; bash tests/gate.sh full mode exits 0 on this host OR the code-factory leg documents host-local source drift as a typed skip naming the measured revision; verbatim reconciliation assertions not weakened: Not examined — evaluation ended before this criterion was reached (no judgement was made about it)
  ✗ Coverage FAILED — not passing: tests/test_spec_ingest.py::test_shipped_blocks_are_verbatim_and_manifests_reconcile[code-factory] passes on this host with default env; bash tests/gate.sh full mode exits 0 on this host OR the code-factory leg documents host-local source drift as a typed skip naming the measured revision; verbatim reconciliation assertions not weakened
Cap exceeded: Iteration cap (200) reached (201.0 used). Increase max_iterations or split criteria. Partial verdict — 0 of 1 criteria graded, 1 ungraded. The run spent 632,063 in / 12,990 out over 201.0 iterations.
- ✓ **fix_verification**
  - fix_verification: 0 finding(s) verified

## Summary

Stage tier1: FAIL
    ✗ tests: scanners: nice=nice -n 10
  ✗ lint: scanners: nice=nice -n 10
  ✓ secrets: secrets: scanners=gitleaks+builtin cross-check

Stage tier2: FAIL
  INCOMPLETE
  — tests/test_spec_ingest.py::test_shipped_blocks_are_verbatim_and_manifests_reconcile[code-factory] passes on this host with default env; bash tests/gate.sh full mode exits 0 on this host OR the code-factory leg documents host-local source drift as a typed skip naming the measured revision; verbatim reconciliation assertions not weakened: Not examined — evaluation ended before this criterion was reached (no judgement was made about it)
  ✗ Coverage FAILED — not passing: tests/test_spec_ingest.py::test_shipped_blocks_are_verbatim_and_manifests_reconcile[code-factory] passes on this host with default env; bash tests/gate.sh full mode exits 0 on this host OR the code-factory leg documents host-local source drift as a typed skip naming the measured revision; verbatim reconciliation assertions not weakened
Cap exceeded: Iteration cap (200) reached (201.0 used). Increase max_iterations or split criteria. Partial verdict — 0 of 1 criteria graded, 1 ungraded. The run spent 632,063 in / 12,990 out over 201.0 iterations.

Stage fix_verification: PASS
  fix_verification: 0 finding(s) verified

Overall: FAIL ✗
