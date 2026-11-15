# Verdict: AUG-142

**Task:** The opt-in semantic finding path for spec align carries evidence, uncertainty and a no-write pin (AUG-142)
**Evaluated:** 2026-11-15T04:58:21.518605
**Result:** ✓ PASS

## Criteria

- ✓ **PASS: the AUG-142 proof file collects and is green — 9 tests, no skips.
PROOF: pytest tests/test_spec_semantic_findings.py -q prints 9 passed.
FAILIF: any failed or errored node, a collection error, or a summary other than 9 passed.**
  - PASS: `python3 -m pytest tests/test_spec_semantic_findings.py -q` -> "9 passed in 1.47s", exit 0, no skips.
- ✓ **PASS: a semantic contradiction is reported with every required field and the false-positive control stays silent.
PROOF: pytest tests/test_spec_semantic_findings.py -q -k every_required_field prints 1 passed and -k no_semantic_finding prints 1 passed, 8 deselected each.
FAILIF: either selection fails, is not collected, or reports 0 passed.**
  - PASS: -k every_required_field -> "1 passed, 8 deselected"; -k no_semantic_finding -> "1 passed, 8 deselected". Tests at tests/test_spec_semantic_findings.py:150 and :196.
- ✓ **PASS: a finding with no evidence excerpt is refused — at the constructor and end to end through the CLI.
PROOF: pytest tests/test_spec_semantic_findings.py -q -k no_citable_text prints 1 passed and -k without_an_evidence_excerpt prints 1 passed.
FAILIF: either selection fails, is not collected, or reports 0 passed.**
  - PASS: -k no_citable_text -> "1 passed, 8 deselected"; -k without_an_evidence_excerpt -> "1 passed, 8 deselected".
- ✓ **PASS: uncertainty is mandatory and validated — a missing band or score and an out-of-range value are all refused.
PROOF: pytest tests/test_spec_semantic_findings.py -q -k uncertainty_is_mandatory prints 1 passed.
FAILIF: 0 passed, a collection error, or any failure.**
  - PASS: -k uncertainty_is_mandatory -> "1 passed, 8 deselected".
- ✓ **PASS: a run that emits semantic findings leaves the corpus tree byte-identical (the checker cannot write).
PROOF: pytest tests/test_spec_semantic_findings.py -q -k writes_nothing prints 1 passed.
FAILIF: 0 passed, a collection error, or any failure.**
  - PASS: -k writes_nothing -> "1 passed, 8 deselected".
- ✓ **PASS: a semantic finding can never carry a pass verdict and its severity is warn or info only.
PROOF: pytest tests/test_spec_semantic_findings.py -q -k never_carries_a_pass_verdict prints 1 passed.
FAILIF: 0 passed, a collection error, or any failure.**
  - PASS: -k never_carries_a_pass_verdict -> "1 passed, 8 deselected".
- ✓ **PASS: the gate pytest arm names the proof file, so the gate and CI actually execute it.
PROOF: grep -c tests/test_spec_semantic_findings.py tests/gate.sh prints 1.
FAILIF: grep prints 0.**
  - PASS: grep -c tests/test_spec_semantic_findings.py tests/gate.sh -> 1 (gate.sh:206).
- ✓ **PASS: the new semantic fixture corpus participates in the both-verbs agreement table and the shipped corpus still validates.
PROOF: pytest tests/test_spec_validators_agree.py -q prints 27 passed, and python3 auger.py spec validate specs prints 3 document(s), 12 block(s), 7 reference(s) ok at rc 0.
FAILIF: any test failure, fewer than 27 passed, a non-zero corpus exit, or different counts.**
  - PASS: pytest tests/test_spec_validators_agree.py -q -> "27 passed in 6.84s"; python3 auger.py spec validate specs -> "spec: 3 document(s), 12 block(s), 7 reference(s) ok", rc=0.

## Pipeline Stages

