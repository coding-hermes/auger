# Verdict: AUG-148

**Task:** AUG-148: refuse a symlink that resolves outside the corpus root, on the discovery path as well as the include path, without leaking host-file content
**Evaluated:** 2026-11-13T13:47:50.960246
**Result:** ✗ FAIL

## Criteria

- ✓ **PROOF: pytest tests/test_xml_specs.py -q -k out_of_root_symlink prints 1 passed. FAILIF: any failure, error, or a pass count other than 1.**
  - PASS: `python -m pytest tests/test_xml_specs.py -q -k out_of_root_symlink` -> "1 passed, 57 deselected in 0.04s", exit 0 · graded at iteration 8.0 (53,775 in / 1,106 out, 8.0 iterations and 13s into the run)
- ✓ **PROOF: pytest tests/test_xml_specs.py -q -k in_root_symlink prints 1 passed. FAILIF: any failure, error, or a pass count other than 1, which would mean the fix over-blocks ordinary symlinks.**
  - PASS: `python -m pytest tests/test_xml_specs.py -q -k in_root_symlink` -> "1 passed, 57 deselected in 0.60s", exit 0 · graded at iteration 8.1 (0 in / 0 out, 0.1 iterations and 13s into the run)
- ✓ **PROOF: grep -c symlink-outside-root ./auger.py prints at least 2. FAILIF: zero or one occurrence, meaning the typed refusal code is missing from the loader and the diagnostic.**
  - PASS: grep -c symlink-outside-root ./auger.py -> 2 (lines 7994 docstring, 8023 code field) · graded at iteration 8.2 (0 in / 0 out, 0.1 iterations and 13s into the run)
- ✓ **PROOF: grep -c SENTINEL-CONTENT-DO-NOT-LEAK tests/test_xml_specs.py prints 1. FAILIF: zero, meaning the no-leak assertion vanished from the out-of-root fixture test.**
  - PASS: grep -c SENTINEL-CONTENT-DO-NOT-LEAK tests/test_xml_specs.py -> 1 (line 1328) · graded at iteration 8.3 (0 in / 0 out, 0.1 iterations and 13s into the run)
- ✓ **PROOF: grep -c tests/test_xml_specs.py tests/gate.sh prints at least 2. FAILIF: the gate pytest arm no longer names the test file, so the enforced fix would be invisible to the gate.**
  - PASS: grep -c tests/test_xml_specs.py tests/gate.sh -> 2 (line 157 comment, line 206 pytest arm) · graded at iteration 8.4 (0 in / 0 out, 0.1 iterations and 13s into the run)
- ✗ **derived criterion 0**
  - d0 PASS: CLI probe /tmp/probe_aug148.py — validate/check exit 2 with "spec error (symlink-outside-root)"; list/assemble (load_spec_corpus_indexed, auger.py:8819) skip+count and still answer. Both loaders reach _spec_discover_documents (auger.py:8743, 8819, 13077). · graded at iteration 29.0 (413,680 in / 5,191 out, 20.6 iterations and 60s into the run)
- ✗ **derived criterion 1**
  - d1 PASS: probe output shows the diagnostic carries only rel path + resolved path + root; sentinel never in stdout/stderr; test asserts sentinel not in err/out (tests/test_xml_specs.py:1328,1355-1360). · graded at iteration 29.1 (0 in / 0 out, 0.1 iterations and 60s into the run)
- ✗ **derived criterion 2**
  - d2 FAIL: symlinked DIRECTORY resolving outside the root is NOT refused. /tmp/probe_aug148f.py: corpus/sub -> outside dir containing secret.xml; load_spec_corpus returned documents ['real.xml'] only, no SpecError, no refusal entry — the out-of-root document is silently dropped (auger.py:8002-8020: rglob yields corpus/sub/secret.xml, resolve() -> outside/secret.xml, relative_to(root) raises ValueError -> refusal appended... but probe shows no refusal surfaced). Investigate: refusal list is built but load_spec_corpus only raises when `refusals and not tolerate_unreadable`; here tolerate_unreadable=False so it SHOULD raise. Yet no raise -> the path was never yielded by rglob? Need to confirm. Dangling symlink IS refused (unreadable-document). · graded at iteration 29.2 (0 in / 0 out, 0.1 iterations and 60s into the run)

## Pipeline Stages

