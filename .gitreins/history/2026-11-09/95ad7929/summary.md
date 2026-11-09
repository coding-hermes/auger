# Verdict: AUG-135

**Task:** AUG-135 verified: auger spec align accepts GitReins' real checker argv (--repo-root/--spec-root/--protocol-version/--diff-stdin) and emits machine-readable findings
**Evaluated:** 2026-11-09T02:01:23.253353
**Result:** ✓ PASS

## Criteria

- ✓ **The real-argv seam runs end to end: the argv GitReins itself builds (engine.spec_alignment) drives the real auger binary and gets machine-readable findings back. PROOF: cd /home/kara/auger && /home/kara/auger/.venv/bin/python -m pytest tests/test_spec_alignment_check.py::test_gitreins_real_invocation_against_the_real_auger_binary -q FAILIF: the test fails, errors, or the reported pass count is not 1.**
  - PASS: pytest tests/test_spec_alignment_check.py::test_gitreins_real_invocation_against_the_real_auger_binary -q -> "1 passed in 0.71s", exit 0. Test imports engine.spec_alignment from /home/kara/gitreins and calls run_checker with real argv.
- ✓ **The checker file is load-bearing, not an unrun proof: a committed test pins tests/test_spec_alignment_check.py into the gate pytest arm. PROOF: cd /home/kara/auger && /home/kara/auger/.venv/bin/python -m pytest tests/test_spec_alignment_check.py::test_gate_arm_runs_this_file -q FAILIF: the test fails or the reported pass count is not 1.**
  - PASS: pytest ...::test_gate_arm_runs_this_file -q -> "1 passed in 0.01s", exit 0.
- ✓ **Protocol negotiation fails closed: a version this checker does not speak is refused with a typed error on stderr and no payload on stdout. PROOF: cd /home/kara/auger && PATH=/home/kara/auger/.venv/bin:$PATH python3 auger.py spec align --repo-root . --spec-root specs --protocol-version 99 --diff-stdin < /dev/null ; echo rc=$? FAILIF: rc is not 2, stdout is not empty, or stderr does not contain spec error (unsupported-protocol-version).**
  - PASS: rc=2, stdout empty, stderr "spec error (unsupported-protocol-version): requested '99'; this checker speaks 1 (checker version 1.0.0)"
- ✓ **The whole staged-diff checker suite is green at the judged tree, including the five fixture cases and the ch:trace contract. PROOF: cd /home/kara/auger && /home/kara/auger/.venv/bin/python -m pytest tests/test_spec_alignment_check.py -q FAILIF: any test in that file fails or the reported pass count is not 22.**
  - PASS: pytest tests/test_spec_alignment_check.py -q -> "22 passed in 4.29s", exit 0.

## Pipeline Stages

- ✓ **tier1**
  -   ✓ lint: scanners: nice=nice -n 10
  ✓ secrets: secrets: scanners=gitleaks+builtin cross-check
  ✓ tests: scanners: nice=nice -n 10
