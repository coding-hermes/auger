# Verdict: AUG-007

**Task:** add the graph tables (edge, facet) and declare them in the namespace — SPEC-001 2/3
**Evaluated:** 2026-09-21T00:53:29.075553
**Result:** ✓ PASS

## Pipeline Stages

- ✓ **tier1**
  -   ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==
- ✓ **tier2**
  - COMPLETE
  ✓ auger init declares the edge and facet tables idempotently and the live namespace exposes them at /api/ns/<ns>/tables: Live run: `python3 auger.py -n auger-eval-944473 init` -> "declared: 11/11 tables -> assumption, break, decision, domain, edge, escalation, facet, option, project, question, unknown" with "wrote declarations: ... edge, facet". Second init -> "namespace auger-eval-944473: existing", "declared: 11/11 tables", and NO "wrote declarations" line (idempotent). GET /api/ns/auger-eval-944473/tables returned ['assumption','break','decision','domain','edge','escalation','facet','option','project','question','unknown'] — edge and facet are live. Code: auger.py:82-88 declares both in COLS; cmd_init (auger.py:333-378) writes each declaration only when the file content differs and then reads back /api/ns/<ns>/tables. Gate: `bash tests/gate.sh` -> ruff "All checks passed!", pytest "27 passed in 51.87s", smoke "passed 15, failed 0", "GATE PASS".
  ✓ an edge row round-trips through the declared-table API with its kind, confidence and source intact: Live on ns auger-eval-944473: auger.edge(ns,'P-EVAL','satisfies','decision','D-001','question','Q-000001',confidence=0.73,source='gate') returned E-000001; re-reading via auger.select(ns,'edge','id=eq.E-000001') gave kind='satisfies', confidence=0.73, source='gate', src_id='D-001', dst_id='Q-000001' — all intact through the declared-table API (auger.py:247-270 writes via insert() -> POST /api/ns/<ns>/tables/edge).
  ✓ inserting an edge whose src_id does not exist is REFUSED rather than silently stored (referential check): Live on ns auger-eval-944473: auger.edge(...,src_id='D-DOES-NOT-EXIST',...) raised SystemExit "refused: src decision 'D-DOES-NOT-EXIST' does not exist in table decision — an edge to a missing node is not stored". A missing dst_id was likewise refused, and an unknown node kind raised "unknown node kind 'banana'". Edge row count before=1, after=1 — nothing was silently stored. Code: auger.py:262-264 loops over both endpoints calling node_exists() (auger.py:238-245) and raises before insert().
  ✓ facet rows are written one per facet for a question, and the facet set is data rather than hardcoded in the code path: Live on ns auger-eval-944473: iterating auger.facet_set() = ('data','failure','ownership','cost','test','who_else') and calling auger.facet() per name produced exactly F-000001..F-000006 for question Q-000001, one row per facet, names matching the set. The set is DATA, not a hardcoded gate: (a) facet() accepted an out-of-set name ('regulatory' -> F-000007) with no rejection; (b) monkeypatching auger.facet_set to ('alpha','beta') made the write path emit exactly those two rows for Q-000002 — the code path follows the data. The six strings appear only in DEFAULT_FACETS (auger.py:104) and docs/SPEC-001-graph.md:88, never as a literal in a code path; facet_set() (auger.py:109-111) is the single read point. facet() for a missing question is refused (auger.py:278-279).
All four criteria verified against a live DuckBrain namespace plus a full green gate run (ruff clean, 27 pytest passed, 15/15 smoke assertions, GATE PASS).

## Summary

Judge Result: AUG-007

Stage tier1: PASS
    ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==

Stage tier2: PASS
  COMPLETE
  ✓ auger init declares the edge and facet tables idempotently and the live namespace exposes them at /api/ns/<ns>/tables: Live run: `python3 auger.py -n auger-eval-944473 init` -> "declared: 11/11 tables -> assumption, break, decision, domain, edge, escalation, facet, option, project, question, unknown" with "wrote declarations: ... edge, facet". Second init -> "namespace auger-eval-944473: existing", "declared: 11/11 tables", and NO "wrote declarations" line (idempotent). GET /api/ns/auger-eval-944473/tables returned ['assumption','break','decision','domain','edge','escalation','facet','option','project','question','unknown'] — edge and facet are live. Code: auger.py:82-88 declares both in COLS; cmd_init (auger.py:333-378) writes each declaration only when the file content differs and then reads back /api/ns/<ns>/tables. Gate: `bash tests/gate.sh` -> ruff "All checks passed!", pytest "27 passed in 51.87s", smoke "passed 15, failed 0", "GATE PASS".
  ✓ an edge row round-trips through the declared-table API with its kind, confidence and source intact: Live on ns auger-eval-944473: auger.edge(ns,'P-EVAL','satisfies','decision','D-001','question','Q-000001',confidence=0.73,source='gate') returned E-000001; re-reading via auger.select(ns,'edge','id=eq.E-000001') gave kind='satisfies', confidence=0.73, source='gate', src_id='D-001', dst_id='Q-000001' — all intact through the declared-table API (auger.py:247-270 writes via insert() -> POST /api/ns/<ns>/tables/edge).
  ✓ inserting an edge whose src_id does not exist is REFUSED rather than silently stored (referential check): Live on ns auger-eval-944473: auger.edge(...,src_id='D-DOES-NOT-EXIST',...) raised SystemExit "refused: src decision 'D-DOES-NOT-EXIST' does not exist in table decision — an edge to a missing node is not stored". A missing dst_id was likewise refused, and an unknown node kind raised "unknown node kind 'banana'". Edge row count before=1, after=1 — nothing was silently stored. Code: auger.py:262-264 loops over both endpoints calling node_exists() (auger.py:238-245) and raises before insert().
  ✓ facet rows are written one per facet for a question, and the facet set is data rather than hardcoded in the code path: Live on ns auger-eval-944473: iterating auger.facet_set() = ('data','failure','ownership','cost','test','who_else') and calling auger.facet() per name produced exactly F-000001..F-000006 for question Q-000001, one row per facet, names matching the set. The set is DATA, not a hardcoded gate: (a) facet() accepted an out-of-set name ('regulatory' -> F-000007) with no rejection; (b) monkeypatching auger.facet_set to ('alpha','beta') made the write path emit exactly those two rows for Q-000002 — the code path follows the data. The six strings appear only in DEFAULT_FACETS (auger.py:104) and docs/SPEC-001-graph.md:88, never as a literal in a code path; facet_set() (auger.py:109-111) is the single read point. facet() for a missing question is refused (auger.py:278-279).
All four criteria verified against a live DuckBrain namespace plus a full green gate run (ruff clean, 27 pytest passed, 15/15 smoke assertions, GATE PASS).

Overall: PASS ✓
