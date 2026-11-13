# Verdict: AUG-148

**Task:** AUG-148: refuse a symlink that resolves outside the corpus root, on the discovery path as well as the include path, without leaking host-file content
**Evaluated:** 2026-11-13T12:45:14.875561
**Result:** ✗ FAIL

## Criteria

- ✓ **PROOF: pytest tests/test_xml_specs.py -q -k out_of_root_symlink prints 1 passed. FAILIF: any failure, error, or a pass count other than 1.**
  - `python3 -m pytest tests/test_xml_specs.py -q -k out_of_root_symlink` -> exit 0, "1 passed, 57 deselected in 0.05s". Test at tests/test_xml_specs.py:1320 test_an_out_of_root_symlink_is_refused_not_read.
- ✓ **PROOF: pytest tests/test_xml_specs.py -q -k in_root_symlink prints 1 passed. FAILIF: any failure, error, or a pass count other than 1, which would mean the fix over-blocks ordinary symlinks.**
  - `python3 -m pytest tests/test_xml_specs.py -q -k in_root_symlink` -> exit 0, "1 passed, 57 deselected in 0.15s". Test at tests/test_xml_specs.py:1378 test_an_in_root_symlink_is_an_ordinary_document.
- ✓ **PROOF: grep -c symlink-outside-root ./auger.py prints at least 2. FAILIF: zero or one occurrence, meaning the typed refusal code is missing from the loader and the diagnostic.**
  - `grep -c symlink-outside-root ./auger.py` -> 2. auger.py:8023 (discovery refusal code in _spec_discover_documents) and auger.py:13083 (comment in _xml_spec_documents strict refusal). Both loaders (load_spec_corpus:8743, load_spec_corpus_indexed:8819, _xml_spec_documents:13077) call _spec_discover_documents and raise the typed code.
- ✓ **PROOF: grep -c SENTINEL-CONTENT-DO-NOT-LEAK tests/test_xml_specs.py prints 1. FAILIF: zero, meaning the no-leak assertion vanished from the out-of-root fixture test.**
  - `grep -c SENTINEL-CONTENT-DO-NOT-LEAK tests/test_xml_specs.py` -> 1 (tests/test_xml_specs.py:1329 sentinel definition, asserted absent from err/out/detail at lines 1349-1363).
- ✓ **PROOF: grep -c tests/test_xml_specs.py tests/gate.sh prints at least 2. FAILIF: the gate pytest arm no longer names the test file, so the enforced fix would be invisible to the gate.**
  - `grep -c tests/test_xml_specs.py tests/gate.sh` -> 2 (tests/gate.sh:157 comment, tests/gate.sh:206 pytest arm naming tests/test_xml_specs.py).

## Pipeline Stages

- ✗ **tier1**
  -   ✓ lint: scanners: nice=nice -n 10
  ✓ secrets: secrets: scanners=gitleaks+builtin cross-check
  ✗ tests: FAIL (tests/test_board_evidence_audit.py::test_the_committed_board_audits_clean [first failing id])
- ✓ **tier2**
  - COMPLETE
  ✓ PROOF: pytest tests/test_xml_specs.py -q -k out_of_root_symlink prints 1 passed. FAILIF: any failure, error, or a pass count other than 1.: `python3 -m pytest tests/test_xml_specs.py -q -k out_of_root_symlink` -> exit 0, "1 passed, 57 deselected in 0.05s". Test at tests/test_xml_specs.py:1320 test_an_out_of_root_symlink_is_refused_not_read.
  ✓ PROOF: pytest tests/test_xml_specs.py -q -k in_root_symlink prints 1 passed. FAILIF: any failure, error, or a pass count other than 1, which would mean the fix over-blocks ordinary symlinks.: `python3 -m pytest tests/test_xml_specs.py -q -k in_root_symlink` -> exit 0, "1 passed, 57 deselected in 0.15s". Test at tests/test_xml_specs.py:1378 test_an_in_root_symlink_is_an_ordinary_document.
  ✓ PROOF: grep -c symlink-outside-root ./auger.py prints at least 2. FAILIF: zero or one occurrence, meaning the typed refusal code is missing from the loader and the diagnostic.: `grep -c symlink-outside-root ./auger.py` -> 2. auger.py:8023 (discovery refusal code in _spec_discover_documents) and auger.py:13083 (comment in _xml_spec_documents strict refusal). Both loaders (load_spec_corpus:8743, load_spec_corpus_indexed:8819, _xml_spec_documents:13077) call _spec_discover_documents and raise the typed code.
  ✓ PROOF: grep -c SENTINEL-CONTENT-DO-NOT-LEAK tests/test_xml_specs.py prints 1. FAILIF: zero, meaning the no-leak assertion vanished from the out-of-root fixture test.: `grep -c SENTINEL-CONTENT-DO-NOT-LEAK tests/test_xml_specs.py` -> 1 (tests/test_xml_specs.py:1329 sentinel definition, asserted absent from err/out/detail at lines 1349-1363).
  ✓ PROOF: grep -c tests/test_xml_specs.py tests/gate.sh prints at least 2. FAILIF: the gate pytest arm no longer names the test file, so the enforced fix would be invisible to the gate.: `grep -c tests/test_xml_specs.py tests/gate.sh` -> 2 (tests/gate.sh:157 comment, tests/gate.sh:206 pytest arm naming tests/test_xml_specs.py).
