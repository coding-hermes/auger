# Verdict: AUG-062

**Task:** answer sentinel -1.0 leaks into user-facing output; unmeasured decisions read as confident
**Evaluated:** 2026-09-25T04:41:53.800868
**Result:** ✓ PASS

## Pipeline Stages

- ✓ **tier1**
  -   ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==
- ✓ **tier2**
  - COMPLETE
  ✓ After a default-confidence answer: dump never shows a bare '-1' for confidence; the answer echo does not print 'confidence -1.0'; feedback's thin list INCLUDES unmeasured decisions (not 'every recorded decision is at or above the threshold'); status min/mean/max ignore the sentinel; priority judgments treat unmeasured as low, not confident: All five sub-parts verified in auger.py and by tests. (1) dump: auger.py:4508 renders `conf {_conf_render(...)}` -> 'conf n/a'; test_confidence_sentinel_stays_in_the_store_and_never_renders asserts '-1' not in dump_out and '## D-001  (4.05)  conf n/a' present. (2) answer echo: auger.py:3959 `confidence {_conf_render(row['confidence'])}` -> 'confidence n/a'; same test asserts '-1' not in out and 'confidence n/a' in out. (3) feedback thin list: thin_decisions (auger.py:2170-2183) returns measured_thin + unmeasured via _is_unmeasured, and cmd_feedback prints _conf_render(conf) at auger.py:2597; the '(none — every recorded decision is at or above the threshold)' line only prints when both thin and drilled are empty (auger.py:2594-2595); test_confidence_sentinel_decisions_are_thin_not_confident asserts '  D-003  n/a  <- thinnest first' in out and '(none' not in out. (4) status aggregates: auger.py:4073 `confs = _conf_values(dec)` drops the sentinel (auger.py:83-90), and per-domain means at auger.py:4082 use _conf_values; test_confidence_sentinel_is_excluded_from_status_aggregates asserts 'confidence: min 0.50  mean 0.70  max 0.90' (sentinels excluded, not averaged in). (5) priority judgments: auger.py:2420 `if not _is_unmeasured(d) and d['confidence'] < T_PRIORITY`; test_confidence_sentinel_is_never_a_priority_judgment_below_the_floor asserts the measured D-002 escalates with its real 0.20 while no '-1'/D-003 escalation exists, and D-003 is still drilled. Helper unit check: _conf_render(-1.0)='n/a', _conf_values([-1.0,0.5,0.9,None])=[0.5,0.9], _is_unmeasured correct. Tests: `python3 -m pytest tests/test_auger.py -k confidence_sentinel -x -q` -> '5 passed, 172 deselected in 17.59s' (exit 0); full gate `bash tests/gate.sh` -> 'All checks passed!' (ruff), '177 passed in 524.11s (0:08:44)', smoke 'passed 21, failed 0', 'GATE PASS' (exit 0).
The -1.0 sentinel is confined to the store: every reader path (dump, answer echo, feedback thin list, status aggregates, priority judgments) routes through _conf_render/_conf_values/_is_unmeasured, and the full gate passes (177 passed, GATE PASS).

## Summary

Judge Result: AUG-062

Stage tier1: PASS
    ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==

Stage tier2: PASS
  COMPLETE
  ✓ After a default-confidence answer: dump never shows a bare '-1' for confidence; the answer echo does not print 'confidence -1.0'; feedback's thin list INCLUDES unmeasured decisions (not 'every recorded decision is at or above the threshold'); status min/mean/max ignore the sentinel; priority judgments treat unmeasured as low, not confident: All five sub-parts verified in auger.py and by tests. (1) dump: auger.py:4508 renders `conf {_conf_render(...)}` -> 'conf n/a'; test_confidence_sentinel_stays_in_the_store_and_never_renders asserts '-1' not in dump_out and '## D-001  (4.05)  conf n/a' present. (2) answer echo: auger.py:3959 `confidence {_conf_render(row['confidence'])}` -> 'confidence n/a'; same test asserts '-1' not in out and 'confidence n/a' in out. (3) feedback thin list: thin_decisions (auger.py:2170-2183) returns measured_thin + unmeasured via _is_unmeasured, and cmd_feedback prints _conf_render(conf) at auger.py:2597; the '(none — every recorded decision is at or above the threshold)' line only prints when both thin and drilled are empty (auger.py:2594-2595); test_confidence_sentinel_decisions_are_thin_not_confident asserts '  D-003  n/a  <- thinnest first' in out and '(none' not in out. (4) status aggregates: auger.py:4073 `confs = _conf_values(dec)` drops the sentinel (auger.py:83-90), and per-domain means at auger.py:4082 use _conf_values; test_confidence_sentinel_is_excluded_from_status_aggregates asserts 'confidence: min 0.50  mean 0.70  max 0.90' (sentinels excluded, not averaged in). (5) priority judgments: auger.py:2420 `if not _is_unmeasured(d) and d['confidence'] < T_PRIORITY`; test_confidence_sentinel_is_never_a_priority_judgment_below_the_floor asserts the measured D-002 escalates with its real 0.20 while no '-1'/D-003 escalation exists, and D-003 is still drilled. Helper unit check: _conf_render(-1.0)='n/a', _conf_values([-1.0,0.5,0.9,None])=[0.5,0.9], _is_unmeasured correct. Tests: `python3 -m pytest tests/test_auger.py -k confidence_sentinel -x -q` -> '5 passed, 172 deselected in 17.59s' (exit 0); full gate `bash tests/gate.sh` -> 'All checks passed!' (ruff), '177 passed in 524.11s (0:08:44)', smoke 'passed 21, failed 0', 'GATE PASS' (exit 0).
The -1.0 sentinel is confined to the store: every reader path (dump, answer echo, feedback thin list, status aggregates, priority judgments) routes through _conf_render/_conf_values/_is_unmeasured, and the full gate passes (177 passed, GATE PASS).

Overall: PASS ✓