- ✓ **tier1**
  -   ✓ lint: scanners: nice=nice -n 10
  ✓ secrets: secrets: scanners=gitleaks+builtin cross-check
  ✓ tests: scanners: nice=nice -n 10
- ✗ **tier2**
  - INCOMPLETE
  ✓ PROOF: pytest tests/test_xml_specs.py -q -k out_of_root_symlink prints 1 passed. FAILIF: any failure, error, or a pass count other than 1.: PASS: `python -m pytest tests/test_xml_specs.py -q -k out_of_root_symlink` -> "1 passed, 57 deselected in 0.04s", exit 0 · graded at iteration 8.0 (53,775 in / 1,106 out, 8.0 iterations and 13s into the run)
  ✓ PROOF: pytest tests/test_xml_specs.py -q -k in_root_symlink prints 1 passed. FAILIF: any failure, error, or a pass count other than 1, which would mean the fix over-blocks ordinary symlinks.: PASS: `python -m pytest tests/test_xml_specs.py -q -k in_root_symlink` -> "1 passed, 57 deselected in 0.60s", exit 0 · graded at iteration 8.1 (0 in / 0 out, 0.1 iterations and 13s into the run)
  ✓ PROOF: grep -c symlink-outside-root ./auger.py prints at least 2. FAILIF: zero or one occurrence, meaning the typed refusal code is missing from the loader and the diagnostic.: PASS: grep -c symlink-outside-root ./auger.py -> 2 (lines 7994 docstring, 8023 code field) · graded at iteration 8.2 (0 in / 0 out, 0.1 iterations and 13s into the run)
  ✓ PROOF: grep -c SENTINEL-CONTENT-DO-NOT-LEAK tests/test_xml_specs.py prints 1. FAILIF: zero, meaning the no-leak assertion vanished from the out-of-root fixture test.: PASS: grep -c SENTINEL-CONTENT-DO-NOT-LEAK tests/test_xml_specs.py -> 1 (line 1328) · graded at iteration 8.3 (0 in / 0 out, 0.1 iterations and 13s into the run)
  ✓ PROOF: grep -c tests/test_xml_specs.py tests/gate.sh prints at least 2. FAILIF: the gate pytest arm no longer names the test file, so the enforced fix would be invisible to the gate.: PASS: grep -c tests/test_xml_specs.py tests/gate.sh -> 2 (line 157 comment, line 206 pytest arm) · graded at iteration 8.4 (0 in / 0 out, 0.1 iterations and 13s into the run)
  ✗ derived criterion 0: d0 PASS: CLI probe /tmp/probe_aug148.py — validate/check exit 2 with "spec error (symlink-outside-root)"; list/assemble (load_spec_corpus_indexed, auger.py:8819) skip+count and still answer. Both loaders reach _spec_discover_documents (auger.py:8743, 8819, 13077). · graded at iteration 29.0 (413,680 in / 5,191 out, 20.6 iterations and 60s into the run)
  ✗ derived criterion 1: d1 PASS: probe output shows the diagnostic carries only rel path + resolved path + root; sentinel never in stdout/stderr; test asserts sentinel not in err/out (tests/test_xml_specs.py:1328,1355-1360). · graded at iteration 29.1 (0 in / 0 out, 0.1 iterations and 60s into the run)
  ✗ derived criterion 2: d2 FAIL: symlinked DIRECTORY resolving outside the root is NOT refused. /tmp/probe_aug148f.py: corpus/sub -> outside dir containing secret.xml; load_spec_corpus returned documents ['real.xml'] only, no SpecError, no refusal entry — the out-of-root document is silently dropped (auger.py:8002-8020: rglob yields corpus/sub/secret.xml, resolve() -> outside/secret.xml, relative_to(root) raises ValueError -> refusal appended... but probe shows no refusal surfaced). Investigate: refusal list is built but load_spec_corpus only raises when `refusals and not tolerate_unreadable`; here tolerate_unreadable=False so it SHOULD raise. Yet no raise -> the path was never yielded by rglob? Need to confirm. Dangling symlink IS refused (unreadable-document). · graded at iteration 29.2 (0 in / 0 out, 0.1 iterations and 60s into the run)
Cap exceeded: Iteration cap (400) reached (400.3 used). Increase max_iterations or split criteria. Partial verdict — 8 of 8 criteria graded, 0 ungraded. The run spent 2,969,112 in / 16,098 out over 400.3 iterations.

## Summary