All five proof criteria for AUG-148 hold: both symlink tests pass (1 passed each), the typed symlink-outside-root refusal appears twice in auger.py on the discovery path, the no-leak sentinel assertion is present, and the gate names the test file.

## Summary

Stage tier1: FAIL
    ✓ lint: scanners: nice=nice -n 10
  ✓ secrets: secrets: scanners=gitleaks+builtin cross-check
  ✗ tests: FAIL (tests/test_board_evidence_audit.py::test_the_committed_board_audits_clean [first failing id])

Stage tier2: PASS
  COMPLETE
  ✓ PROOF: pytest tests/test_xml_specs.py -q -k out_of_root_symlink prints 1 passed. FAILIF: any failure, error, or a pass count other than 1.: `python3 -m pytest tests/test_xml_specs.py -q -k out_of_root_symlink` -> exit 0, "1 passed, 57 deselected in 0.05s". Test at tests/test_xml_specs.py:1320 test_an_out_of_root_symlink_is_refused_not_read.
  ✓ PROOF: pytest tests/test_xml_specs.py -q -k in_root_symlink prints 1 passed. FAILIF: any failure, error, or a pass count other than 1, which would mean the fix over-blocks ordinary symlinks.: `python3 -m pytest tests/test_xml_specs.py -q -k in_root_symlink` -> exit 0, "1 passed, 57 deselected in 0.15s". Test at tests/test_xml_specs.py:1378 test_an_in_root_symlink_is_an_ordinary_document.
  ✓ PROOF: grep -c symlink-outside-root ./auger.py prints at least 2. FAILIF: zero or one occurrence, meaning the typed refusal code is missing from the loader and the diagnostic.: `grep -c symlink-outside-root ./auger.py` -> 2. auger.py:8023 (discovery refusal code in _spec_discover_documents) and auger.py:13083 (comment in _xml_spec_documents strict refusal). Both loaders (load_spec_corpus:8743, load_spec_corpus_indexed:8819, _xml_spec_documents:13077) call _spec_discover_documents and raise the typed code.
  ✓ PROOF: grep -c SENTINEL-CONTENT-DO-NOT-LEAK tests/test_xml_specs.py prints 1. FAILIF: zero, meaning the no-leak assertion vanished from the out-of-root fixture test.: `grep -c SENTINEL-CONTENT-DO-NOT-LEAK tests/test_xml_specs.py` -> 1 (tests/test_xml_specs.py:1329 sentinel definition, asserted absent from err/out/detail at lines 1349-1363).
  ✓ PROOF: grep -c tests/test_xml_specs.py tests/gate.sh prints at least 2. FAILIF: the gate pytest arm no longer names the test file, so the enforced fix would be invisible to the gate.: `grep -c tests/test_xml_specs.py tests/gate.sh` -> 2 (tests/gate.sh:157 comment, tests/gate.sh:206 pytest arm naming tests/test_xml_specs.py).
All five proof criteria for AUG-148 hold: both symlink tests pass (1 passed each), the typed symlink-outside-root refusal appears twice in auger.py on the discovery path, the no-leak sentinel assertion is present, and the gate names the test file.

Overall: FAIL ✗
