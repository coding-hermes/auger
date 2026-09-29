# Verdict: DOC-001

**Task:** Document answer invalidation flags in VERBS.md
**Evaluated:** 2026-09-26T20:47:37.871317
**Result:** ✓ PASS

## Pipeline Stages

- ✓ **tier1**
  -   ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==
- ✓ **tier2**
  - COMPLETE
  ✓ docs/VERBS.md lists --invalidates and --invalidates-why/--why semantics and includes one valid worked example: docs/VERBS.md:137-138 lists `--invalidates` (repeatable decision id) and `--invalidates-why` (alias `--why`; only with `--invalidates`) in the answer Arguments line. Lines 162-172 document full semantics: one LOCAL `breaks` edge per target, source `human`, default asserted confidence, note `<did> invalidates <target>`, repeatable, target must already be stored in this project's namespace, refusals for not-stored/sibling/self/why-without-target, nothing rewritten, propagate performs cascade. Lines 187-189 confirm `--invalidates-why` valid only with `--invalidates` and reason recorded on the break edge's note. Worked example at lines 178-181: `auger answer --id D-003 --chosen O2 --invalidates D-001 --invalidates-why 'new evidence rules out the earlier decision'`, with prose describing the recorded edge `D-003 invalidates D-001: new evidence rules out the earlier decision` and the trigger line naming propagate. Example verified valid via live run (rc=0, matching output) and focused tests `python -m pytest tests/test_auger.py -k invalidates -q` => 3 passed, 211 deselected.
docs/VERBS.md fully documents --invalidates and --invalidates-why/--why semantics with a valid, live-verified worked example.

## Summary

Judge Result: DOC-001

Stage tier1: PASS
    ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==

Stage tier2: PASS
  COMPLETE
  ✓ docs/VERBS.md lists --invalidates and --invalidates-why/--why semantics and includes one valid worked example: docs/VERBS.md:137-138 lists `--invalidates` (repeatable decision id) and `--invalidates-why` (alias `--why`; only with `--invalidates`) in the answer Arguments line. Lines 162-172 document full semantics: one LOCAL `breaks` edge per target, source `human`, default asserted confidence, note `<did> invalidates <target>`, repeatable, target must already be stored in this project's namespace, refusals for not-stored/sibling/self/why-without-target, nothing rewritten, propagate performs cascade. Lines 187-189 confirm `--invalidates-why` valid only with `--invalidates` and reason recorded on the break edge's note. Worked example at lines 178-181: `auger answer --id D-003 --chosen O2 --invalidates D-001 --invalidates-why 'new evidence rules out the earlier decision'`, with prose describing the recorded edge `D-003 invalidates D-001: new evidence rules out the earlier decision` and the trigger line naming propagate. Example verified valid via live run (rc=0, matching output) and focused tests `python -m pytest tests/test_auger.py -k invalidates -q` => 3 passed, 211 deselected.
docs/VERBS.md fully documents --invalidates and --invalidates-why/--why semantics with a valid, live-verified worked example.

Overall: PASS ✓
