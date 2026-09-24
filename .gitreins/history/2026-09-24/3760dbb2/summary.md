# Verdict: AUG-030

**Task:** Supersession: re-answering a question flags and supersedes the prior decision
**Evaluated:** 2026-09-24T06:17:41.664415
**Result:** ✓ PASS

## Pipeline Stages

- ✓ **tier1**
  -   ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==
- ✓ **tier2**
  - COMPLETE
  ✓ answer flags an overlapping decided decision and --supersedes writes a supersedes edge (decision->decision), flips the old decision status to superseded with the reason recorded in facet rows; dump renders superseded decisions as such and excludes them from ACTIVE CONFIGURATION; status names unresolved supersession chains; tests cover flag, edge, status flip, dump exclusion: Fresh evidence: `python -m pytest tests/test_auger.py -k "supersede or supersession" -p no:cacheprovider -q` -> "6 passed, 130 deselected in 2.54s" (exit 0); `python -m py_compile auger.py` OK; `ruff check auger.py` -> "All checks passed!". FLAG: auger.py:3734-3743 prints "supersedes?  D-001 (4.05, decided) ... pass --supersedes D-001"; test_auger.py:760-784 asserts the warning and that no edge is written. EDGE decision->decision: auger.py:1848 edge(kind="supersedes", src_kind="decision", dst_kind="decision"); test_auger.py:720-728 asserts exactly one edge with src (decision,D-003) -> dst (decision,D-001) and note "D-003 supersedes D-001: the envelope format changed". STATUS FLIP: auger.py:1859 patch(decision,target,{"status":SUPERSEDED_STATUS}) with SUPERSEDED_STATUS="superseded" (auger.py:248); test_auger.py:717 asserts "status -> superseded" and :729 asserts stored status. FACET REASON: auger.py:1861-1875 writes reason "superseded by D-00X: why" into facet rows; test_auger.py:818 asserts note "superseded by D-002: the answer was re-evaluated". DUMP: auger.py:4206-4212 sets live_ids=set() for superseded decisions so they are excluded from confs, and :4237 renders "[SUPERSEDED by X]"; ACTIVE CONFIGURATION line at auger.py:4256; test_auger.py:903-915 asserts "## D-001  (4.05)  conf 0.82  [SUPERSEDED by D-003]" and "ACTIVE CONFIGURATION: D-002=staging table, D-003=blake3 envelope" (D-001 absent). STATUS UNRESOLVED CHAIN: auger.py:3894-3928 prints the "supersession:" block including "UNRESOLVED: D-00X is still decided with an active option despite supersession by D-00Y"; test_auger.py:839-862. Validation/refusals: auger.py:1799-1830 (self-supersede, nonexistent target, --supersedes-why without target); test_auger.py:732-756 asserts nothing is written on refusal.
Supersession is fully implemented and verified: fresh targeted tests pass (6 passed), py_compile and ruff clean, and code inspection confirms the detection flag, decision->decision supersedes edge, status flip with facet reason, dump rendering/exclusion from ACTIVE CONFIGURATION, and UNRESOLVED chain reporting.

## Summary

Judge Result: AUG-030

Stage tier1: PASS
    ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==

Stage tier2: PASS
  COMPLETE
  ✓ answer flags an overlapping decided decision and --supersedes writes a supersedes edge (decision->decision), flips the old decision status to superseded with the reason recorded in facet rows; dump renders superseded decisions as such and excludes them from ACTIVE CONFIGURATION; status names unresolved supersession chains; tests cover flag, edge, status flip, dump exclusion: Fresh evidence: `python -m pytest tests/test_auger.py -k "supersede or supersession" -p no:cacheprovider -q` -> "6 passed, 130 deselected in 2.54s" (exit 0); `python -m py_compile auger.py` OK; `ruff check auger.py` -> "All checks passed!". FLAG: auger.py:3734-3743 prints "supersedes?  D-001 (4.05, decided) ... pass --supersedes D-001"; test_auger.py:760-784 asserts the warning and that no edge is written. EDGE decision->decision: auger.py:1848 edge(kind="supersedes", src_kind="decision", dst_kind="decision"); test_auger.py:720-728 asserts exactly one edge with src (decision,D-003) -> dst (decision,D-001) and note "D-003 supersedes D-001: the envelope format changed". STATUS FLIP: auger.py:1859 patch(decision,target,{"status":SUPERSEDED_STATUS}) with SUPERSEDED_STATUS="superseded" (auger.py:248); test_auger.py:717 asserts "status -> superseded" and :729 asserts stored status. FACET REASON: auger.py:1861-1875 writes reason "superseded by D-00X: why" into facet rows; test_auger.py:818 asserts note "superseded by D-002: the answer was re-evaluated". DUMP: auger.py:4206-4212 sets live_ids=set() for superseded decisions so they are excluded from confs, and :4237 renders "[SUPERSEDED by X]"; ACTIVE CONFIGURATION line at auger.py:4256; test_auger.py:903-915 asserts "## D-001  (4.05)  conf 0.82  [SUPERSEDED by D-003]" and "ACTIVE CONFIGURATION: D-002=staging table, D-003=blake3 envelope" (D-001 absent). STATUS UNRESOLVED CHAIN: auger.py:3894-3928 prints the "supersession:" block including "UNRESOLVED: D-00X is still decided with an active option despite supersession by D-00Y"; test_auger.py:839-862. Validation/refusals: auger.py:1799-1830 (self-supersede, nonexistent target, --supersedes-why without target); test_auger.py:732-756 asserts nothing is written on refusal.
Supersession is fully implemented and verified: fresh targeted tests pass (6 passed), py_compile and ruff clean, and code inspection confirms the detection flag, decision->decision supersedes edge, status flip with facet reason, dump rendering/exclusion from ACTIVE CONFIGURATION, and UNRESOLVED chain reporting.

Overall: PASS ✓
