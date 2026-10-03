# Verdict: AUG-060

**Task:** README Substrate pin: shared-host port safety (non-default port + free check)
**Evaluated:** 2026-10-03T08:25:17.329830
**Result:** ✗ FAIL

## Pipeline Stages

- ✓ **tier1**
  -   ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==
- ✗ **tier2**
  - INCOMPLETE
  ✗ README.md Substrate pin gives the boot line a non-default port for shared hosts with the WHY (fixed 127.0.0.1:3000 collides on multi-user boxes and a stranger's daemon answers), a port-free check line, and AUGER_DUCKBRAIN_URL pinning guidance; docs_check still passes: Three of four elements are present and correct: non-default port (README.md:155 `node bin/duckbrain.js http --port 3987`), the WHY (README.md:147-151: fixed 127.0.0.1:3000 collides on multi-user boxes, requests 'answered by someone else's daemon — you see another agent's namespaces and wrong-looking 500s'), and the port-free check line (README.md:154 `ss -tlnp | grep :3987  # must print nothing before you boot on it`). docs_check passes: `bash tests/docs_check.sh` → exit 0, 'PASS: README claims match code — ... 5 env/path refs ok'. BUT the AUGER_DUCKBRAIN_URL pinning guidance is functionally broken: README.md:156 tells the user `export AUGER_DUCKBRAIN_URL=http://127.0.0.1:3987`, yet auger.py:64 reads `DB_URL = os.environ.get("DUCKBRAIN_URL", "http://127.0.0.1:3000")` — `grep -c AUGER_DUCKBRAIN_URL auger.py` returns 0, and no alias/wrapper maps the name anywhere in the repo (only DUCKBRAIN_URL is consumed, auger.py:64 and tests/conftest.py:96). Following the guidance is a no-op: auger still targets the default 127.0.0.1:3000, i.e. exactly the collision the section exists to prevent. README.md:146 even states 'auger reads DUCKBRAIN_URL', directly contradicting line 156. docs_check cannot catch this because arm C (tests/docs_check.sh:185-205) checks only a fixed 5-item list (AUGER_DOMAIN_GRID, DUCKBRAIN_API_KEY, foreman-status.token, ~/.duckbrain/token, ~/.hermes/.env) that excludes AUGER_DUCKBRAIN_URL.
The README adds the non-default port, the WHY, and the ss port-free check, and docs_check passes, but the AUGER_DUCKBRAIN_URL pinning guidance is a no-op — auger.py reads DUCKBRAIN_URL, so the documented export does not pin auger and the shared-host collision remains.

## Summary

Judge Result: AUG-060

Stage tier1: PASS
    ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==

Stage tier2: FAIL
  INCOMPLETE
  ✗ README.md Substrate pin gives the boot line a non-default port for shared hosts with the WHY (fixed 127.0.0.1:3000 collides on multi-user boxes and a stranger's daemon answers), a port-free check line, and AUGER_DUCKBRAIN_URL pinning guidance; docs_check still passes: Three of four elements are present and correct: non-default port (README.md:155 `node bin/duckbrain.js http --port 3987`), the WHY (README.md:147-151: fixed 127.0.0.1:3000 collides on multi-user boxes, requests 'answered by someone else's daemon — you see another agent's namespaces and wrong-looking 500s'), and the port-free check line (README.md:154 `ss -tlnp | grep :3987  # must print nothing before you boot on it`). docs_check passes: `bash tests/docs_check.sh` → exit 0, 'PASS: README claims match code — ... 5 env/path refs ok'. BUT the AUGER_DUCKBRAIN_URL pinning guidance is functionally broken: README.md:156 tells the user `export AUGER_DUCKBRAIN_URL=http://127.0.0.1:3987`, yet auger.py:64 reads `DB_URL = os.environ.get("DUCKBRAIN_URL", "http://127.0.0.1:3000")` — `grep -c AUGER_DUCKBRAIN_URL auger.py` returns 0, and no alias/wrapper maps the name anywhere in the repo (only DUCKBRAIN_URL is consumed, auger.py:64 and tests/conftest.py:96). Following the guidance is a no-op: auger still targets the default 127.0.0.1:3000, i.e. exactly the collision the section exists to prevent. README.md:146 even states 'auger reads DUCKBRAIN_URL', directly contradicting line 156. docs_check cannot catch this because arm C (tests/docs_check.sh:185-205) checks only a fixed 5-item list (AUGER_DOMAIN_GRID, DUCKBRAIN_API_KEY, foreman-status.token, ~/.duckbrain/token, ~/.hermes/.env) that excludes AUGER_DUCKBRAIN_URL.
The README adds the non-default port, the WHY, and the ss port-free check, and docs_check passes, but the AUGER_DUCKBRAIN_URL pinning guidance is a no-op — auger.py reads DUCKBRAIN_URL, so the documented export does not pin auger and the shared-host collision remains.

Overall: FAIL ✗
