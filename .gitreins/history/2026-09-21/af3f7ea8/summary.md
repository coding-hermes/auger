# Verdict: AUG-008

**Task:** propagation: closure and moot cascades (SPEC-001 BEAT 4)
**Evaluated:** 2026-09-21T03:22:58.960113
**Result:** ✓ PASS

## Pipeline Stages

- ✓ **tier1**
  -   ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==
- ✓ **tier2**
  - COMPLETE
  ✓ A question whose parent decision is invalidated by a breaks edge becomes moot with a reason naming the edge; the cascade reaches grandchildren; a question with an unresolved blocks edge is never surfaced by ask; a question that becomes answerable after a parent closes is LINKED by the gate rather than asked: All four sub-claims verified in auger.py propagate()/askable_questions()/cmd_ask(). (a) moot+reason naming edge: auger.py:544 `root = f"{e['dst_id']} was invalidated by {e['id']}"` then set_state(...,'moot',root); test_propagate_moots_the_question_a_breaks_edge_invalidates asserts auger.q_reason(ns,'Q-000001')=='D-001 was invalidated by E-000001' and status=='moot' with row intact. (b) cascade reaches grandchildren: BFS `while queue` loop over idx['children'] (auger.py:551-561); test_propagate_reopens_the_stale_branch_and_reaches_the_grandchild asserts Q-000003 (grandchild) status=='open' and its reason names Q-000002. (c) unresolved blocks never surfaced by ask: askable_questions (auger.py:457) filters unresolved_blockers; cmd_ask (auger.py:741-743) prints only askable ids/texts and merely counts blocked; test_ask_never_surfaces_a_question_with_an_unresolved_blocker asserts 'Q-000002' not in out and Q_STORE not in out, plus control that settling the blocker makes it askable. (d) answerable-after-parent-closes LINKED by gate: gate walk (auger.py:566-601) writes a 'satisfies' edge and set_state(...,'linked',...); test_propagate_links_a_question_the_gate_can_answer_after_its_parent_closes asserts status=='linked', satisfies edge (decision D-001 -> question Q-000002, source 'gate'), and 'Q-000002' not in ask output. Test evidence: `python3 -m pytest tests/test_auger.py -q -k "propagate or ask_never"` -> '4 passed, 27 deselected in 24.69s' EXIT=0. Full gate `bash tests/gate.sh` -> '31 passed in 58.77s', smoke 'passed 15, failed 0', final line 'GATE PASS'.
All four propagation sub-claims (moot-with-edge-reason, grandchild cascade, blocks-edge suppression in ask, gate LINKING) are implemented in auger.py and proven by 4 passing tests plus the full green gate.

## Summary

Judge Result: AUG-008

Stage tier1: PASS
    ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==

Stage tier2: PASS
  COMPLETE
  ✓ A question whose parent decision is invalidated by a breaks edge becomes moot with a reason naming the edge; the cascade reaches grandchildren; a question with an unresolved blocks edge is never surfaced by ask; a question that becomes answerable after a parent closes is LINKED by the gate rather than asked: All four sub-claims verified in auger.py propagate()/askable_questions()/cmd_ask(). (a) moot+reason naming edge: auger.py:544 `root = f"{e['dst_id']} was invalidated by {e['id']}"` then set_state(...,'moot',root); test_propagate_moots_the_question_a_breaks_edge_invalidates asserts auger.q_reason(ns,'Q-000001')=='D-001 was invalidated by E-000001' and status=='moot' with row intact. (b) cascade reaches grandchildren: BFS `while queue` loop over idx['children'] (auger.py:551-561); test_propagate_reopens_the_stale_branch_and_reaches_the_grandchild asserts Q-000003 (grandchild) status=='open' and its reason names Q-000002. (c) unresolved blocks never surfaced by ask: askable_questions (auger.py:457) filters unresolved_blockers; cmd_ask (auger.py:741-743) prints only askable ids/texts and merely counts blocked; test_ask_never_surfaces_a_question_with_an_unresolved_blocker asserts 'Q-000002' not in out and Q_STORE not in out, plus control that settling the blocker makes it askable. (d) answerable-after-parent-closes LINKED by gate: gate walk (auger.py:566-601) writes a 'satisfies' edge and set_state(...,'linked',...); test_propagate_links_a_question_the_gate_can_answer_after_its_parent_closes asserts status=='linked', satisfies edge (decision D-001 -> question Q-000002, source 'gate'), and 'Q-000002' not in ask output. Test evidence: `python3 -m pytest tests/test_auger.py -q -k "propagate or ask_never"` -> '4 passed, 27 deselected in 24.69s' EXIT=0. Full gate `bash tests/gate.sh` -> '31 passed in 58.77s', smoke 'passed 15, failed 0', final line 'GATE PASS'.
All four propagation sub-claims (moot-with-edge-reason, grandchild cascade, blocks-edge suppression in ask, gate LINKING) are implemented in auger.py and proven by 4 passing tests plus the full green gate.

Overall: PASS ✓
