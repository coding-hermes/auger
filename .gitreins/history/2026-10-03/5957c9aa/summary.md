# Verdict: AUG-090

**Task:** Document DUCKBRAIN_CONFIG_PATH substrate isolation escape hatch
**Evaluated:** 2026-10-03T01:41:54.365557
**Result:** ✓ PASS

## Pipeline Stages

- ✓ **tier1**
  -   ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==
- ✓ **tier2**
  - COMPLETE
  ✓ README Substrate pin documents that DUCKBRAIN_DATA_DIR+DUCKBRAIN_NAMESPACES_PATH alone do not isolate a second substrate's namespace list from a co-located production DuckBrain, and gives the DUCKBRAIN_CONFIG_PATH workaround; README known-gaps bullet points to it: README.md:53-59 (## Substrate pin) states 'On a box that already runs a production DuckBrain, data-dir env alone does not isolate a second instance... pointing DUCKBRAIN_DATA_DIR and DUCKBRAIN_NAMESPACES_PATH at a scratch dir still lists the co-located production namespaces (review-lane finding AUG-090)'. README.md:61-71 gives the workaround: a scratch config.json with empty namespaceMappings + namespacesPath, launched with DUCKBRAIN_CONFIG_PATH=~/dd-scratch/config.json, plus the post-boot /api/namespaces isolation check. README.md:256-260 known-gaps bullet ('A second substrate on a shared host is not isolated by data-dir env alone') explicitly points to the *Substrate pin* section for the DUCKBRAIN_CONFIG_PATH escape hatch. Technical accuracy confirmed against real DuckBrain source: /home/kara/duckbrain/src/config/index.ts:293-310 (GAP-022 — DUCKBRAIN_CONFIG_PATH is an env-only runtime override, read at boot, never persisted) and src/namespaces/census.ts (census = namespaceMappings union on-disk scan). Docs-only change: commit aa7e1cf modifies README.md only (23 insertions, 1 deletion); no test surface applies.
README Substrate pin documents the data-dir-env isolation gap and the DUCKBRAIN_CONFIG_PATH workaround, and the known-gaps bullet points to it — all claims verified against the real DuckBrain source.

## Summary

Judge Result: AUG-090

Stage tier1: PASS
    ✓ lint: ok (no output)
  ✓ secrets: secrets: harness state excluded from gitleaks scope (.gitreins/**)
  ✓ tests: == syntax ==

Stage tier2: PASS
  COMPLETE
  ✓ README Substrate pin documents that DUCKBRAIN_DATA_DIR+DUCKBRAIN_NAMESPACES_PATH alone do not isolate a second substrate's namespace list from a co-located production DuckBrain, and gives the DUCKBRAIN_CONFIG_PATH workaround; README known-gaps bullet points to it: README.md:53-59 (## Substrate pin) states 'On a box that already runs a production DuckBrain, data-dir env alone does not isolate a second instance... pointing DUCKBRAIN_DATA_DIR and DUCKBRAIN_NAMESPACES_PATH at a scratch dir still lists the co-located production namespaces (review-lane finding AUG-090)'. README.md:61-71 gives the workaround: a scratch config.json with empty namespaceMappings + namespacesPath, launched with DUCKBRAIN_CONFIG_PATH=~/dd-scratch/config.json, plus the post-boot /api/namespaces isolation check. README.md:256-260 known-gaps bullet ('A second substrate on a shared host is not isolated by data-dir env alone') explicitly points to the *Substrate pin* section for the DUCKBRAIN_CONFIG_PATH escape hatch. Technical accuracy confirmed against real DuckBrain source: /home/kara/duckbrain/src/config/index.ts:293-310 (GAP-022 — DUCKBRAIN_CONFIG_PATH is an env-only runtime override, read at boot, never persisted) and src/namespaces/census.ts (census = namespaceMappings union on-disk scan). Docs-only change: commit aa7e1cf modifies README.md only (23 insertions, 1 deletion); no test surface applies.
README Substrate pin documents the data-dir-env isolation gap and the DUCKBRAIN_CONFIG_PATH workaround, and the known-gaps bullet points to it — all claims verified against the real DuckBrain source.

Overall: PASS ✓
