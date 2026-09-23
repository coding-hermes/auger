# Verdict: AUG-033

**Task:** fresh-install quickstart: pnpm user-prefix bootstrap, data dir mkdir, AUGER_DOMAIN_GRID pointer
**Evaluated:** 2026-09-23T07:26:43.501821
**Result:** ✓ PASS

## Pipeline Stages

- ✓ **tier1**
  -   ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==
- ✓ **tier2**
  - COMPLETE
  ✓ README quickstart gains the bootstrap block (pnpm user prefix, mkdir data dir, AUGER_DOMAIN_GRID); each failure mode gets one line in docs/dogfood/diagnostics.md; every documented command matches the verified-working path from the 2026-09-23 leg: README.md:76-84 (Quick start) adds the bootstrap block with all three elements: pnpm user prefix (`npm config set prefix ~/.npm-global && export PATH=~/.npm-global/bin:$PATH && npm i -g pnpm`), data dir (`mkdir -p ~/dd-data && DUCKBRAIN_DATA_DIR=~/dd-data node bin/duckbrain.js http`), and `export AUGER_DOMAIN_GRID=<a canonical 44-domain grid>`; expanded with rationale at README.md:36-53. docs/dogfood/diagnostics.md:113-133 ('Fresh-install leg (2026-09-23...)') gives exactly one bullet per failure mode: pnpm EACCES (L117-121), DUCKBRAIN_DATA_DIR ENOENT (L121-124), AUGER_DOMAIN_GRID refusal (L125-133). Commands match the 2026-09-23 leg record verbatim: docs/dogfood/2026-09-23-graph-bundles-integration.md:124-133 (pnpm 12.4.2 ~5s; `mkdir -p ~/dd-data`; grid refusal). Code cross-check confirms the documented path is real: auger.py:2257 DOMAIN_GRID_ENV='AUGER_DOMAIN_GRID'; auger.py:2258-2267 DOMAIN_GRID_DIR default = ~/.hermes/skills/software-development/spec-decomposition-matrix-tradeoff/references/dogfood-artifact/02-domains, matching README.md:109 exactly; auger.py:2431-2433 error string 'domain grid not found: {d} — point AUGER_DOMAIN_GRID at the skill's references/dogfood-artifact/02-domains directory' matches README.md:109 verbatim; auger.py:3903 defines --seed-domains; auger.py:2268 DOMAIN_GRID_SIZE=44. TESTS (fresh, cache-defeating): `python3 -m py_compile auger.py` -> SYNTAX_OK; `ruff check auger.py` -> 'All checks passed!'; `AUGER_CI=1 python3 -m pytest tests/test_auger.py -q -ra` -> '104 passed in 1030.29s (0:17:10)' with 0 failed and 0 skipped; targeted `-k 'domain or grid or seed'` -> '10 passed, 94 deselected in 44.35s', confirming the documented AUGER_DOMAIN_GRID/--seed-domains path works as described.
README quickstart bootstrap block, per-failure-mode diagnostics lines, and 2026-09-23-leg command fidelity are all present and verified against auger.py source and a green 104-test suite.

## Summary

Judge Result: AUG-033

Stage tier1: PASS
    ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==

Stage tier2: PASS
  COMPLETE
  ✓ README quickstart gains the bootstrap block (pnpm user prefix, mkdir data dir, AUGER_DOMAIN_GRID); each failure mode gets one line in docs/dogfood/diagnostics.md; every documented command matches the verified-working path from the 2026-09-23 leg: README.md:76-84 (Quick start) adds the bootstrap block with all three elements: pnpm user prefix (`npm config set prefix ~/.npm-global && export PATH=~/.npm-global/bin:$PATH && npm i -g pnpm`), data dir (`mkdir -p ~/dd-data && DUCKBRAIN_DATA_DIR=~/dd-data node bin/duckbrain.js http`), and `export AUGER_DOMAIN_GRID=<a canonical 44-domain grid>`; expanded with rationale at README.md:36-53. docs/dogfood/diagnostics.md:113-133 ('Fresh-install leg (2026-09-23...)') gives exactly one bullet per failure mode: pnpm EACCES (L117-121), DUCKBRAIN_DATA_DIR ENOENT (L121-124), AUGER_DOMAIN_GRID refusal (L125-133). Commands match the 2026-09-23 leg record verbatim: docs/dogfood/2026-09-23-graph-bundles-integration.md:124-133 (pnpm 12.4.2 ~5s; `mkdir -p ~/dd-data`; grid refusal). Code cross-check confirms the documented path is real: auger.py:2257 DOMAIN_GRID_ENV='AUGER_DOMAIN_GRID'; auger.py:2258-2267 DOMAIN_GRID_DIR default = ~/.hermes/skills/software-development/spec-decomposition-matrix-tradeoff/references/dogfood-artifact/02-domains, matching README.md:109 exactly; auger.py:2431-2433 error string 'domain grid not found: {d} — point AUGER_DOMAIN_GRID at the skill's references/dogfood-artifact/02-domains directory' matches README.md:109 verbatim; auger.py:3903 defines --seed-domains; auger.py:2268 DOMAIN_GRID_SIZE=44. TESTS (fresh, cache-defeating): `python3 -m py_compile auger.py` -> SYNTAX_OK; `ruff check auger.py` -> 'All checks passed!'; `AUGER_CI=1 python3 -m pytest tests/test_auger.py -q -ra` -> '104 passed in 1030.29s (0:17:10)' with 0 failed and 0 skipped; targeted `-k 'domain or grid or seed'` -> '10 passed, 94 deselected in 44.35s', confirming the documented AUGER_DOMAIN_GRID/--seed-domains path works as described.
README quickstart bootstrap block, per-failure-mode diagnostics lines, and 2026-09-23-leg command fidelity are all present and verified against auger.py source and a green 104-test suite.

Overall: PASS ✓
