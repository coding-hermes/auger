# Verdict: AUG-157

**Task:** AUG-157 verified: spec repository root resolves lazily so obligation-free corpora need no git repo
**Evaluated:** 2026-11-08T02:01:29.502478
**Result:** ✗ FAIL

## Criteria

- ✓ **AUG-157 lazy repository root at CLI level: an obligation-free corpus validates outside any git repository. PROOF: d=$(mktemp -d); cp -r /home/kara/auger/examples/xml $d/; cd $d; /home/kara/auger/.venv/bin/python /home/kara/auger/auger.py spec validate . ; /home/kara/auger/.venv/bin/python /home/kara/auger/auger.py spec check . FAILIF: either verb exits non-zero, either stderr names unresolvable-repo-root, or validate does not print "spec: 1 document(s), 2 block(s), 1 reference(s) ok".**
  - PASS: mktemp -d copy of examples/xml outside git (git rev-parse fatal: not a git repository); `spec validate .` printed "spec: 1 document(s), 2 block(s), 1 reference(s) ok" RC_VALIDATE=0; `spec check .` printed {"status":"valid",...} RC_CHECK=0; no unresolvable-repo-root on stderr.
- ✓ **AUG-127 obligation invariant preserved: a corpus naming a tracked test still resolves against a repo root. PROOF: cd /home/kara/auger && PATH=/home/kara/auger/.venv/bin:$PATH DUCKBRAIN_URL=http://127.0.0.1:3000 DUCKBRAIN_API_KEY=$(cat ~/.duckbrain/foreman-status.token) /home/kara/auger/.venv/bin/python -m pytest tests/test_spec_repository_root.py -q FAILIF: any test in that file fails or the reported pass count is not 9.**
  - PASS: pytest tests/test_spec_repository_root.py -q -> "9 passed in 4.77s", exit 0. Includes test_an_obligation_free_corpus_validates_outside_any_repository and the AUG-127 obligation-carrying refusal tests.
- ✓ **Obligation gate is unaffected by the lazy root. PROOF: cd /home/kara/auger && PATH=/home/kara/auger/.venv/bin:$PATH DUCKBRAIN_URL=http://127.0.0.1:3000 DUCKBRAIN_API_KEY=$(cat ~/.duckbrain/foreman-status.token) /home/kara/auger/.venv/bin/python -m pytest tests/test_spec_validate_obligations.py tests/test_spec_validators_agree.py -q FAILIF: any test fails or the combined pass count is not 31.**
  - PASS: pytest tests/test_spec_validate_obligations.py tests/test_spec_validators_agree.py -q -> "31 passed in 9.21s", exit 0.
- ✓ **Shipped authored corpus still validates and checks clean on this tree. PROOF: cd /home/kara/auger && /home/kara/auger/.venv/bin/python auger.py spec validate specs && /home/kara/auger/.venv/bin/python auger.py spec check specs FAILIF: either exits non-zero, validate does not print "3 document(s), 12 block(s), 7 reference(s)", or check reports a status other than valid.**
  - PASS: `auger.py spec validate specs` -> "spec: 3 document(s), 12 block(s), 7 reference(s) ok" RC=0; `spec check specs` -> {"status": "valid", ...} RC=0.
- ✓ **Packaging surface untouched by the change. PROOF: cd /home/kara/auger && /home/kara/auger/.venv/bin/python -m pytest tests/test_packaging_wheel.py -q FAILIF: any test fails or the reported pass count is not 6.**
  - PASS: pytest tests/test_packaging_wheel.py -q -> "6 passed in 4.82s", exit 0.
- ✓ **The documented claim is kept true and the resolution is stated in the docs. PROOF: grep -c "validates outside any repository" /home/kara/auger/examples/xml/minimal.xml ; grep -c -- "--repo-root" /home/kara/auger/docs/XML-SPEC-SCHEMA.md FAILIF: the example claim count is not 1 (resolution A keeps the documented claim true instead of deleting it) or the schema document mentions --repo-root fewer than once.**
  - PASS: grep -c "validates outside any repository" examples/xml/minimal.xml = 1; grep -c -- "--repo-root" docs/XML-SPEC-SCHEMA.md = 12 (>=1). docs/XML-SPEC-SCHEMA.md:100-106 states lazy resolution (AUG-157).

## Pipeline Stages

