# Verdict: AUG-155

**Task:** [docs] SPEC-003-xml-specs.md documents 2 of the 7 `auger spec` verbs: validate/check/bundle/render/align are absent from the design doc
**Evaluated:** 2026-11-20T17:46:38.380849
**Result:** ✓ PASS

## Criteria

- ✓ **PASS: `grep -oE 'auger spec [a-z-]+' docs/SPEC-003-xml-specs.md | sort -u` prints all seven verbs docs/VERBS.md names (align, assemble, bundle, check, list, render, validate).**
  - Ran the exact command: output is exactly `auger spec align / assemble / bundle / check / list / render / validate` — all seven verbs the criterion names are present (VERBS.md additionally names `context`, but the criterion's enumerated list is the seven and all appear).
- ✓ **PASS: for each verb, the doc names one command line and its exit-code contract, and `for v in align assemble bundle check list render validate; do python3 auger.py spec $v --help; done` exits 0 for all seven (the documented verb exists).**
  - docs/SPEC-003-xml-specs.md sections Validate (L135-138), Check (L141-144), Bundle (L147-150), Align (L153-157), List (L160-163), Assemble (L165-171), Render (L174-178) each give one command line plus exit-code contract (e.g. L138 'Exit 0 means validation succeeded; exit 2 means a typed refusal'). Ran the help loop: every verb printed exit 0 (align: 0, assemble: 0, bundle: 0, check: 0, list: 0, render: 0, validate: 0).
- ✓ **PASS: the render section states it writes BOTH SPEC-CORPUS-VIEW.md and .html and refuses --out inside the corpus root (render-into-corpus, rc 2); the assemble section documents --measure, report_schema_version 1.1 and that the measured report is opt-in.**
  - Render section (docs/SPEC-003-xml-specs.md:174-178): 'writes both `SPEC-CORPUS-VIEW.md` and `SPEC-CORPUS-VIEW.html`... exit 2 means a typed refusal, including `render-into-corpus` when `--out` is inside the corpus root'. Assemble section (L165-171): '`--measure` is opt-in and JSON-only: it adds a measured report with `report_schema_version` `1.1`... Without it the measured report is absent'.
- ✓ **PASS: `bash tests/docs_check.sh`**
  - Ran `bash tests/docs_check.sh`: exit 0, output 'PASS: README claims match code — fences ok (15/19 bash-labeled parse under bash -n; 6 live-service parsed only — never executed), loop verbs ok (17 listed, 18 shipped), VERBS.md registry ok, tally ok (14 tables), 5 env/path refs ok'.
- ✓ **Derived: the documented exit-code/refusal contracts match the implementation (render-into-corpus refusal exists; report_schema_version is 1.1; --measure is opt-in)**
  - auger.py:9573 emits the 'render-into-corpus' typed refusal for --out inside the corpus root (docstring at 9555); auger.py:9127 SPEC_ASSEMBLY_REPORT_VERSION = "1.1" consumed at auger.py:13107; auger.py:14431 --measure help text describes the opt-in measured report. Doc matches code.

## Pipeline Stages

- ✓ **tier1**
  -   ✓ lint: scanners: nice=nice -n 10
  ✓ secrets: secrets: scanners=gitleaks+builtin cross-check
  ✓ tests: scanners: nice=nice -n 10
- ✓ **tier2**
  - COMPLETE
  ✓ PASS: `grep -oE 'auger spec [a-z-]+' docs/SPEC-003-xml-specs.md | sort -u` prints all seven verbs docs/VERBS.md names (align, assemble, bundle, check, list, render, validate).: Ran the exact command: output is exactly `auger spec align / assemble / bundle / check / list / render / validate` — all seven verbs the criterion names are present (VERBS.md additionally names `context`, but the criterion's enumerated list is the seven and all appear).
  ✓ PASS: for each verb, the doc names one command line and its exit-code contract, and `for v in align assemble bundle check list render validate; do python3 auger.py spec $v --help; done` exits 0 for all seven (the documented verb exists).: docs/SPEC-003-xml-specs.md sections Validate (L135-138), Check (L141-144), Bundle (L147-150), Align (L153-157), List (L160-163), Assemble (L165-171), Render (L174-178) each give one command line plus exit-code contract (e.g. L138 'Exit 0 means validation succeeded; exit 2 means a typed refusal'). Ran the help loop: every verb printed exit 0 (align: 0, assemble: 0, bundle: 0, check: 0, list: 0, render: 0, validate: 0).
  ✓ PASS: the render section states it writes BOTH SPEC-CORPUS-VIEW.md and .html and refuses --out inside the corpus root (render-into-corpus, rc 2); the assemble section documents --measure, report_schema_version 1.1 and that the measured report is opt-in.: Render section (docs/SPEC-003-xml-specs.md:174-178): 'writes both `SPEC-CORPUS-VIEW.md` and `SPEC-CORPUS-VIEW.html`... exit 2 means a typed refusal, including `render-into-corpus` when `--out` is inside the corpus root'. Assemble section (L165-171): '`--measure` is opt-in and JSON-only: it adds a measured report with `report_schema_version` `1.1`... Without it the measured report is absent'.
  ✓ PASS: `bash tests/docs_check.sh`: Ran `bash tests/docs_check.sh`: exit 0, output 'PASS: README claims match code — fences ok (15/19 bash-labeled parse under bash -n; 6 live-service parsed only — never executed), loop verbs ok (17 listed, 18 shipped), VERBS.md registry ok, tally ok (14 tables), 5 env/path refs ok'.
  ✓ Derived: the documented exit-code/refusal contracts match the implementation (render-into-corpus refusal exists; report_schema_version is 1.1; --measure is opt-in): auger.py:9573 emits the 'render-into-corpus' typed refusal for --out inside the corpus root (docstring at 9555); auger.py:9127 SPEC_ASSEMBLY_REPORT_VERSION = "1.1" consumed at auger.py:13107; auger.py:14431 --measure help text describes the opt-in measured report. Doc matches code.
SPEC-003-xml-specs.md now documents all seven shipped `auger spec` verbs with command lines and exit-code contracts (committed as c4cf366), the render/assemble specifics match the implementation in auger.py, all seven --help invocations exit 0, and tests/docs_check.sh passes with exit 0.

## Summary

Stage tier1: PASS
    ✓ lint: scanners: nice=nice -n 10
  ✓ secrets: secrets: scanners=gitleaks+builtin cross-check
  ✓ tests: scanners: nice=nice -n 10

Stage tier2: PASS
  COMPLETE
  ✓ PASS: `grep -oE 'auger spec [a-z-]+' docs/SPEC-003-xml-specs.md | sort -u` prints all seven verbs docs/VERBS.md names (align, assemble, bundle, check, list, render, validate).: Ran the exact command: output is exactly `auger spec align / assemble / bundle / check / list / render / validate` — all seven verbs the criterion names are present (VERBS.md additionally names `context`, but the criterion's enumerated list is the seven and all appear).
  ✓ PASS: for each verb, the doc names one command line and its exit-code contract, and `for v in align assemble bundle check list render validate; do python3 auger.py spec $v --help; done` exits 0 for all seven (the documented verb exists).: docs/SPEC-003-xml-specs.md sections Validate (L135-138), Check (L141-144), Bundle (L147-150), Align (L153-157), List (L160-163), Assemble (L165-171), Render (L174-178) each give one command line plus exit-code contract (e.g. L138 'Exit 0 means validation succeeded; exit 2 means a typed refusal'). Ran the help loop: every verb printed exit 0 (align: 0, assemble: 0, bundle: 0, check: 0, list: 0, render: 0, validate: 0).
  ✓ PASS: the render section states it writes BOTH SPEC-CORPUS-VIEW.md and .html and refuses --out inside the corpus root (render-into-corpus, rc 2); the assemble section documents --measure, report_schema_version 1.1 and that the measured report is opt-in.: Render section (docs/SPEC-003-xml-specs.md:174-178): 'writes both `SPEC-CORPUS-VIEW.md` and `SPEC-CORPUS-VIEW.html`... exit 2 means a typed refusal, including `render-into-corpus` when `--out` is inside the corpus root'. Assemble section (L165-171): '`--measure` is opt-in and JSON-only: it adds a measured report with `report_schema_version` `1.1`... Without it the measured report is absent'.
  ✓ PASS: `bash tests/docs_check.sh`: Ran `bash tests/docs_check.sh`: exit 0, output 'PASS: README claims match code — fences ok (15/19 bash-labeled parse under bash -n; 6 live-service parsed only — never executed), loop verbs ok (17 listed, 18 shipped), VERBS.md registry ok, tally ok (14 tables), 5 env/path refs ok'.
  ✓ Derived: the documented exit-code/refusal contracts match the implementation (render-into-corpus refusal exists; report_schema_version is 1.1; --measure is opt-in): auger.py:9573 emits the 'render-into-corpus' typed refusal for --out inside the corpus root (docstring at 9555); auger.py:9127 SPEC_ASSEMBLY_REPORT_VERSION = "1.1" consumed at auger.py:13107; auger.py:14431 --measure help text describes the opt-in measured report. Doc matches code.
SPEC-003-xml-specs.md now documents all seven shipped `auger spec` verbs with command lines and exit-code contracts (committed as c4cf366), the render/assemble specifics match the implementation in auger.py, all seven --help invocations exit 0, and tests/docs_check.sh passes with exit 0.

Overall: PASS ✓
