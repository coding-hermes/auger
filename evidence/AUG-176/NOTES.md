# AUG-176 — what is done, what remains, and why

Worker: wt/AUG-176 @ (see git log). Branch committed stage-by-stage; the FINAL commit
carries the full-gate transcript in evidence/AUG-176/07-gate.log.

## Done

- AC1 (conversion no loss): all three corpora convert with section_count == block_count
  and zero skipped sections; the manifests carry the skipped list with a required reason
  (enforced by validate_manifest and tests/test_spec_ingest.py). Coverage report:
  docs/qa/aug176-conversion-coverage.md (counts read back from the manifests).
- AC2 (target set bounded and named): code-factory 7/7 files; auger residual prose 14
  docs/*.md files (SPEC-CORPUS-VIEW.md excluded, generated view, named reason);
  coding-hermes-director 22 structured files (DESIGN.md, 5 contracts md, 1 sections md,
  14 build-spec md, 2 review PLANs); exclusions (audit/, tickets/, handbook/,
  review packaging, non-markdown contracts) each carry a named reason in the manifests.
- AC5 (verbatim): block text is the source prose verbatim — whitespace-normalized
  containment checked for EVERY block of every corpus by tests/test_spec_ingest.py
  (shipped-corpora test) and 10 hand-picked blocks in evidence/AUG-176/05 (10/10 YES).
  The one non-whitespace emit is `ch:trace` -> `ch&#58;trace` (XML character reference):
  parses back to identical text, keeps the align scanner from resolving a quoted marker
  against this repo; pinned by test_no_raw_trace_marker_in_emitted_documents.
- AC4 (registry + linkage): corpora validate under the SAME loader/grammar
  SPEC-GRAPH-CORPUS describes (auger spec validate + spec check rc 0 per root, gate arm
  `converted corpora (AUG-176)`); provenance is per block (source comment `source:
  <path>#<anchor>` + manifest block map), so a citation round-trips. Interpretation note:
  the row's "register in SPEC-GRAPH-CORPUS" is honoured as "validated into the registry
  the SPEC-GRAPH-CORPUS requirement defines" — specs/ block content is byte-stable per
  design constraint 2, so no new blocks were added to specs/.
- AC3 (embedding recall): registration complete — every active block embedded into the
  live substrate under /auger/AUG176-<project>/<BLOCK-ID> (oversized blocks as -P<nn>
  PART rows; three single-line 182k-340k-char generated tables needed that). The battery
  runner (scripts/recall_battery.py, 36 queries with named targets) is committed and unit
  tested. RUN STATUS: see the tail of 06-recall-battery.md — the substrate's
  semantic-search path (every q= query, any prefix, any namespace) hung >300s while
  prefix-only reads answered instantly; a bounded probe loop watched for recovery and the
  battery was re-run when it cleared (or the residual is named below).
- AC6 (hygiene): commits use the fleet identity + co-author trailer; intermediate stage
  commits disclose the hook bypass and cite the focused-arm evidence; the final commit is
  backed by a real full-gate run. No board files, no .gitreins/history writes, shared
  checkout untouched.

## Residuals / next step

- (filled at close) recall battery outcome.
- 36 stale memory rows under /auger/AUG176-auger-docs/AD-SPEC-CORPUS-VIEW-* (the excluded
  generated view's blocks, registered before the exclusion-matching fix) remain in the
  live namespace: DuckBrain exposes no DELETE route for memory rows (DELETE
  /api/memories/<id|key> answered NOT_FOUND/ROUTE_NOT_FOUND — probed twice). They only
  add recall candidates for auger-docs queries; the owner can prune them from the
  substrate if wanted.
