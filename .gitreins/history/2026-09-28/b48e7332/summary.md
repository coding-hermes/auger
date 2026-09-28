# Verdict: AUG-084

**Task:** Make implicit project targeting explicit and reject duplicate names
**Evaluated:** 2026-09-28T18:25:09.453555
**Result:** ✓ PASS

## Pipeline Stages

- ✓ **tier1**
  -   ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==
- ✓ **tier2**
  - COMPLETE
  ✓ No-flag verbs must not silently retarget when a second project starts; duplicate project names must be refused; add regression tests and docs, with full gate green.: (1) No silent retarget: auger.py:3421-3438 `_project(ns, pid=None)` now selects `order=created_at.desc` with no limit and, when len(rows)>1, raises SystemExit 'refused: namespace {ns!r} has multiple projects; implicit selection would choose newest project {id} ({name!r}). Pass -p/--project-id to choose explicitly; nothing was written'. Every project-targeted no-flag verb routes through it: cmd_propagate(2086), feedback(2595), cmd_ask(3647), cmd_answer(4178), cmd_status(4543), cmd_dump(5073), verdict(5355/5378), record_option(5491/5513/5542); export is namespace-scoped by contract (render_export docstring 5021-5027) and recall uses project_id only as an optional key-prefix filter. (2) Duplicate name refused: auger.py:3454-3465 cmd_start computes `name = a.name or ns`, queries `select(ns,'project',f'name=eq.{name}&limit=1')`, and raises SystemExit 'refused: project name {name!r} already exists as {existing[0]["id"]}; nothing was written' BEFORE insert()/remember(), so no row and no memory is written. (3) Regression tests: tests/test_auger.py:279 test_start_refuses_duplicate_project_name_before_writing and tests/test_auger.py:295 test_no_flag_project_read_refuses_after_second_project; focused run `python3 -m pytest tests/test_auger.py -q -k 'duplicate_project_name or no_flag_project_read'` -> '2 passed, 237 deselected in 11.58s'. (4) Docs: docs/VERBS.md conventions section documents the implicit-refusal rule and the `start` section documents 'A duplicate `--name` is refused before the row or memory write, and the refusal names the existing project id.' (5) Full gate green: fresh `bash tests/gate.sh` (repo's configured test_command) -> '== syntax ==' OK, '== lint == All checks passed!', '== pytest == 238 passed, 1 skipped in 835.91s (0:13:55)', '== end-to-end smoke == ... passed 21, failed 0', 'LIVE ARMS: ran 2; skipped 0', 'GATE PASS'.
Implicit project targeting now refuses when a namespace holds multiple projects, duplicate project names are refused before any write, regression tests and docs were added, and the full gate (syntax + ruff + 238 pytest + 21-assertion smoke) is green.

## Summary

Judge Result: AUG-084

Stage tier1: PASS
    ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==

Stage tier2: PASS
  COMPLETE
  ✓ No-flag verbs must not silently retarget when a second project starts; duplicate project names must be refused; add regression tests and docs, with full gate green.: (1) No silent retarget: auger.py:3421-3438 `_project(ns, pid=None)` now selects `order=created_at.desc` with no limit and, when len(rows)>1, raises SystemExit 'refused: namespace {ns!r} has multiple projects; implicit selection would choose newest project {id} ({name!r}). Pass -p/--project-id to choose explicitly; nothing was written'. Every project-targeted no-flag verb routes through it: cmd_propagate(2086), feedback(2595), cmd_ask(3647), cmd_answer(4178), cmd_status(4543), cmd_dump(5073), verdict(5355/5378), record_option(5491/5513/5542); export is namespace-scoped by contract (render_export docstring 5021-5027) and recall uses project_id only as an optional key-prefix filter. (2) Duplicate name refused: auger.py:3454-3465 cmd_start computes `name = a.name or ns`, queries `select(ns,'project',f'name=eq.{name}&limit=1')`, and raises SystemExit 'refused: project name {name!r} already exists as {existing[0]["id"]}; nothing was written' BEFORE insert()/remember(), so no row and no memory is written. (3) Regression tests: tests/test_auger.py:279 test_start_refuses_duplicate_project_name_before_writing and tests/test_auger.py:295 test_no_flag_project_read_refuses_after_second_project; focused run `python3 -m pytest tests/test_auger.py -q -k 'duplicate_project_name or no_flag_project_read'` -> '2 passed, 237 deselected in 11.58s'. (4) Docs: docs/VERBS.md conventions section documents the implicit-refusal rule and the `start` section documents 'A duplicate `--name` is refused before the row or memory write, and the refusal names the existing project id.' (5) Full gate green: fresh `bash tests/gate.sh` (repo's configured test_command) -> '== syntax ==' OK, '== lint == All checks passed!', '== pytest == 238 passed, 1 skipped in 835.91s (0:13:55)', '== end-to-end smoke == ... passed 21, failed 0', 'LIVE ARMS: ran 2; skipped 0', 'GATE PASS'.
Implicit project targeting now refuses when a namespace holds multiple projects, duplicate project names are refused before any write, regression tests and docs were added, and the full gate (syntax + ruff + 238 pytest + 21-assertion smoke) is green.

Overall: PASS ✓
