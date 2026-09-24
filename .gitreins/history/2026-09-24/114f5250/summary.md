# Verdict: AUG-049

**Task:** untracked .tmp_* scratch files red the Tier-1 lint arm and block a legitimate commit
**Evaluated:** 2026-09-24T12:54:30.068480
**Result:** ✓ PASS

## Pipeline Stages

- ✓ **tier1**
  -   ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==
- ✓ **tier2**
  - COMPLETE
  ✓ repo-root .tmp_* rule in .gitignore; a documented convention names where scratch files go; a lint run with a .tmp_*.py present no longer reds the gate: (1) .gitignore:4 contains `.tmp_*`; `git check-ignore -v .tmp_probe_eval.py` returned `.gitignore:4:.tmp_*` (exit=0). (2) README.md:193-200 adds a '## Scratch files' section stating scratch scripts go under `/tmp/`, never the workdir, with the gitignore rule as backstop. (3) Live proof with a probe file present: `ruff check .tmp_probe_eval.py` (probe with unused imports) -> 'F401 ... Found 2 errors', exit=1 (counterfactual: the file WOULD red lint if selected); `ruff check .` with probe present -> 'All checks passed!', exit=0; `ruff check --no-respect-gitignore .` -> 'Found 2 errors' (confirms the probe is the only excluded item); gate lint arm `ruff check auger.py` with probe present -> 'All checks passed!', exit=0; `git status --porcelain` did not list the probe (ignored). Probe removed after verification.
The .gitignore .tmp_* rule, the README scratch-file convention, and a live lint run with a .tmp_*.py present (gate stays green, exit=0) all verify the fix.

## Summary

Judge Result: AUG-049

Stage tier1: PASS
    ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==

Stage tier2: PASS
  COMPLETE
  ✓ repo-root .tmp_* rule in .gitignore; a documented convention names where scratch files go; a lint run with a .tmp_*.py present no longer reds the gate: (1) .gitignore:4 contains `.tmp_*`; `git check-ignore -v .tmp_probe_eval.py` returned `.gitignore:4:.tmp_*` (exit=0). (2) README.md:193-200 adds a '## Scratch files' section stating scratch scripts go under `/tmp/`, never the workdir, with the gitignore rule as backstop. (3) Live proof with a probe file present: `ruff check .tmp_probe_eval.py` (probe with unused imports) -> 'F401 ... Found 2 errors', exit=1 (counterfactual: the file WOULD red lint if selected); `ruff check .` with probe present -> 'All checks passed!', exit=0; `ruff check --no-respect-gitignore .` -> 'Found 2 errors' (confirms the probe is the only excluded item); gate lint arm `ruff check auger.py` with probe present -> 'All checks passed!', exit=0; `git status --porcelain` did not list the probe (ignored). Probe removed after verification.
The .gitignore .tmp_* rule, the README scratch-file convention, and a live lint run with a .tmp_*.py present (gate stays green, exit=0) all verify the fix.

Overall: PASS ✓
