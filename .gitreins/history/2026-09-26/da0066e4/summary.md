# Verdict: AUG-082

**Task:** Project scoped semantic recall
**Evaluated:** 2026-09-26T18:32:35.531991
**Result:** ✗ FAIL

## Pipeline Stages

- ✗ **tier1**
  -   ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✗ tests: FAIL (tests/test_auger.py::test_recall_cli_scopes_semantic_hits_to_the_selected_project [first faili
- ✗ **tier2**
  - INCOMPLETE
  ✗ When recall is invoked with a project id, every returned semantic hit belongs to that project and cross-project hits are excluded before ranking or limiting.: Criterion 0 (project filter excludes cross-project hits before ranking/limiting): auger.py:1128-1153 — recall() builds project_prefix `/auger/<pid>/`, sends `&prefix=<encoded>` in the SAME /api/memories request as q+limit (so the substrate constrains candidates before top-N), and post-filters items by key.startswith(project_prefix) as fail-closed backstop. cmd_recall (auger.py:4865) passes project_id=a.project_id. Test test_recall_sends_project_prefix_with_semantic_limit_and_rejects_foreign_hits (tests/test_auger.py:4449) asserts URL contains prefix before q/limit and that a foreign higher-scored row is dropped. NOTE: substrate-side `prefix` param support unverified (no substrate source in repo) — see criterion 3 live test.
  ✗ Without a project filter, recall keeps current namespace-wide behavior.: Criterion 1 (no project filter => namespace-wide): auger.py:1128 project_prefix is None when project_id is None; prefix_query = "" so URL is unchanged `/api/memories?namespace=..&q=..&limit=..`; auger.py:1147-1153 returns items unfiltered. Existing test test_recall_sends_an_encoded_namespace (tests/test_auger.py:4433) asserts exact URL with no prefix param. Also test_recall_cli_scopes_semantic_hits_to_the_selected_project asserts no-`-p` control sees both projects' seeds.
  ✗ Regression tests create two projects in one namespace, seed evidence for both, and prove each filtered recall returns only its own evidence with no cross-project leakage.: Criterion 2 (regression test: two projects in one namespace, seed both, prove no leakage): tests/test_auger.py:1766-1795 test_recall_cli_scopes_semantic_hits_to_the_selected_project(ns) — creates P-AUG082-ALPHA and P-AUG082-BETA in ONE namespace via `start --seed` (both seeds share the query phrase), asserts unfiltered recall returns exactly both `/auger/<pid>/seed` keys, then for each selected project runs `-p <pid> recall` and asserts own seed key present and other's absent. LIVE RUN: `python3 -m pytest tests/test_auger.py -q -k recall_cli_scopes -v` => "1 passed, 213 deselected in 38.35s" (exit 0). Also `-k "recall"` => "11 passed, 203 deselected in 48.84s".
  ✗ The focused tests and the project gate pass, and the committed change is limited to the implementation and regression tests for AUG-082.: Not verified — evaluation terminated before this criterion was checked
Partial verdict — evaluation hit resource cap before all criteria verified

## Summary

Judge Result: AUG-082

Stage tier1: FAIL
    ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✗ tests: FAIL (tests/test_auger.py::test_recall_cli_scopes_semantic_hits_to_the_selected_project [first faili

Stage tier2: FAIL
  INCOMPLETE
  ✗ When recall is invoked with a project id, every returned semantic hit belongs to that project and cross-project hits are excluded before ranking or limiting.: Criterion 0 (project filter excludes cross-project hits before ranking/limiting): auger.py:1128-1153 — recall() builds project_prefix `/auger/<pid>/`, sends `&prefix=<encoded>` in the SAME /api/memories request as q+limit (so the substrate constrains candidates before top-N), and post-filters items by key.startswith(project_prefix) as fail-closed backstop. cmd_recall (auger.py:4865) passes project_id=a.project_id. Test test_recall_sends_project_prefix_with_semantic_limit_and_rejects_foreign_hits (tests/test_auger.py:4449) asserts URL contains prefix before q/limit and that a foreign higher-scored row is dropped. NOTE: substrate-side `prefix` param support unverified (no substrate source in repo) — see criterion 3 live test.
  ✗ Without a project filter, recall keeps current namespace-wide behavior.: Criterion 1 (no project filter => namespace-wide): auger.py:1128 project_prefix is None when project_id is None; prefix_query = "" so URL is unchanged `/api/memories?namespace=..&q=..&limit=..`; auger.py:1147-1153 returns items unfiltered. Existing test test_recall_sends_an_encoded_namespace (tests/test_auger.py:4433) asserts exact URL with no prefix param. Also test_recall_cli_scopes_semantic_hits_to_the_selected_project asserts no-`-p` control sees both projects' seeds.
  ✗ Regression tests create two projects in one namespace, seed evidence for both, and prove each filtered recall returns only its own evidence with no cross-project leakage.: Criterion 2 (regression test: two projects in one namespace, seed both, prove no leakage): tests/test_auger.py:1766-1795 test_recall_cli_scopes_semantic_hits_to_the_selected_project(ns) — creates P-AUG082-ALPHA and P-AUG082-BETA in ONE namespace via `start --seed` (both seeds share the query phrase), asserts unfiltered recall returns exactly both `/auger/<pid>/seed` keys, then for each selected project runs `-p <pid> recall` and asserts own seed key present and other's absent. LIVE RUN: `python3 -m pytest tests/test_auger.py -q -k recall_cli_scopes -v` => "1 passed, 213 deselected in 38.35s" (exit 0). Also `-k "recall"` => "11 passed, 203 deselected in 48.84s".
  ✗ The focused tests and the project gate pass, and the committed change is limited to the implementation and regression tests for AUG-082.: Not verified — evaluation terminated before this criterion was checked
Partial verdict — evaluation hit resource cap before all criteria verified

Overall: FAIL ✗