- ✗ **tier1**
  -   ✗ lint: scanners: nice=nice -n 10
  ✗ secrets: secrets: scanners=gitleaks+builtin cross-check
  ✓ tests: scanners: nice=nice -n 10
- ✓ **tier2**
  - COMPLETE
  ✓ AUG-157 lazy repository root at CLI level: an obligation-free corpus validates outside any git repository. PROOF: d=$(mktemp -d); cp -r /home/kara/auger/examples/xml $d/; cd $d; /home/kara/auger/.venv/bin/python /home/kara/auger/auger.py spec validate . ; /home/kara/auger/.venv/bin/python /home/kara/auger/auger.py spec check . FAILIF: either verb exits non-zero, either stderr names unresolvable-repo-root, or validate does not print "spec: 1 document(s), 2 block(s), 1 reference(s) ok".: PASS: mktemp -d copy of examples/xml outside git (git rev-parse fatal: not a git repository); `spec validate .` printed "spec: 1 document(s), 2 block(s), 1 reference(s) ok" RC_VALIDATE=0; `spec check .` printed {"status":"valid",...} RC_CHECK=0; no unresolvable-repo-root on stderr.
  ✓ AUG-127 obligation invariant preserved: a corpus naming a tracked test still resolves against a repo root. PROOF: cd /home/kara/auger && PATH=/home/kara/auger/.venv/bin:$PATH DUCKBRAIN_URL=http://127.0.0.1:3000 DUCKBRAIN_API_KEY=$(cat ~/.duckbrain/foreman-status.token) /home/kara/auger/.venv/bin/python -m pytest tests/test_spec_repository_root.py -q FAILIF: any test in that file fails or the reported pass count is not 9.: PASS: pytest tests/test_spec_repository_root.py -q -> "9 passed in 4.77s", exit 0. Includes test_an_obligation_free_corpus_validates_outside_any_repository and the AUG-127 obligation-carrying refusal tests.
  ✓ Obligation gate is unaffected by the lazy root. PROOF: cd /home/kara/auger && PATH=/home/kara/auger/.venv/bin:$PATH DUCKBRAIN_URL=http://127.0.0.1:3000 DUCKBRAIN_API_KEY=$(cat ~/.duckbrain/foreman-status.token) /home/kara/auger/.venv/bin/python -m pytest tests/test_spec_validate_obligations.py tests/test_spec_validators_agree.py -q FAILIF: any test fails or the combined pass count is not 31.: PASS: pytest tests/test_spec_validate_obligations.py tests/test_spec_validators_agree.py -q -> "31 passed in 9.21s", exit 0.
  ✓ Shipped authored corpus still validates and checks clean on this tree. PROOF: cd /home/kara/auger && /home/kara/auger/.venv/bin/python auger.py spec validate specs && /home/kara/auger/.venv/bin/python auger.py spec check specs FAILIF: either exits non-zero, validate does not print "3 document(s), 12 block(s), 7 reference(s)", or check reports a status other than valid.: PASS: `auger.py spec validate specs` -> "spec: 3 document(s), 12 block(s), 7 reference(s) ok" RC=0; `spec check specs` -> {"status": "valid", ...} RC=0.
  ✓ Packaging surface untouched by the change. PROOF: cd /home/kara/auger && /home/kara/auger/.venv/bin/python -m pytest tests/test_packaging_wheel.py -q FAILIF: any test fails or the reported pass count is not 6.: PASS: pytest tests/test_packaging_wheel.py -q -> "6 passed in 4.82s", exit 0.
  ✓ The documented claim is kept true and the resolution is stated in the docs. PROOF: grep -c "validates outside any repository" /home/kara/auger/examples/xml/minimal.xml ; grep -c -- "--repo-root" /home/kara/auger/docs/XML-SPEC-SCHEMA.md FAILIF: the example claim count is not 1 (resolution A keeps the documented claim true instead of deleting it) or the schema document mentions --repo-root fewer than once.: PASS: grep -c "validates outside any repository" examples/xml/minimal.xml = 1; grep -c -- "--repo-root" docs/XML-SPEC-SCHEMA.md = 12 (>=1). docs/XML-SPEC-SCHEMA.md:100-106 states lazy resolution (AUG-157).
All six criteria verified with fresh command output: obligation-free corpora validate outside git, obligation-carrying corpora still resolve, the obligation gate and shipped corpus are unaffected, packaging tests pass, and the docs/example claim remains true.

## Summary

Judge Result: AUG-157

