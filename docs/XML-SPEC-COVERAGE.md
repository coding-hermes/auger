# XML spec graph — implementation coverage (R1–R16 / AC-1–AC-20)

Source of truth for what exists. The design authority is
[`docs/prd/xml-spec-graph.md`](prd/xml-spec-graph.md) (39 KB: requirements, acceptance
criteria, scope, boundaries, sequencing). This file answers the different question:
**which requirement is actually implemented, and which test proves it.**

Status vocabulary — no third meaning is allowed:

* **proven** — code exists on `main`, and the test the acceptance criterion names runs in
  `tests/gate.sh`'s pytest arm (so CI and the commit guard execute it).
* **partial** — code exists but a named clause of the acceptance criterion has no test.
* **designed only** — the PRD specifies it; no code path exists (grep for the verb/attribute
  returns nothing in `auger.py`).
* **gap** — the doc that claims the behaviour is behind the code.

Snapshot: `origin/main` @ `6c8f1cc`, 2026-11-03. Re-verify with the commands at the bottom.

## Requirements

| Req | Subject | Status | Evidence |
|---|---|---|---|
| R1 | XML is the authoritative spec source; MD/HTML are views | proven | `spec validate` / `spec list` / `spec assemble` on a corpus; `SPEC-003` |
| R2 | Stable, unique, never-renumbered IDs | proven | `test_schema_matches_the_validator`, `duplicate-id` refusal fixture |
| R3 | Typed kind / applicability / lifecycle / verification metadata | proven | same parity test; `invalid-status`, `invalid-version`, `invalid-applies-to`, `invalid-verification-method` fixtures |
| R4 | Cross-document reuse by stable ID; duplicate/dangling/cycle detection | proven | `test_shared_block_reference`, `test_refusals_are_typed_and_emit_no_document` (11 negative fixtures) |
| R5 | Targeted retrieval: select by ID/attribute, emit only selection + closure | proven | `spec assemble`; `test_selects_only_requested_blocks_and_their_dependencies`, `test_no_deps_emits_only_the_matched_block` |
| R6 | Deterministic, byte-identical, read-only rendering | proven | `test_selection_is_byte_identical_across_runs`, `test_selection_matches_the_golden` |
| R7 | Safe parsing: no external entities, no network, fail before partial output | proven | `external-entity-forbidden` fixture; no network surface by construction (repo-relative `src` only — `remote-reference-rejected`, `include-outside-root`) |
| R8 | DuckBrain stays authoritative for decisions; XML references records | designed only | no record-reference attribute in the schema or validator |
| R9 | Approval-bound board write via boardctl | designed only | `grep -c boardctl auger.py` → 0 |
| R10 | `ch:trace` graph checker | designed only | no checker code in `auger.py` |
| R11 | Read-only staged-diff checker with machine-readable findings | designed only | no checker code in `auger.py` |
| R12 | Opt-in GitReins spec-alignment guard | designed only | GitReins side not built (GR-154 open) |
| R13 | Semantic honesty (evidence + uncertainty, never auto-edit) | n/a until R11/R12 exist | — |
| R14 | Scoped task packet (in-scope + explicit out-of-scope with reasons) | designed only | no task-packet code |
| R15 | Structured verification/test obligations, incl. migration apply-test | **partial** | implemented by AUG-123 — see AC-17; the *rollback* clause is declared unsupported (no rollback attribute in the schema) |
| R16 | Boardctl-sourced GitReins judgment on the real tree | designed only | no packet writer, no judge intake code (GR-155 open) |

Summary: **7 proven, 1 partial, 7 designed only, 1 not applicable yet (R13 depends on R11/R12).**

## Acceptance criteria

| AC | Status | Proof / reason |
|---|---|---|
| AC-1 | proven | `tests/test_xml_specs.py::test_shared_block_reference` |
| AC-2 | proven | `test_refusals_are_typed_and_emit_no_document` (8 fixtures: duplicate-id, unresolved-reference, reference-cycle, include-cycle, external-entity, schema-version, invalid/missing verification method) |
| AC-3 | proven | `test_selects_only_requested_blocks_and_their_dependencies`, `test_selection_matches_the_golden` |
| AC-4 | proven | `test_selection_is_byte_identical_across_runs` |
| AC-5 | **partial** | `test_markdown_export_is_unchanged` proves the export golden; the "multi-view regeneration + source-only mutation" half has no test |
| AC-6 | designed only | no board export path exists to refuse |
| AC-7 | designed only | no board export path exists to read back |
| AC-8 | designed only | `tests/test_spec_alignment.py` does not exist |
| AC-9 | designed only | same file, missing |
| AC-10 | designed only | GitReins flag (GR-154) not built |
| AC-11 | designed only | same |
| AC-12 | designed only | same |
| AC-13 | designed only | no semantic-review code |
| AC-14 | designed only | `spec assemble` reports matched/dependency/total counts only — no input bytes, output bytes, or elapsed time |
| AC-15 | designed only | no `docs/prd/evidence/` end-to-end trace artifact |
| AC-16 | designed only | `tests/test_task_context.py` does not exist |
| AC-17 | proven | `test_assembles_migration_obligation_with_repo_harness_metadata`, `test_rejects_duplicate_dangling_stale_and_unregistered_test_refs`, `test_serialized_runner_is_argument_vector_not_shell_command`, `test_migration_obligation_applies_named_file_and_detects_noop` (4/4 clauses; rollback declared unsupported, which AC-17 permits) |
| AC-18 | designed only | packet export not implemented |
| AC-19 | designed only | trace gate before judge handoff not implemented |
| AC-20 | designed only | judge-context fixture not implemented |

Summary: **5 proven, 1 partial, 14 designed only.**

## Known documentation gaps

1. **The XSD does not cover the AUG-123 surface.** `specs/schema/auger-spec-1.0.xsd` declares
   `section`, `requirement`, `acceptance`, `verification` — but no `test` element, and none of the
   attributes the validator requires on one (`runner`, `selector`, `path`, `migration`, `fixture`,
   `assertions`, `type`). `auger.py` validates `<test>` nodes via `SPEC_BLOCK_TAGS`, a separate set
   from the authored `SPEC_BLOCK_KINDS`, so the parity test
   (`test_schema_matches_the_validator`) passes while the normative grammar is incomplete. An
   editor validating against the shipped XSD will flag every AUG-123 test obligation as invalid.
2. **`SPEC-003` is a slice record, not a status record.** Its header still says typed test
   obligations are "deliberately out of scope here", although AUG-123 landed; the coverage table in
   this file is the correcting surface.
3. **`docs/XML-SPEC-SCHEMA.md` (2.6 KB) is the only prose for the verification/test graph** and is
   not referenced from `SPEC-003`'s schema section.

## Re-verify

```bash
python3 auger.py spec validate tests/fixtures/xml/specs
python3 auger.py spec assemble tests/fixtures/xml/specs --kind requirement
python3 auger.py spec list tests/fixtures/xml/specs
python3 -m pytest tests/test_xml_specs.py tests/test_verification_graph.py -q
grep -n 'python3 -m pytest' tests/gate.sh   # both files must appear in the arm
grep -c boardctl auger.py                   # 0 == R9/R14/R16 still designed only
```

Machine-readable status is the boolean of each row above, not a narrative claim. Do not mark a
row proven because a PRD says so, or because a doc describes it: the cited test must exist and
must be inside the gate's pytest arm.
