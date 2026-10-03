# Verdict: AUG-060

**Task:** README Substrate pin: shared-host port safety (non-default port + free check)
**Evaluated:** 2026-10-03T09:24:16.939031
**Result:** ✗ FAIL

## Pipeline Stages

- ✗ **tier1**
  -   ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✗ tests: FAIL (tests/test_auger.py::test_no_test_namespace_survives_the_suite [first failing id])
- ✓ **tier2**
  - COMPLETE
  ✓ README.md Substrate pin gives the boot line a non-default port for shared hosts with the WHY (fixed 127.0.0.1:3000 collides on multi-user boxes and a stranger's daemon answers), a port-free check line, and AUGER_DUCKBRAIN_URL pinning guidance; docs_check still passes: README.md:145-156 'Substrate port on shared hosts' block: WHY present (lines 148-150: 'that fixed port collides... the requests meant for your instance get answered by *someone else's* daemon'); non-default boot line README.md:155 'node bin/duckbrain.js http --port 3987'; port-free check README.md:154 'ss -tlnp | grep :3987  # must print nothing before you boot on it'; pinning guidance README.md:156 'export DUCKBRAIN_URL=http://127.0.0.1:3987'. Note: the criterion names AUGER_DUCKBRAIN_URL, but that var is read by nothing (grep finds it nowhere in README/docs/auger.py); auger.py:64 reads DUCKBRAIN_URL, so the rework commit aa85747 deliberately corrected the export to the functional var — the pinning guidance is present and actually pins auger to the verified-free port, satisfying the criterion's intent. docs_check verified fresh: `bash tests/docs_check.sh` exit_code=0, output 'PASS: README claims match code — fences ok (10/14 bash-labeled parse under bash -n; 6 live-service parsed only — never executed), loop verbs ok (14 listed, 16 shipped), VERBS.md registry ok, tally ok (14 tables), 5 env/path refs ok'.
README Substrate pin documents the shared-host non-default port (3987) with the collision WHY, an ss port-free check, and functional pinning guidance via the env var auger actually reads (DUCKBRAIN_URL), and docs_check passes (exit 0).

## Summary

Judge Result: AUG-060

Stage tier1: FAIL
    ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✗ tests: FAIL (tests/test_auger.py::test_no_test_namespace_survives_the_suite [first failing id])

Stage tier2: PASS
  COMPLETE
  ✓ README.md Substrate pin gives the boot line a non-default port for shared hosts with the WHY (fixed 127.0.0.1:3000 collides on multi-user boxes and a stranger's daemon answers), a port-free check line, and AUGER_DUCKBRAIN_URL pinning guidance; docs_check still passes: README.md:145-156 'Substrate port on shared hosts' block: WHY present (lines 148-150: 'that fixed port collides... the requests meant for your instance get answered by *someone else's* daemon'); non-default boot line README.md:155 'node bin/duckbrain.js http --port 3987'; port-free check README.md:154 'ss -tlnp | grep :3987  # must print nothing before you boot on it'; pinning guidance README.md:156 'export DUCKBRAIN_URL=http://127.0.0.1:3987'. Note: the criterion names AUGER_DUCKBRAIN_URL, but that var is read by nothing (grep finds it nowhere in README/docs/auger.py); auger.py:64 reads DUCKBRAIN_URL, so the rework commit aa85747 deliberately corrected the export to the functional var — the pinning guidance is present and actually pins auger to the verified-free port, satisfying the criterion's intent. docs_check verified fresh: `bash tests/docs_check.sh` exit_code=0, output 'PASS: README claims match code — fences ok (10/14 bash-labeled parse under bash -n; 6 live-service parsed only — never executed), loop verbs ok (14 listed, 16 shipped), VERBS.md registry ok, tally ok (14 tables), 5 env/path refs ok'.
README Substrate pin documents the shared-host non-default port (3987) with the collision WHY, an ss port-free check, and functional pinning guidance via the env var auger actually reads (DUCKBRAIN_URL), and docs_check passes (exit 0).

Overall: FAIL ✗
