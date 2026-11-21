# Verdict: INT-CI-002

**Task:** regenerate auger-docs corpus after AUG-155 doc rewrite; un-red spec-ingest on main
**Evaluated:** 2026-11-21T05:07:16.004498
**Result:** ✓ PASS

## Criteria

- ✓ **tests/test_spec_ingest.py green on the merged tree; delta limited to corpora/auger-docs derived artifacts; no source doc reverted**
  - (1) Green on merged tree: HEAD is merge d68994c; fresh run `.venv/bin/python -m pytest tests/test_spec_ingest.py -p no:cacheprovider -q` -> exit 0, '19 passed in 1.82s'; verbose run shows the exact previously-red node test_shipped_blocks_are_verbatim_and_manifests_reconcile[auger-docs] PASSED [57%] (not skipped). (2) Delta limited: `git diff a3be1d2 d68994c --name-status` = exactly M corpora/auger-docs/SPEC-003-xml-specs.xml + M corpora/auger-docs/manifest.json; diff line count outside corpora/auger-docs is 0. The XML delta adds the regenerated CLI-section blocks (cli-commands-and-exit-codes, validate, check, bundle, align, list, assemble) and manifest block_count 9->17 with new text_sha256 digests — derived artifacts only. (3) No source doc reverted: `git diff c4cf366 d68994c -- docs/SPEC-003-xml-specs.md` = 0 lines, so the AUG-155 rewrite (commit c4cf366) is intact; docs/SPEC-003-xml-specs.md still contains the new 'CLI commands and exit codes' section (grep -c = 1) which the regenerated corpus now mirrors.

## Pipeline Stages

- ✓ **tier1**
  -   ✓ lint: scanners: nice=nice -n 10
  ✓ secrets: secrets: scanners=gitleaks+builtin cross-check
  ✓ tests: scanners: nice=nice -n 10
- ✓ **tier2**
  - COMPLETE
  ✓ tests/test_spec_ingest.py green on the merged tree; delta limited to corpora/auger-docs derived artifacts; no source doc reverted: (1) Green on merged tree: HEAD is merge d68994c; fresh run `.venv/bin/python -m pytest tests/test_spec_ingest.py -p no:cacheprovider -q` -> exit 0, '19 passed in 1.82s'; verbose run shows the exact previously-red node test_shipped_blocks_are_verbatim_and_manifests_reconcile[auger-docs] PASSED [57%] (not skipped). (2) Delta limited: `git diff a3be1d2 d68994c --name-status` = exactly M corpora/auger-docs/SPEC-003-xml-specs.xml + M corpora/auger-docs/manifest.json; diff line count outside corpora/auger-docs is 0. The XML delta adds the regenerated CLI-section blocks (cli-commands-and-exit-codes, validate, check, bundle, align, list, assemble) and manifest block_count 9->17 with new text_sha256 digests — derived artifacts only. (3) No source doc reverted: `git diff c4cf366 d68994c -- docs/SPEC-003-xml-specs.md` = 0 lines, so the AUG-155 rewrite (commit c4cf366) is intact; docs/SPEC-003-xml-specs.md still contains the new 'CLI commands and exit codes' section (grep -c = 1) which the regenerated corpus now mirrors.
The merge d68994c regenerates exactly the two corpora/auger-docs derived artifacts (SPEC-003-xml-specs.xml + manifest.json) from the AUG-155-rewritten source doc, the previously-red verbatim/manifest test node for auger-docs now passes in a fresh 19/19 run, and the source doc diff vs the AUG-155 commit is zero — nothing reverted, nothing outside the corpus touched.

## Summary

Stage tier1: PASS
    ✓ lint: scanners: nice=nice -n 10
  ✓ secrets: secrets: scanners=gitleaks+builtin cross-check
  ✓ tests: scanners: nice=nice -n 10

Stage tier2: PASS
  COMPLETE
  ✓ tests/test_spec_ingest.py green on the merged tree; delta limited to corpora/auger-docs derived artifacts; no source doc reverted: (1) Green on merged tree: HEAD is merge d68994c; fresh run `.venv/bin/python -m pytest tests/test_spec_ingest.py -p no:cacheprovider -q` -> exit 0, '19 passed in 1.82s'; verbose run shows the exact previously-red node test_shipped_blocks_are_verbatim_and_manifests_reconcile[auger-docs] PASSED [57%] (not skipped). (2) Delta limited: `git diff a3be1d2 d68994c --name-status` = exactly M corpora/auger-docs/SPEC-003-xml-specs.xml + M corpora/auger-docs/manifest.json; diff line count outside corpora/auger-docs is 0. The XML delta adds the regenerated CLI-section blocks (cli-commands-and-exit-codes, validate, check, bundle, align, list, assemble) and manifest block_count 9->17 with new text_sha256 digests — derived artifacts only. (3) No source doc reverted: `git diff c4cf366 d68994c -- docs/SPEC-003-xml-specs.md` = 0 lines, so the AUG-155 rewrite (commit c4cf366) is intact; docs/SPEC-003-xml-specs.md still contains the new 'CLI commands and exit codes' section (grep -c = 1) which the regenerated corpus now mirrors.
The merge d68994c regenerates exactly the two corpora/auger-docs derived artifacts (SPEC-003-xml-specs.xml + manifest.json) from the AUG-155-rewritten source doc, the previously-red verbatim/manifest test node for auger-docs now passes in a fresh 19/19 run, and the source doc diff vs the AUG-155 commit is zero — nothing reverted, nothing outside the corpus touched.

Overall: PASS ✓
