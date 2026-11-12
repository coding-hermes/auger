# Verdict: AUG-144

**Task:** AUG-144: spec bundle resolves a namespaced-corpus requirement, follows the inverse verifies edge, and refuses an unknown id with its own typed code
**Evaluated:** 2026-11-12T20:17:26.447186
**Result:** ✓ PASS

## Criteria

- ✓ **PROOF: PATH=$PWD/.venv/bin:$PATH python3 -m pytest tests/test_xml_specs.py -k bundle -q prints 9 passed and exits 0. FAILIF: any failure or error, or a passed count other than 9.**
  - PASS: `PATH=$PWD/.venv/bin:$PATH python3 -m pytest tests/test_xml_specs.py -k bundle -q` -> "9 passed, 49 deselected in 0.45s", exit 0.
- ✓ **PROOF: python3 auger.py spec bundle tests/fixtures/xml/specs AUG-REQ-014 exits 0 and its JSON names id AUG-REQ-014 with status ok and both nodes AUG-AC-014-1 and AUG-VER-014-1. FAILIF: nonzero exit, or either node id missing from the verifications array.**
  - PASS: `python3 auger.py spec bundle tests/fixtures/xml/specs AUG-REQ-014` exit 0; JSON {"count":2,"id":"AUG-REQ-014","status":"ok","verifications":[{"id":"AUG-AC-014-1",...},{"id":"AUG-VER-014-1",...}]} — both node ids present.
- ✓ **PROOF: python3 auger.py spec bundle tests/fixtures/xml/specs AUG-NOT-A-REQUIREMENT exits 2 and stderr carries the typed line spec error (unknown-requirement). FAILIF: exit 0, or a diagnostic without that typed code.**
  - PASS: `python3 auger.py spec bundle tests/fixtures/xml/specs AUG-NOT-A-REQUIREMENT` exit 2; stdout empty; stderr = "spec error (unknown-requirement): AUG-NOT-A-REQUIREMENT is not a requirement block in this corpus".
- ✓ **PROOF: python3 auger.py spec check tests/fixtures/xml/specs exits 0 with status valid and lists AUG-REQ-014 among its nodes, so check and bundle agree on the same id. FAILIF: nonzero exit, status other than valid, or AUG-REQ-014 absent from nodes.**
  - PASS: `python3 auger.py spec check tests/fixtures/xml/specs` exit 0; JSON {"nodes": ["AUG-REQ-014", "AUG-REQ-020", "AUG-REQ-021", "AUG-REQ-030", "AUG-SHARED-CITATION", "AUG-SHARED-DETERMINISM", "AUG-VER-014-1"], "rollback": {}, "status": "valid"} — AUG-REQ-014 present, status valid.
- ✓ **PROOF: grep -c tests/test_xml_specs.py tests/gate.sh prints 2, so the regression file is named in the gates pytest arm. FAILIF: a count below 2.**
  - PASS: `grep -c tests/test_xml_specs.py tests/gate.sh` -> 2. Line 157 (comment) and line 206 (pytest arm invocation).

## Pipeline Stages

- ✓ **tier1**
  -   ✓ lint: scanners: nice=nice -n 10
  ✓ secrets: secrets: scanners=gitleaks+builtin cross-check
  ✓ tests: scanners: nice=nice -n 10
- ✓ **tier2**
  - COMPLETE
  ✓ PROOF: PATH=$PWD/.venv/bin:$PATH python3 -m pytest tests/test_xml_specs.py -k bundle -q prints 9 passed and exits 0. FAILIF: any failure or error, or a passed count other than 9.: PASS: `PATH=$PWD/.venv/bin:$PATH python3 -m pytest tests/test_xml_specs.py -k bundle -q` -> "9 passed, 49 deselected in 0.45s", exit 0.
  ✓ PROOF: python3 auger.py spec bundle tests/fixtures/xml/specs AUG-REQ-014 exits 0 and its JSON names id AUG-REQ-014 with status ok and both nodes AUG-AC-014-1 and AUG-VER-014-1. FAILIF: nonzero exit, or either node id missing from the verifications array.: PASS: `python3 auger.py spec bundle tests/fixtures/xml/specs AUG-REQ-014` exit 0; JSON {"count":2,"id":"AUG-REQ-014","status":"ok","verifications":[{"id":"AUG-AC-014-1",...},{"id":"AUG-VER-014-1",...}]} — both node ids present.
  ✓ PROOF: python3 auger.py spec bundle tests/fixtures/xml/specs AUG-NOT-A-REQUIREMENT exits 2 and stderr carries the typed line spec error (unknown-requirement). FAILIF: exit 0, or a diagnostic without that typed code.: PASS: `python3 auger.py spec bundle tests/fixtures/xml/specs AUG-NOT-A-REQUIREMENT` exit 2; stdout empty; stderr = "spec error (unknown-requirement): AUG-NOT-A-REQUIREMENT is not a requirement block in this corpus".
  ✓ PROOF: python3 auger.py spec check tests/fixtures/xml/specs exits 0 with status valid and lists AUG-REQ-014 among its nodes, so check and bundle agree on the same id. FAILIF: nonzero exit, status other than valid, or AUG-REQ-014 absent from nodes.: PASS: `python3 auger.py spec check tests/fixtures/xml/specs` exit 0; JSON {"nodes": ["AUG-REQ-014", "AUG-REQ-020", "AUG-REQ-021", "AUG-REQ-030", "AUG-SHARED-CITATION", "AUG-SHARED-DETERMINISM", "AUG-VER-014-1"], "rollback": {}, "status": "valid"} — AUG-REQ-014 present, status valid.
  ✓ PROOF: grep -c tests/test_xml_specs.py tests/gate.sh prints 2, so the regression file is named in the gates pytest arm. FAILIF: a count below 2.: PASS: `grep -c tests/test_xml_specs.py tests/gate.sh` -> 2. Line 157 (comment) and line 206 (pytest arm invocation).