- ✓ **tier1**
  -   ✓ lint: scanners: nice=nice -n 10
  ✓ secrets: secrets: scanners=gitleaks+builtin cross-check
  ✓ tests: scanners: nice=nice -n 10
- ✓ **tier2**
  - COMPLETE
  ✓ PASS: the AUG-142 proof file collects and is green — 9 tests, no skips.
PROOF: pytest tests/test_spec_semantic_findings.py -q prints 9 passed.
FAILIF: any failed or errored node, a collection error, or a summary other than 9 passed.: PASS: `python3 -m pytest tests/test_spec_semantic_findings.py -q` -> "9 passed in 1.47s", exit 0, no skips.
  ✓ PASS: a semantic contradiction is reported with every required field and the false-positive control stays silent.
PROOF: pytest tests/test_spec_semantic_findings.py -q -k every_required_field prints 1 passed and -k no_semantic_finding prints 1 passed, 8 deselected each.
FAILIF: either selection fails, is not collected, or reports 0 passed.: PASS: -k every_required_field -> "1 passed, 8 deselected"; -k no_semantic_finding -> "1 passed, 8 deselected". Tests at tests/test_spec_semantic_findings.py:150 and :196.
  ✓ PASS: a finding with no evidence excerpt is refused — at the constructor and end to end through the CLI.
PROOF: pytest tests/test_spec_semantic_findings.py -q -k no_citable_text prints 1 passed and -k without_an_evidence_excerpt prints 1 passed.
FAILIF: either selection fails, is not collected, or reports 0 passed.: PASS: -k no_citable_text -> "1 passed, 8 deselected"; -k without_an_evidence_excerpt -> "1 passed, 8 deselected".
  ✓ PASS: uncertainty is mandatory and validated — a missing band or score and an out-of-range value are all refused.
PROOF: pytest tests/test_spec_semantic_findings.py -q -k uncertainty_is_mandatory prints 1 passed.
FAILIF: 0 passed, a collection error, or any failure.: PASS: -k uncertainty_is_mandatory -> "1 passed, 8 deselected".
  ✓ PASS: a run that emits semantic findings leaves the corpus tree byte-identical (the checker cannot write).
PROOF: pytest tests/test_spec_semantic_findings.py -q -k writes_nothing prints 1 passed.
FAILIF: 0 passed, a collection error, or any failure.: PASS: -k writes_nothing -> "1 passed, 8 deselected".
  ✓ PASS: a semantic finding can never carry a pass verdict and its severity is warn or info only.
PROOF: pytest tests/test_spec_semantic_findings.py -q -k never_carries_a_pass_verdict prints 1 passed.
FAILIF: 0 passed, a collection error, or any failure.: PASS: -k never_carries_a_pass_verdict -> "1 passed, 8 deselected".
  ✓ PASS: the gate pytest arm names the proof file, so the gate and CI actually execute it.
PROOF: grep -c tests/test_spec_semantic_findings.py tests/gate.sh prints 1.
FAILIF: grep prints 0.: PASS: grep -c tests/test_spec_semantic_findings.py tests/gate.sh -> 1 (gate.sh:206).
  ✓ PASS: the new semantic fixture corpus participates in the both-verbs agreement table and the shipped corpus still validates.
PROOF: pytest tests/test_spec_validators_agree.py -q prints 27 passed, and python3 auger.py spec validate specs prints 3 document(s), 12 block(s), 7 reference(s) ok at rc 0.
FAILIF: any test failure, fewer than 27 passed, a non-zero corpus exit, or different counts.: PASS: pytest tests/test_spec_validators_agree.py -q -> "27 passed in 6.84s"; python3 auger.py spec validate specs -> "spec: 3 document(s), 12 block(s), 7 reference(s) ok", rc=0.
All eight AUG-142 criteria are verified green: the 9-test proof file passes with no skips, each targeted selection passes, the gate names the proof file, and the agreement table plus shipped corpus validation remain intact.

## Summary

