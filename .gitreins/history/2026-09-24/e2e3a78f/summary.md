# Verdict: AUG-056

**Task:** Prevent ask from re-proposing the just-closed question
**Evaluated:** 2026-09-24T10:12:02.081057
**Result:** ✓ PASS

## Pipeline Stages

- ✓ **tier1**
  -   ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==
- ✓ **tier2**
  - COMPLETE
  ✓ After ask -> answer --question-id -> ask on a fresh namespace, the second ask must not re-propose the exact question text just closed; it must still allow a genuinely different next question; no duplicate decision is needed; ruff check auger.py passes.: ruff check auger.py -> 'All checks passed!' (exit 0); py_compile OK. auger.py:3083-3117 store_proposed_question now selects ALL project questions (project_id=eq.X&order=id.asc) and refuses an exact canonical text match when the prior row's status is in SETTLED_STATES=("answered","linked") (auger.py:1129) AND a facet row has status=closed with non-empty closed_by, returning 'already closed/answered as Q-000001 by D-001'. answer --question-id -> close_question (auger.py:1263) -> set_state(...,"answered",...,closed_by=decision_id) (auger.py:1211) writes facets closed+closed_by, so the closure is durable. Tests on a fresh ephemeral namespace (project fixture, tests/conftest.py:233): test_ask_does_not_repropose_a_question_after_answer_closes_it asserts no 'stored question:' on the 2nd ask, 'already closed/answered'+'Q-000001'+'answered by D-001', 1 question row, 1 decision row; test_ask_stores_a_different_question_after_answer_closes_the_first asserts Q-000002 stored with the different criterion text, 2 question rows, 1 decision row. Command evidence: `python -m pytest tests/test_auger.py -k 'repropose_a_question_after_answer_closes_it or stores_a_different_question_after_answer_closes_the_first'` -> '2 passed, 139 deselected in 52.26s'; ask-focused suite `-k ask` -> '23 passed, 118 deselected in 150.86s'.
The fix stores proposed-question identity against durable question rows and refuses only settled questions carrying a stored decision closure, verified by passing regression tests (2 passed) and the ask suite (23 passed) plus a clean ruff check.

## Summary

Judge Result: AUG-056

Stage tier1: PASS
    ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==

Stage tier2: PASS
  COMPLETE
  ✓ After ask -> answer --question-id -> ask on a fresh namespace, the second ask must not re-propose the exact question text just closed; it must still allow a genuinely different next question; no duplicate decision is needed; ruff check auger.py passes.: ruff check auger.py -> 'All checks passed!' (exit 0); py_compile OK. auger.py:3083-3117 store_proposed_question now selects ALL project questions (project_id=eq.X&order=id.asc) and refuses an exact canonical text match when the prior row's status is in SETTLED_STATES=("answered","linked") (auger.py:1129) AND a facet row has status=closed with non-empty closed_by, returning 'already closed/answered as Q-000001 by D-001'. answer --question-id -> close_question (auger.py:1263) -> set_state(...,"answered",...,closed_by=decision_id) (auger.py:1211) writes facets closed+closed_by, so the closure is durable. Tests on a fresh ephemeral namespace (project fixture, tests/conftest.py:233): test_ask_does_not_repropose_a_question_after_answer_closes_it asserts no 'stored question:' on the 2nd ask, 'already closed/answered'+'Q-000001'+'answered by D-001', 1 question row, 1 decision row; test_ask_stores_a_different_question_after_answer_closes_the_first asserts Q-000002 stored with the different criterion text, 2 question rows, 1 decision row. Command evidence: `python -m pytest tests/test_auger.py -k 'repropose_a_question_after_answer_closes_it or stores_a_different_question_after_answer_closes_the_first'` -> '2 passed, 139 deselected in 52.26s'; ask-focused suite `-k ask` -> '23 passed, 118 deselected in 150.86s'.
The fix stores proposed-question identity against durable question rows and refuses only settled questions carrying a stored decision closure, verified by passing regression tests (2 passed) and the ask suite (23 passed) plus a clean ruff check.

Overall: PASS ✓
