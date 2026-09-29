# Verdict: AUG-057

**Task:** Preserve every repeated --why-not reason against its rejected option
**Evaluated:** 2026-09-29T00:09:59.379653
**Result:** ✓ PASS

## Pipeline Stages

- ✓ **tier1**
  -   ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==
- ✓ **tier2**
  - COMPLETE
  ✓ A decision rejecting two alternatives preserves and renders two reasons against the correct options; repeated --why-not is honored or refused loudly; docs and regression tests cover the arity.: auger.py:4170 encode_why_not() keeps a single --why-not as the legacy scalar but stores repeated values as WHY_NOT_PREFIX ('per-option-v1:') + JSON list of {option_id,label,reason} zipped against the rejected options (those whose id != chosen id); an arity mismatch raises SystemExit 'refused: --why-not was repeated N times, but this answer has M rejected options ... nothing was written' at auger.py:4184, and this call (auger.py:4364) precedes insert_decision (auger.py:4407) so nothing is written. why_not_records/why_not_display/why_not_dump_lines (auger.py:4198/4224/4235) decode and render each pair as 'D-###-O# (label) — reason'; dump uses why_not_dump_lines at auger.py:5257. --why-not is action='append' (auger.py:5728). docs/VERBS.md:150-160 documents the arity (one shared reason, or repeat exactly once per rejected option in option order; mismatch refused by name before any rows written). Regression tests tests/test_auger.py test_answer_repeated_why_not_records_each_rejected_option_and_dump_renders_it (asserts D-057-O1/O3 pairs rendered and chosen O2 NOT rendered as rejected) and test_answer_repeated_why_not_with_wrong_arity_is_refused (rc!=0, '--why-not' in message, no D-058 row). TEST RUN: `python -m pytest tests/test_auger.py -k "why_not" -x -q` -> exit_code 0, '2 passed, 239 deselected in 8.42s'. Independent direct verification: encode_why_not(['a','b','c'], 2 options, chosen O1) -> 'REFUSED: refused: --why-not was repeated 3 times, but this answer has 1 rejected options ... nothing was written'; encode_why_not(['r1','r2'], 3 options, chosen O2) -> per-option-v1:[{"option_id":"D-1-O1","label":"x","reason":"r1"},{"option_id":"D-1-O3","label":"z","reason":"r2"}] and display 'D-1-O1 (x) — r1; D-1-O3 (z) — r2'; single value -> 'only' (legacy scalar).
Repeated --why-not is preserved per rejected option with correct pairing, arity mismatch is refused loudly before any write, and docs plus passing regression tests cover the arity.

## Summary

Judge Result: AUG-057

Stage tier1: PASS
    ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==

Stage tier2: PASS
  COMPLETE
  ✓ A decision rejecting two alternatives preserves and renders two reasons against the correct options; repeated --why-not is honored or refused loudly; docs and regression tests cover the arity.: auger.py:4170 encode_why_not() keeps a single --why-not as the legacy scalar but stores repeated values as WHY_NOT_PREFIX ('per-option-v1:') + JSON list of {option_id,label,reason} zipped against the rejected options (those whose id != chosen id); an arity mismatch raises SystemExit 'refused: --why-not was repeated N times, but this answer has M rejected options ... nothing was written' at auger.py:4184, and this call (auger.py:4364) precedes insert_decision (auger.py:4407) so nothing is written. why_not_records/why_not_display/why_not_dump_lines (auger.py:4198/4224/4235) decode and render each pair as 'D-###-O# (label) — reason'; dump uses why_not_dump_lines at auger.py:5257. --why-not is action='append' (auger.py:5728). docs/VERBS.md:150-160 documents the arity (one shared reason, or repeat exactly once per rejected option in option order; mismatch refused by name before any rows written). Regression tests tests/test_auger.py test_answer_repeated_why_not_records_each_rejected_option_and_dump_renders_it (asserts D-057-O1/O3 pairs rendered and chosen O2 NOT rendered as rejected) and test_answer_repeated_why_not_with_wrong_arity_is_refused (rc!=0, '--why-not' in message, no D-058 row). TEST RUN: `python -m pytest tests/test_auger.py -k "why_not" -x -q` -> exit_code 0, '2 passed, 239 deselected in 8.42s'. Independent direct verification: encode_why_not(['a','b','c'], 2 options, chosen O1) -> 'REFUSED: refused: --why-not was repeated 3 times, but this answer has 1 rejected options ... nothing was written'; encode_why_not(['r1','r2'], 3 options, chosen O2) -> per-option-v1:[{"option_id":"D-1-O1","label":"x","reason":"r1"},{"option_id":"D-1-O3","label":"z","reason":"r2"}] and display 'D-1-O1 (x) — r1; D-1-O3 (z) — r2'; single value -> 'only' (legacy scalar).
Repeated --why-not is preserved per rejected option with correct pairing, arity mismatch is refused loudly before any write, and docs plus passing regression tests cover the arity.

Overall: PASS ✓