Stage tier1: PASS
    ✓ lint: scanners: nice=nice -n 10
  ✓ secrets: secrets: scanners=gitleaks+builtin cross-check
  ✓ tests: scanners: nice=nice -n 10

Stage tier2: PASS
  COMPLETE
  ✓ PASS: the AUG-142 proof file collects and is green — 9 tests, no skips.
PROOF: pytest tests/test_spec_semantic_findings.py -q prints 9 passed.
FAILIF: any failed or errored node, a collection error, or a summary other than 9 passed.: PASS: `python3 -m pytest tests/test_spec_semantic_findings.py -q` -> "9 passed in 1.47s", exit 0, no skips.
  ✓ PASS: a semantic contradiction is reported with every required field and the false-positive control stays silent.
PROOF: pytest tests/test_spec_semantic_findings.py -q -k every_required_field prints 1 passed and -k no_semantic_finding prints 1 passed, 8 deselected each.
FAILIF: either selection fails, is not collected, or reports 0 passed.: PASS: -k every_required_field -> "1 passed, 8 deselected"; -k no_semantic_finding -> "1 passed, 8 deselected". Tests at tests/test_spec_semantic_findings.py:150 and :196.
  ✓ PASS: a finding with no evidence excerpt is refused — at the constructor and end to end through the CLI.
PROOF: pytest tests/test_spec_semantic_findings.py -q -k no_citable_text prints 1 passed and -k without_an_evidence_excerpt prints 1 passed.
FAILIF: either selection fails, is not collected, or reports 0 passed.: PASS: -k no_citable_text -> "1 passed, 8 deselected"; -k without_an_evidence_excerpt -> "1 passed, 8 deselected".
  ✓ PASS: uncertainty is mandatory and validated — a missing band or score and an out-of-range value are all refused.
PROOF: pytest tests/test_spec_semantic_findings.py -q -k uncertainty_is_mandatory prints 1 passed.
FAILIF: 0 passed, a collection error, or any failure.: PASS: -k uncertainty_is_mandatory -> "1 passed, 8 deselected".
  ✓ PASS: a run that emits semantic findings leaves the corpus tree byte-identical (the checker cannot write).
PROOF: pytest tests/test_spec_semantic_findings.py -q -k writes_nothing prints 1 passed.
FAILIF: 0 passed, a collection error, or any failure.: PASS: -k writes_nothing -> "1 passed, 8 deselected".
  ✓ PASS: a semantic finding can never carry a pass verdict and its severity is warn or info only.
PROOF: pytest tests/test_spec_semantic_findings.py -q -k never_carries_a_pass_verdict prints 1 passed.
FAILIF: 0 passed, a collection error, or any failure.: PASS: -k never_carries_a_pass_verdict -> "1 passed, 8 deselected".
  ✓ PASS: the gate pytest arm names the proof file, so the gate and CI actually execute it.
PROOF: grep -c tests/test_spec_semantic_findings.py tests/gate.sh prints 1.
FAILIF: grep prints 0.: PASS: grep -c tests/test_spec_semantic_findings.py tests/gate.sh -> 1 (gate.sh:206).
  ✓ PASS: the new semantic fixture corpus participates in the both-verbs agreement table and the shipped corpus still validates.
PROOF: pytest tests/test_spec_validators_agree.py -q prints 27 passed, and python3 auger.py spec validate specs prints 3 document(s), 12 block(s), 7 reference(s) ok at rc 0.
FAILIF: any test failure, fewer than 27 passed, a non-zero corpus exit, or different counts.: PASS: pytest tests/test_spec_validators_agree.py -q -> "27 passed in 6.84s"; python3 auger.py spec validate specs -> "spec: 3 document(s), 12 block(s), 7 reference(s) ok", rc=0.
All eight AUG-142 criteria are verified green: the 9-test proof file passes with no skips, each targeted selection passes, the gate names the proof file, and the agreement table plus shipped corpus validation remain intact.

Overall: PASS ✓