All five criteria verified with fresh command output: the bundle tests pass (9 passed, exit 0), bundle/check agree on AUG-REQ-014, the unknown-requirement path exits 2 with the typed diagnostic, and the regression file is wired into tests/gate.sh.

## Summary

Stage tier1: PASS
    ✓ lint: scanners: nice=nice -n 10
  ✓ secrets: secrets: scanners=gitleaks+builtin cross-check
  ✓ tests: scanners: nice=nice -n 10

Stage tier2: PASS
  COMPLETE
  ✓ PROOF: PATH=$PWD/.venv/bin:$PATH python3 -m pytest tests/test_xml_specs.py -k bundle -q prints 9 passed and exits 0. FAILIF: any failure or error, or a passed count other than 9.: PASS: `PATH=$PWD/.venv/bin:$PATH python3 -m pytest tests/test_xml_specs.py -k bundle -q` -> "9 passed, 49 deselected in 0.45s", exit 0.
  ✓ PROOF: python3 auger.py spec bundle tests/fixtures/xml/specs AUG-REQ-014 exits 0 and its JSON names id AUG-REQ-014 with status ok and both nodes AUG-AC-014-1 and AUG-VER-014-1. FAILIF: nonzero exit, or either node id missing from the verifications array.: PASS: `python3 auger.py spec bundle tests/fixtures/xml/specs AUG-REQ-014` exit 0; JSON {"count":2,"id":"AUG-REQ-014","status":"ok","verifications":[{"id":"AUG-AC-014-1",...},{"id":"AUG-VER-014-1",...}]} — both node ids present.
  ✓ PROOF: python3 auger.py spec bundle tests/fixtures/xml/specs AUG-NOT-A-REQUIREMENT exits 2 and stderr carries the typed line spec error (unknown-requirement). FAILIF: exit 0, or a diagnostic without that typed code.: PASS: `python3 auger.py spec bundle tests/fixtures/xml/specs AUG-NOT-A-REQUIREMENT` exit 2; stdout empty; stderr = "spec error (unknown-requirement): AUG-NOT-A-REQUIREMENT is not a requirement block in this corpus".
  ✓ PROOF: python3 auger.py spec check tests/fixtures/xml/specs exits 0 with status valid and lists AUG-REQ-014 among its nodes, so check and bundle agree on the same id. FAILIF: nonzero exit, status other than valid, or AUG-REQ-014 absent from nodes.: PASS: `python3 auger.py spec check tests/fixtures/xml/specs` exit 0; JSON {"nodes": ["AUG-REQ-014", "AUG-REQ-020", "AUG-REQ-021", "AUG-REQ-030", "AUG-SHARED-CITATION", "AUG-SHARED-DETERMINISM", "AUG-VER-014-1"], "rollback": {}, "status": "valid"} — AUG-REQ-014 present, status valid.
  ✓ PROOF: grep -c tests/test_xml_specs.py tests/gate.sh prints 2, so the regression file is named in the gates pytest arm. FAILIF: a count below 2.: PASS: `grep -c tests/test_xml_specs.py tests/gate.sh` -> 2. Line 157 (comment) and line 206 (pytest arm invocation).
All five criteria verified with fresh command output: the bundle tests pass (9 passed, exit 0), bundle/check agree on AUG-REQ-014, the unknown-requirement path exits 2 with the typed diagnostic, and the regression file is wired into tests/gate.sh.

Overall: PASS ✓
