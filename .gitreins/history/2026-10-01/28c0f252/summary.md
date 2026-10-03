# Verdict: AUG-086

**Task:** check threshold nondeterminism
**Evaluated:** 2026-10-01T05:28:20.751306
**Result:** ✓ PASS

## Pipeline Stages

- ✓ **tier1**
  -   ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==
- ✓ **tier2**
  - COMPLETE
  ✓ Implement sticky hysteresis around the already-answered threshold so identical evidence produces deterministic results; add regression test proving the fix: auger.py:77-79 adds T_ANSWERED_BAND=0.03; in_answered_band (auger.py:5121-5130) and check_verdict (auger.py:5133-5152) make the verdict deterministic: outside the band the score decides as before, inside the band the STORE decides via settled_question_with_text (auger.py:5100-5119), which reads stored question rows deterministically (select_or_empty order=id.asc) matching SETTLED_STATES=('answered','linked') (auger.py:1921) with casefold/trim text — identical rows give identical verdicts. cmd_check (auger.py:5215-5225) wires it and prints check_band_note naming the deciding row. Regression tests at tests/test_auger.py:2087-2225: test_check_verdict_band_rule_is_exact, test_settled_question_with_text_reads_only_settled_rows, test_check_is_deterministic_inside_the_threshold_band (run-10 repro: 0.55 then 0.53 both -> ALREADY ANSWERED naming Q-000001), test_check_in_band_defers_to_the_record_when_nothing_is_settled. Evidence: focused run `python -m pytest tests/test_auger.py -k "band or deterministic or settled_question_with_text" -q` -> '5 passed, 276 deselected in 67.28s' (exit 0); full repo gate `bash tests/gate.sh` -> syntax OK, lint 'All checks passed!', pytest '280 passed, 1 skipped in 1127.52s', smoke 'passed 21, failed 0', docs check PASS, final line 'GATE PASS'; LSP diagnostics 0 findings.
Sticky band hysteresis around T_ANSWERED makes check's verdict deterministic (store decides inside the band), with regression tests proving the run-10 flip is fixed and the full gate green (280 passed, GATE PASS).

## Summary

Judge Result: AUG-086

Stage tier1: PASS
    ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==

Stage tier2: PASS
  COMPLETE
  ✓ Implement sticky hysteresis around the already-answered threshold so identical evidence produces deterministic results; add regression test proving the fix: auger.py:77-79 adds T_ANSWERED_BAND=0.03; in_answered_band (auger.py:5121-5130) and check_verdict (auger.py:5133-5152) make the verdict deterministic: outside the band the score decides as before, inside the band the STORE decides via settled_question_with_text (auger.py:5100-5119), which reads stored question rows deterministically (select_or_empty order=id.asc) matching SETTLED_STATES=('answered','linked') (auger.py:1921) with casefold/trim text — identical rows give identical verdicts. cmd_check (auger.py:5215-5225) wires it and prints check_band_note naming the deciding row. Regression tests at tests/test_auger.py:2087-2225: test_check_verdict_band_rule_is_exact, test_settled_question_with_text_reads_only_settled_rows, test_check_is_deterministic_inside_the_threshold_band (run-10 repro: 0.55 then 0.53 both -> ALREADY ANSWERED naming Q-000001), test_check_in_band_defers_to_the_record_when_nothing_is_settled. Evidence: focused run `python -m pytest tests/test_auger.py -k "band or deterministic or settled_question_with_text" -q` -> '5 passed, 276 deselected in 67.28s' (exit 0); full repo gate `bash tests/gate.sh` -> syntax OK, lint 'All checks passed!', pytest '280 passed, 1 skipped in 1127.52s', smoke 'passed 21, failed 0', docs check PASS, final line 'GATE PASS'; LSP diagnostics 0 findings.
Sticky band hysteresis around T_ANSWERED makes check's verdict deterministic (store decides inside the band), with regression tests proving the run-10 flip is fixed and the full gate green (280 passed, GATE PASS).

Overall: PASS ✓
