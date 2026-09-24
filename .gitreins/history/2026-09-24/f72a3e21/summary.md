# Verdict: AUG-061

**Task:** Spaced project ids/namespaces crash every table URL with raw http.client.InvalidURL
**Evaluated:** 2026-09-24T18:37:03.396545
**Result:** ✓ PASS

## Pipeline Stages

- ✓ **tier1**
  -   ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==
- ✓ **tier2**
  - COMPLETE
  ✓ python3 auger.py --namespace 'ns with space' --project-id 'Test Project' feedback (and sibling verbs) never raises InvalidURL; regression tests cover path-segment and filter-value encoding; bash tests/gate.sh ends GATE PASS: Live run of the exact reported command: `python3 auger.py --namespace 'ns with space' --project-id 'Test Project' feedback` -> exit 1 with "select project failed (404): {'error': "Table 'project' not found in namespace 'ns with space'", 'code': 'NOT_FOUND'}" — a clean substrate answer, no InvalidURL. Sibling verbs status/dump/toggle likewise return clean 404s and recall exits 0. Encoding is centralized at auger.py:404 url_seg() (quote safe=""), :419 ns_tables_path(), :427 encode_query(), :441 tbl(), with the two remaining hand-built sites fixed at :510 (delete_namespace) and :909 (recall); every db() path is now encoded. Regression tests in tests/test_auger.py:2690-2900 use the real validator (http.client.putrequest in validate_like_the_transport_does) plus a control test proving the pre-fix shape still raises InvalidURL; they cover path segments (test_tbl_percent_encodes_spaced_path_segments), filter values (test_tbl_encodes_a_spaced_filter_value_and_keeps_its_operator), both halves at once, reserved chars, live spaced-namespace and live spaced-project-id round trips, and recall/delete_namespace encoding — 13 selected encoding tests pass ("13 passed, 141 deselected in 0.73s"). Full gate: `bash tests/gate.sh` -> syntax OK, lint "All checks passed!", pytest "154 passed in 403.13s (0:06:43)", smoke "passed 21, failed 0", final line "GATE PASS".
The reported spaced-namespace/project-id command and all sibling verbs now return clean substrate answers instead of InvalidURL, encoding regression tests cover path segments and filter values, and the full gate ends GATE PASS.

## Summary

Judge Result: AUG-061

Stage tier1: PASS
    ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==

Stage tier2: PASS
  COMPLETE
  ✓ python3 auger.py --namespace 'ns with space' --project-id 'Test Project' feedback (and sibling verbs) never raises InvalidURL; regression tests cover path-segment and filter-value encoding; bash tests/gate.sh ends GATE PASS: Live run of the exact reported command: `python3 auger.py --namespace 'ns with space' --project-id 'Test Project' feedback` -> exit 1 with "select project failed (404): {'error': "Table 'project' not found in namespace 'ns with space'", 'code': 'NOT_FOUND'}" — a clean substrate answer, no InvalidURL. Sibling verbs status/dump/toggle likewise return clean 404s and recall exits 0. Encoding is centralized at auger.py:404 url_seg() (quote safe=""), :419 ns_tables_path(), :427 encode_query(), :441 tbl(), with the two remaining hand-built sites fixed at :510 (delete_namespace) and :909 (recall); every db() path is now encoded. Regression tests in tests/test_auger.py:2690-2900 use the real validator (http.client.putrequest in validate_like_the_transport_does) plus a control test proving the pre-fix shape still raises InvalidURL; they cover path segments (test_tbl_percent_encodes_spaced_path_segments), filter values (test_tbl_encodes_a_spaced_filter_value_and_keeps_its_operator), both halves at once, reserved chars, live spaced-namespace and live spaced-project-id round trips, and recall/delete_namespace encoding — 13 selected encoding tests pass ("13 passed, 141 deselected in 0.73s"). Full gate: `bash tests/gate.sh` -> syntax OK, lint "All checks passed!", pytest "154 passed in 403.13s (0:06:43)", smoke "passed 21, failed 0", final line "GATE PASS".
The reported spaced-namespace/project-id command and all sibling verbs now return clean substrate answers instead of InvalidURL, encoding regression tests cover path segments and filter values, and the full gate ends GATE PASS.

Overall: PASS ✓
