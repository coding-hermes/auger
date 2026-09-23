# Verdict: AUG-042

**Task:** ask's proposed question is the JEV criterion SLUG, not a question sentence
**Evaluated:** 2026-09-23T14:52:46.957963
**Result:** ✓ PASS

## Pipeline Stages

- ✓ **tier1**
  -   ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==
- ✓ **tier2**
  - COMPLETE
  ✓ Stored question text is a real question sentence (from one shared slug->sentence source), never a bare criterion slug; an unknown slug is refused by name with no row written; the new_question criteria used by the JEV request and the sentence map cannot drift; tests cover slug->sentence, refusal, and the none case: Single source: auger.py:2874-2900 defines ASK_NEW_QUESTION_CRITERIA mapping each slug to {description, sentence} (e.g. how_is_it_tested -> 'What test proves this works?'); a repo-wide search shows these slugs are defined nowhere else. Stored text is the sentence, never the slug: auger.py:2941 `text = criterion["sentence"]` and the row is written with text=text (auger.py:2947); cmd_ask prints the same sentence from the same dict (auger.py:3078). Unknown slug refused by name with no row: auger.py:2925-2927 `criterion = ASK_NEW_QUESTION_CRITERIA.get(slug)` / `if criterion is None: return "", f"JEV proposed unknown question criterion {slug!r}"` — this returns before any insert, so no row is written. none case: auger.py:2928-2929 returns "JEV proposed none: nothing left worth asking" (sentence=None is never stored). No drift: the JEV request's new_question criteria are built from the same dict at auger.py:3006-3012 (`slug: criterion["description"] for slug, criterion in ASK_NEW_QUESTION_CRITERIA.items()`), so the wire criteria and the sentence map share one source. Tests cover all three: tests/test_auger.py:1597 test_ask_stores_the_question_it_proposes_and_the_answer_then_closes_it asserts q["text"] == Q_NEXT_TEXT, q["text"] != Q_NEXT and endswith('?'); tests/test_auger.py:1661 test_ask_sends_the_six_exact_new_question_criteria_to_jev asserts the exact six wire criteria; tests/test_auger.py:1685 test_ask_unknown_criterion_is_fail_closed_and_names_the_slug asserts the refusal names the slug and the row count is unchanged; tests/test_auger.py:1708 test_ask_none_criterion_is_not_stored asserts the none refusal writes no row. Command evidence: `python3 -m pytest tests/test_auger.py -k "ask"` -> '21 passed, 101 deselected in 351.99s'; targeted run of the four new tests -> '4 passed, 118 deselected in 88.05s'; full gate `bash tests/gate.sh` -> 'All checks passed!' (lint), '122 passed in 376.71s' (pytest), 'passed 21, failed 0' (smoke), 'GATE PASS'.
The fix stores a real question sentence from a single slug->sentence map shared with the JEV request, refuses unknown slugs by name without writing a row, and is covered by passing tests for slug->sentence, refusal, and the none case (full gate: GATE PASS).

## Summary

Judge Result: AUG-042

Stage tier1: PASS
    ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==

Stage tier2: PASS
  COMPLETE
  ✓ Stored question text is a real question sentence (from one shared slug->sentence source), never a bare criterion slug; an unknown slug is refused by name with no row written; the new_question criteria used by the JEV request and the sentence map cannot drift; tests cover slug->sentence, refusal, and the none case: Single source: auger.py:2874-2900 defines ASK_NEW_QUESTION_CRITERIA mapping each slug to {description, sentence} (e.g. how_is_it_tested -> 'What test proves this works?'); a repo-wide search shows these slugs are defined nowhere else. Stored text is the sentence, never the slug: auger.py:2941 `text = criterion["sentence"]` and the row is written with text=text (auger.py:2947); cmd_ask prints the same sentence from the same dict (auger.py:3078). Unknown slug refused by name with no row: auger.py:2925-2927 `criterion = ASK_NEW_QUESTION_CRITERIA.get(slug)` / `if criterion is None: return "", f"JEV proposed unknown question criterion {slug!r}"` — this returns before any insert, so no row is written. none case: auger.py:2928-2929 returns "JEV proposed none: nothing left worth asking" (sentence=None is never stored). No drift: the JEV request's new_question criteria are built from the same dict at auger.py:3006-3012 (`slug: criterion["description"] for slug, criterion in ASK_NEW_QUESTION_CRITERIA.items()`), so the wire criteria and the sentence map share one source. Tests cover all three: tests/test_auger.py:1597 test_ask_stores_the_question_it_proposes_and_the_answer_then_closes_it asserts q["text"] == Q_NEXT_TEXT, q["text"] != Q_NEXT and endswith('?'); tests/test_auger.py:1661 test_ask_sends_the_six_exact_new_question_criteria_to_jev asserts the exact six wire criteria; tests/test_auger.py:1685 test_ask_unknown_criterion_is_fail_closed_and_names_the_slug asserts the refusal names the slug and the row count is unchanged; tests/test_auger.py:1708 test_ask_none_criterion_is_not_stored asserts the none refusal writes no row. Command evidence: `python3 -m pytest tests/test_auger.py -k "ask"` -> '21 passed, 101 deselected in 351.99s'; targeted run of the four new tests -> '4 passed, 118 deselected in 88.05s'; full gate `bash tests/gate.sh` -> 'All checks passed!' (lint), '122 passed in 376.71s' (pytest), 'passed 21, failed 0' (smoke), 'GATE PASS'.
The fix stores a real question sentence from a single slug->sentence map shared with the JEV request, refuses unknown slugs by name without writing a row, and is covered by passing tests for slug->sentence, refusal, and the none case (full gate: GATE PASS).

Overall: PASS ✓
