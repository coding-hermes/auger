# Verdict: AUG-044

**Task:** Fix README/DESIGN false 'versioned in git' claim
**Evaluated:** 2026-10-06T10:35:57.140971
**Result:** ✓ PASS

## Pipeline Stages

- ✓ **tier1**
  -   ✓ lint: scanners: nice=nice -n 10
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: scanners: nice=nice -n 10
- ✓ **tier2**
  - COMPLETE
  ✓ README and DESIGN docs claim the spec is 'versioned in git' but duckbrain gitignores /namespaces/. Correct the docs to reflect reality: the spec is stored in duckbrain, not versioned in git. Remove or qualify the false claims in README.md and docs/DESIGN.md.: Commit 2843a55 removes the false claims. README.md:171-181 now reads 'declared tables over the JSONL files that namespace holds' (was 'git-backed JSONL') and 'the spec lives in the DuckBrain database (the namespace you name)' (was 'the spec is versioned in git'), plus 'DuckBrain's own repo gitignores `/namespaces/`, so these files are not tracked by git and carry no git history.' docs/DESIGN.md:104-110 changes the diagram label 'git-backed JSONL' -> 'namespace JSONL', drops 'versioned by git' from the storage sentence, and adds 'DuckBrain's repo gitignores `/namespaces/`, so these files are not tracked by git.' grep for 'versioned|git-backed|versioned in git' in README.md + docs/DESIGN.md returns only the corrected negative statements (README.md:181, DESIGN.md:110); no false claims remain. Remaining 'versioned in git' hits are in docs/dogfood/*.md, which are historical diagnostic reports documenting the bug as FALSE (diagnostics.md:196 'Versioned in git is currently false'), not claims. Reality verified: /home/kara/duckbrain/.gitignore:13 = '/namespaces/' and `git check-ignore -v namespaces/` -> '.gitignore:13:/namespaces/ namespaces/'. Test evidence: `bash tests/docs_check.sh` exit_code=0, output 'PASS: README claims match code — ... tally ok (14 tables), 5 env/path refs ok'.
Both README.md and docs/DESIGN.md were corrected to state the spec lives in the DuckBrain namespace (not versioned in git), matching the verified duckbrain .gitignore /namespaces/ rule, and tests/docs_check.sh passes.

## Summary

Judge Result: AUG-044

Stage tier1: PASS
    ✓ lint: scanners: nice=nice -n 10
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: scanners: nice=nice -n 10

Stage tier2: PASS
  COMPLETE
  ✓ README and DESIGN docs claim the spec is 'versioned in git' but duckbrain gitignores /namespaces/. Correct the docs to reflect reality: the spec is stored in duckbrain, not versioned in git. Remove or qualify the false claims in README.md and docs/DESIGN.md.: Commit 2843a55 removes the false claims. README.md:171-181 now reads 'declared tables over the JSONL files that namespace holds' (was 'git-backed JSONL') and 'the spec lives in the DuckBrain database (the namespace you name)' (was 'the spec is versioned in git'), plus 'DuckBrain's own repo gitignores `/namespaces/`, so these files are not tracked by git and carry no git history.' docs/DESIGN.md:104-110 changes the diagram label 'git-backed JSONL' -> 'namespace JSONL', drops 'versioned by git' from the storage sentence, and adds 'DuckBrain's repo gitignores `/namespaces/`, so these files are not tracked by git.' grep for 'versioned|git-backed|versioned in git' in README.md + docs/DESIGN.md returns only the corrected negative statements (README.md:181, DESIGN.md:110); no false claims remain. Remaining 'versioned in git' hits are in docs/dogfood/*.md, which are historical diagnostic reports documenting the bug as FALSE (diagnostics.md:196 'Versioned in git is currently false'), not claims. Reality verified: /home/kara/duckbrain/.gitignore:13 = '/namespaces/' and `git check-ignore -v namespaces/` -> '.gitignore:13:/namespaces/ namespaces/'. Test evidence: `bash tests/docs_check.sh` exit_code=0, output 'PASS: README claims match code — ... tally ok (14 tables), 5 env/path refs ok'.
Both README.md and docs/DESIGN.md were corrected to state the spec lives in the DuckBrain namespace (not versioned in git), matching the verified duckbrain .gitignore /namespaces/ rule, and tests/docs_check.sh passes.

Overall: PASS ✓