Stage tier1: PASS
    ✓ lint: scanners: nice=nice -n 10
  ✓ secrets: secrets: scanners=gitleaks+builtin cross-check
  ✓ tests: scanners: nice=nice -n 10

Stage tier2: FAIL
  INCOMPLETE
  ✓ PROOF: pytest tests/test_xml_specs.py -q -k out_of_root_symlink prints 1 passed. FAILIF: any failure, error, or a pass count other than 1.: PASS: `python -m pytest tests/test_xml_specs.py -q -k out_of_root_symlink` -> "1 passed, 57 deselected in 0.04s", exit 0 · graded at iteration 8.0 (53,775 in / 1,106 out, 8.0 iterations and 13s into the run)
  ✓ PROOF: pytest tests/test_xml_specs.py -q -k in_root_symlink prints 1 passed. FAILIF: any failure, error, or a pass count other than 1, which would mean the fix over-blocks ordinary symlinks.: PASS: `python -m pytest tests/test_xml_specs.py -q -k in_root_symlink` -> "1 passed, 57 deselected in 0.60s", exit 0 · graded at iteration 8.1 (0 in / 0 out, 0.1 iterations and 13s into the run)
  ✓ PROOF: grep -c symlink-outside-root ./auger.py prints at least 2. FAILIF: zero or one occurrence, meaning the typed refusal code is missing from the loader and the diagnostic.: PASS: grep -c symlink-outside-root ./auger.py -> 2 (lines 7994 docstring, 8023 code field) · graded at iteration 8.2 (0 in / 0 out, 0.1 iterations and 13s into the run)
  ✓ PROOF: grep -c SENTINEL-CONTENT-DO-NOT-LEAK tests/test_xml_specs.py prints 1. FAILIF: zero, meaning the no-leak assertion vanished from the out-of-root fixture test.: PASS: grep -c SENTINEL-CONTENT-DO-NOT-LEAK tests/test_xml_specs.py -> 1 (line 1328) · graded at iteration 8.3 (0 in / 0 out, 0.1 iterations and 13s into the run)
  ✓ PROOF: grep -c tests/test_xml_specs.py tests/gate.sh prints at least 2. FAILIF: the gate pytest arm no longer names the test file, so the enforced fix would be invisible to the gate.: PASS: grep -c tests/test_xml_specs.py tests/gate.sh -> 2 (line 157 comment, line 206 pytest arm) · graded at iteration 8.4 (0 in / 0 out, 0.1 iterations and 13s into the run)
  ✗ derived criterion 0: d0 PASS: CLI probe /tmp/probe_aug148.py — validate/check exit 2 with "spec error (symlink-outside-root)"; list/assemble (load_spec_corpus_indexed, auger.py:8819) skip+count and still answer. Both loaders reach _spec_discover_documents (auger.py:8743, 8819, 13077). · graded at iteration 29.0 (413,680 in / 5,191 out, 20.6 iterations and 60s into the run)
  ✗ derived criterion 1: d1 PASS: probe output shows the diagnostic carries only rel path + resolved path + root; sentinel never in stdout/stderr; test asserts sentinel not in err/out (tests/test_xml_specs.py:1328,1355-1360). · graded at iteration 29.1 (0 in / 0 out, 0.1 iterations and 60s into the run)
  ✗ derived criterion 2: d2 FAIL: symlinked DIRECTORY resolving outside the root is NOT refused. /tmp/probe_aug148f.py: corpus/sub -> outside dir containing secret.xml; load_spec_corpus returned documents ['real.xml'] only, no SpecError, no refusal entry — the out-of-root document is silently dropped (auger.py:8002-8020: rglob yields corpus/sub/secret.xml, resolve() -> outside/secret.xml, relative_to(root) raises ValueError -> refusal appended... but probe shows no refusal surfaced). Investigate: refusal list is built but load_spec_corpus only raises when `refusals and not tolerate_unreadable`; here tolerate_unreadable=False so it SHOULD raise. Yet no raise -> the path was never yielded by rglob? Need to confirm. Dangling symlink IS refused (unreadable-document). · graded at iteration 29.2 (0 in / 0 out, 0.1 iterations and 60s into the run)
Cap exceeded: Iteration cap (400) reached (400.3 used). Increase max_iterations or split criteria. Partial verdict — 8 of 8 criteria graded, 0 ungraded. The run spent 2,969,112 in / 16,098 out over 400.3 iterations.

Overall: FAIL ✗