Stage tier1: FAIL
    ✗ lint: scanners: nice=nice -n 10
  ✗ secrets: secrets: scanners=gitleaks+builtin cross-check
  ✓ tests: scanners: nice=nice -n 10

Stage tier2: PASS
  COMPLETE
  ✓ AUG-157 lazy repository root at CLI level: an obligation-free corpus validates outside any git repository. PROOF: d=$(mktemp -d); cp -r /home/kara/auger/examples/xml $d/; cd $d; /home/kara/auger/.venv/bin/python /home/kara/auger/auger.py spec validate . ; /home/kara/auger/.venv/bin/python /home/kara/auger/auger.py spec check . FAILIF: either verb exits non-zero, either stderr names unresolvable-repo-root, or validate does not print "spec: 1 document(s), 2 block(s), 1 reference(s) ok".: PASS: mktemp -d copy of examples/xml outside git (git rev-parse fatal: not a git repository); `spec validate .` printed "spec: 1 document(s), 2 block(s), 1 reference(s) ok" RC_VALIDATE=0; `spec check .` printed {"status":"valid",...} RC_CHECK=0; no unresolvable-repo-root on stderr.
  ✓ AUG-127 obligation invariant preserved: a corpus naming a tracked test still resolves against a repo root. PROOF: cd /home/kara/auger && PATH=/home/kara/auger/.venv/bin:$PATH DUCKBRAIN_URL=http://127.0.0.1:3000 DUCKBRAIN_API_KEY=$(cat ~/.duckbrain/foreman-status.token) /home/kara/auger/.venv/bin/python -m pytest tests/test_spec_repository_root.py -q FAILIF: any test in that file fails or the reported pass count is not 9.: PASS: pytest tests/test_spec_repository_root.py -q -> "9 passed in 4.77s", exit 0. Includes test_an_obligation_free_corpus_validates_outside_any_repository and the AUG-127 obligation-carrying refusal tests.
  ✓ Obligation gate is unaffected by the lazy root. PROOF: cd /home/kara/auger && PATH=/home/kara/auger/.venv/bin:$PATH DUCKBRAIN_URL=http://127.0.0.1:3000 DUCKBRAIN_API_KEY=$(cat ~/.duckbrain/foreman-status.token) /home/kara/auger/.venv/bin/python -m pytest tests/test_spec_validate_obligations.py tests/test_spec_validators_agree.py -q FAILIF: any test fails or the combined pass count is not 31.: PASS: pytest tests/test_spec_validate_obligations.py tests/test_spec_validators_agree.py -q -> "31 passed in 9.21s", exit 0.
  ✓ Shipped authored corpus still validates and checks clean on this tree. PROOF: cd /home/kara/auger && /home/kara/auger/.venv/bin/python auger.py spec validate specs && /home/kara/auger/.venv/bin/python auger.py spec check specs FAILIF: either exits non-zero, validate does not print "3 document(s), 12 block(s), 7 reference(s)", or check reports a status other than valid.: PASS: `auger.py spec validate specs` -> "spec: 3 document(s), 12 block(s), 7 reference(s) ok" RC=0; `spec check specs` -> {"status": "valid", ...} RC=0.
  ✓ Packaging surface untouched by the change. PROOF: cd /home/kara/auger && /home/kara/auger/.venv/bin/python -m pytest tests/test_packaging_wheel.py -q FAILIF: any test fails or the reported pass count is not 6.: PASS: pytest tests/test_packaging_wheel.py -q -> "6 passed in 4.82s", exit 0.
  ✓ The documented claim is kept true and the resolution is stated in the docs. PROOF: grep -c "validates outside any repository" /home/kara/auger/examples/xml/minimal.xml ; grep -c -- "--repo-root" /home/kara/auger/docs/XML-SPEC-SCHEMA.md FAILIF: the example claim count is not 1 (resolution A keeps the documented claim true instead of deleting it) or the schema document mentions --repo-root fewer than once.: PASS: grep -c "validates outside any repository" examples/xml/minimal.xml = 1; grep -c -- "--repo-root" docs/XML-SPEC-SCHEMA.md = 12 (>=1). docs/XML-SPEC-SCHEMA.md:100-106 states lazy resolution (AUG-157).
All six criteria verified with fresh command output: obligation-free corpora validate outside git, obligation-carrying corpora still resolve, the obligation gate and shipped corpus are unaffected, packaging tests pass, and the docs/example claim remains true.

Overall: FAIL ✗