- ✓ **tier2**
  - COMPLETE
  ✓ The real-argv seam runs end to end: the argv GitReins itself builds (engine.spec_alignment) drives the real auger binary and gets machine-readable findings back. PROOF: cd /home/kara/auger && /home/kara/auger/.venv/bin/python -m pytest tests/test_spec_alignment_check.py::test_gitreins_real_invocation_against_the_real_auger_binary -q FAILIF: the test fails, errors, or the reported pass count is not 1.: PASS: pytest tests/test_spec_alignment_check.py::test_gitreins_real_invocation_against_the_real_auger_binary -q -> "1 passed in 0.71s", exit 0. Test imports engine.spec_alignment from /home/kara/gitreins and calls run_checker with real argv.
  ✓ The checker file is load-bearing, not an unrun proof: a committed test pins tests/test_spec_alignment_check.py into the gate pytest arm. PROOF: cd /home/kara/auger && /home/kara/auger/.venv/bin/python -m pytest tests/test_spec_alignment_check.py::test_gate_arm_runs_this_file -q FAILIF: the test fails or the reported pass count is not 1.: PASS: pytest ...::test_gate_arm_runs_this_file -q -> "1 passed in 0.01s", exit 0.
  ✓ Protocol negotiation fails closed: a version this checker does not speak is refused with a typed error on stderr and no payload on stdout. PROOF: cd /home/kara/auger && PATH=/home/kara/auger/.venv/bin:$PATH python3 auger.py spec align --repo-root . --spec-root specs --protocol-version 99 --diff-stdin < /dev/null ; echo rc=$? FAILIF: rc is not 2, stdout is not empty, or stderr does not contain spec error (unsupported-protocol-version).: PASS: rc=2, stdout empty, stderr "spec error (unsupported-protocol-version): requested '99'; this checker speaks 1 (checker version 1.0.0)"
  ✓ The whole staged-diff checker suite is green at the judged tree, including the five fixture cases and the ch:trace contract. PROOF: cd /home/kara/auger && /home/kara/auger/.venv/bin/python -m pytest tests/test_spec_alignment_check.py -q FAILIF: any test in that file fails or the reported pass count is not 22.: PASS: pytest tests/test_spec_alignment_check.py -q -> "22 passed in 4.29s", exit 0.
All four criteria verified with fresh command output: the real-argv seam, gate-arm pinning, fail-closed protocol negotiation, and the full 22-test checker suite are green.

## Summary

Stage tier1: PASS
    ✓ lint: scanners: nice=nice -n 10
  ✓ secrets: secrets: scanners=gitleaks+builtin cross-check
  ✓ tests: scanners: nice=nice -n 10

Stage tier2: PASS
  COMPLETE
  ✓ The real-argv seam runs end to end: the argv GitReins itself builds (engine.spec_alignment) drives the real auger binary and gets machine-readable findings back. PROOF: cd /home/kara/auger && /home/kara/auger/.venv/bin/python -m pytest tests/test_spec_alignment_check.py::test_gitreins_real_invocation_against_the_real_auger_binary -q FAILIF: the test fails, errors, or the reported pass count is not 1.: PASS: pytest tests/test_spec_alignment_check.py::test_gitreins_real_invocation_against_the_real_auger_binary -q -> "1 passed in 0.71s", exit 0. Test imports engine.spec_alignment from /home/kara/gitreins and calls run_checker with real argv.
  ✓ The checker file is load-bearing, not an unrun proof: a committed test pins tests/test_spec_alignment_check.py into the gate pytest arm. PROOF: cd /home/kara/auger && /home/kara/auger/.venv/bin/python -m pytest tests/test_spec_alignment_check.py::test_gate_arm_runs_this_file -q FAILIF: the test fails or the reported pass count is not 1.: PASS: pytest ...::test_gate_arm_runs_this_file -q -> "1 passed in 0.01s", exit 0.
  ✓ Protocol negotiation fails closed: a version this checker does not speak is refused with a typed error on stderr and no payload on stdout. PROOF: cd /home/kara/auger && PATH=/home/kara/auger/.venv/bin:$PATH python3 auger.py spec align --repo-root . --spec-root specs --protocol-version 99 --diff-stdin < /dev/null ; echo rc=$? FAILIF: rc is not 2, stdout is not empty, or stderr does not contain spec error (unsupported-protocol-version).: PASS: rc=2, stdout empty, stderr "spec error (unsupported-protocol-version): requested '99'; this checker speaks 1 (checker version 1.0.0)"
  ✓ The whole staged-diff checker suite is green at the judged tree, including the five fixture cases and the ch:trace contract. PROOF: cd /home/kara/auger && /home/kara/auger/.venv/bin/python -m pytest tests/test_spec_alignment_check.py -q FAILIF: any test in that file fails or the reported pass count is not 22.: PASS: pytest tests/test_spec_alignment_check.py -q -> "22 passed in 4.29s", exit 0.
All four criteria verified with fresh command output: the real-argv seam, gate-arm pinning, fail-closed protocol negotiation, and the full 22-test checker suite are green.

Overall: PASS ✓
